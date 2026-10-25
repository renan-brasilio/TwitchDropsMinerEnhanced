from __future__ import annotations

import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from aiohttp import web, WSMsgType
from constants import State, PriorityMode, WORKING_DIR, CATALOG_PAGE_MAX, CATALOG_LOOKUP_MAX

if TYPE_CHECKING:
    from twitch import Twitch
    from state_hub import StateHub

logger = logging.getLogger("TwitchDrops.api")
WEB_DIR = Path(WORKING_DIR, "web")
API_KEY = os.environ.get("API_KEY", "").strip()


@web.middleware
async def auth_middleware(request: web.Request, handler):
    api_key = os.environ.get("API_KEY", "").strip()
    if not api_key:
        return await handler(request)
    # Allow static dashboard and options preflight
    if request.path in ("/", "/index.html", "/favicon.ico") or request.path.startswith("/assets") or request.method == "OPTIONS":
        return await handler(request)

    auth_header = request.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    elif "X-API-Key" in request.headers:
        token = request.headers["X-API-Key"].strip()
    elif "key" in request.query:
        token = request.query["key"].strip()

    if token != api_key:
        return web.json_response({"error": "Unauthorized", "message": "Invalid or missing API key"}, status=401)

    return await handler(request)


class APIServer:
    def __init__(self, twitch: Twitch, hub: StateHub, host: str | None = None, port: int = 23432) -> None:
        self._twitch = twitch
        self._hub = hub
        self._host = host or os.environ.get("HOST", "127.0.0.1")
        self._port = port
        self._app = web.Application(middlewares=[auth_middleware, self._account_middleware])
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._ws_clients: set[web.WebSocketResponse] = set()
        self._setup_routes()

    def _cors_headers(self) -> dict[str, str]:
        return {
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
        }

    def _json_response(self, data: Any, status: int = 200) -> web.Response:
        return web.json_response(data, status=status, headers=self._cors_headers())

    @web.middleware
    async def _account_middleware(self, request: web.Request, handler):
        account_control = request.path.startswith("/api/control/") or request.path == "/api/inventory/refresh"
        if not account_control:
            return await handler(request)
        if not self._twitch.account_available:
            return self._json_response({
                "error": "Connect a Twitch account before using account controls"
            }, status=409)
        try:
            async with self._twitch.account_operation():
                return await handler(request)
        except asyncio.CancelledError:
            if self._twitch.account_available:
                raise
            return self._json_response({"error": "Account changed; operation cancelled"}, status=409)

    def _setup_routes(self) -> None:
        # CORS preflight
        self._app.router.add_route("OPTIONS", "/{tail:.*}", self._options_handler)

        # REST API
        self._app.router.add_get("/api/status", self._get_status)
        self._app.router.add_get("/api/channels", self._get_channels)
        self._app.router.add_get("/api/inventory", self._get_inventory)
        self._app.router.add_post("/api/inventory/refresh", self._post_inventory_refresh)
        self._app.router.add_post("/api/campaigns/ignore", self._post_campaign_ignore)
        self._app.router.add_post("/api/campaigns/focus", self._post_campaign_focus)
        self._app.router.add_post("/api/campaigns/archive", self._post_campaign_archive)
        self._app.router.add_get("/api/games", self._get_games)
        self._app.router.add_get("/api/games/catalog", self._get_games_catalog)
        self._app.router.add_get("/api/games/lookup", self._get_games_lookup)
        self._app.router.add_get("/api/settings", self._get_settings)
        self._app.router.add_post("/api/settings", self._post_settings)
        self._app.router.add_post("/api/auth/token", self._post_auth_token)
        self._app.router.add_post("/api/auth/connect", self._post_auth_connect)
        self._app.router.add_post("/api/auth/disconnect", self._post_auth_disconnect)
        self._app.router.add_get("/api/logs", self._get_logs)

        # Controls
        self._app.router.add_post("/api/control/pause", self._control_pause)
        self._app.router.add_post("/api/control/resume", self._control_resume)
        self._app.router.add_post("/api/control/reload", self._control_reload)
        self._app.router.add_post("/api/control/switch", self._control_switch)
        self._app.router.add_post("/api/control/claim", self._control_claim)
        self._app.router.add_post("/api/control/watch", self._control_watch)

        # WebSocket
        self._app.router.add_get("/ws", self._ws_handler)

        # Static Dashboard Files
        if WEB_DIR.exists():
            if (WEB_DIR / "assets").exists():
                self._app.router.add_static("/assets", str(WEB_DIR / "assets"), show_index=False)
            self._app.router.add_get("/", self._serve_index)
            self._app.router.add_get("/index.html", self._serve_index)

    async def _options_handler(self, request: web.Request) -> web.Response:
        return web.Response(status=204, headers=self._cors_headers())

    async def _serve_index(self, request: web.Request) -> web.StreamResponse:
        index_file = WEB_DIR / "index.html"
        if index_file.exists():
            headers = self._cors_headers()
            headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            headers["Pragma"] = "no-cache"
            headers["Expires"] = "0"
            return web.FileResponse(index_file, headers=headers)
        return web.Response(text="Web Dashboard assets loading...", content_type="text/plain")

    async def _get_status(self, request: web.Request) -> web.Response:
        status_data = self._hub.get_full_status()
        status_data["settings"] = {
            "priority": list(self._twitch.settings.priority),
            "priority_mode": self._twitch.settings.priority_mode.name,
            "has_auth_token": bool(getattr(self._twitch.settings, "auth_token", False)),
            "auto_claim": self._twitch.settings.auto_claim,
        }
        return self._json_response(status_data)

    async def _get_channels(self, request: web.Request) -> web.Response:
        return self._json_response(list(self._hub.channels_map.values()))

    async def _get_inventory(self, request: web.Request) -> web.Response:
        return self._json_response(self._hub.get_inventory())

    def _campaign_request_id(self, data: Any) -> str | None:
        if isinstance(data, dict) and isinstance(data.get("campaign_id"), str):
            campaign_id = data["campaign_id"].strip()
            if 0 < len(campaign_id) <= 200:
                return campaign_id
        return None

    def _publish_campaign_choice(self) -> None:
        self._twitch.settings.save(force=True)
        self._hub.broadcast_event("inventory", self._hub.get_inventory())
        # re-rank games: an ignored campaign stops being mined, a started one goes first
        self._twitch.change_state(State.GAMES_UPDATE)

    async def _post_campaign_ignore(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return self._json_response({"error": "Invalid JSON"}, status=400)
        campaign_id = self._campaign_request_id(data)
        if campaign_id is None or not isinstance(data.get("ignored"), bool):
            return self._json_response({"error": "campaign_id (string) and ignored (boolean) are required"}, status=400)
        settings = self._twitch.settings
        ignored = set(settings.ignored_campaigns)
        if data["ignored"]:
            ignored.add(campaign_id)
            if settings.focused_campaign == campaign_id:
                settings.focused_campaign = ""
        else:
            ignored.discard(campaign_id)
        settings.ignored_campaigns = ignored
        self._publish_campaign_choice()
        return self._json_response({"status": "success", "ignored": sorted(ignored)})

    async def _post_campaign_archive(self, request: web.Request) -> web.Response:
        """
        Prune ignored campaigns that have expired or finished from settings.ignored_campaigns.
        Keeps active/upcoming ignored campaigns untouched.
        """
        settings = self._twitch.settings
        ignored = set(settings.ignored_campaigns)
        if not ignored:
            return self._json_response({"status": "success", "archived_count": 0, "ignored": []})

        # Identify past campaigns (expired or finished)
        past_ids = set()
        for cid in ignored:
            campaign = self._twitch._campaigns.get(cid) or next((c for c in self._twitch.inventory if c.id == cid), None)
            if campaign is not None:
                if campaign.expired or campaign.finished:
                    past_ids.add(cid)
            else:
                past_ids.add(cid)

        if past_ids:
            ignored.difference_update(past_ids)
            settings.ignored_campaigns = ignored
            self._publish_campaign_choice()

        return self._json_response({
            "status": "success",
            "archived_count": len(past_ids),
            "ignored": sorted(ignored),
            "message": f"Archived {len(past_ids)} past ignored campaign(s)"
        })

    async def _post_campaign_focus(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return self._json_response({"error": "Invalid JSON"}, status=400)
        settings = self._twitch.settings
        if isinstance(data, dict) and data.get("campaign_id") is None:
            settings.focused_campaign = ""  # back to the normal priority order
            self._publish_campaign_choice()
            return self._json_response({"status": "success", "focused": None})
        campaign_id = self._campaign_request_id(data)
        if campaign_id is None:
            return self._json_response({"error": "campaign_id must be a string or null"}, status=400)
        campaign = next((c for c in self._twitch.inventory if c.id == campaign_id), None)
        if campaign is None:
            return self._json_response({"error": "Campaign is no longer available"}, status=404)
        if campaign.ignored:
            return self._json_response({"error": "Restore this campaign before mining it"}, status=409)
        if not campaign.active:
            return self._json_response({"error": "This campaign hasn't started yet"}, status=409)
        if campaign.finished or campaign.remaining_minutes <= 0:
            return self._json_response({"error": "Nothing left to earn in this campaign"}, status=409)
        self._twitch._paused = False
        settings.focused_campaign = campaign_id
        self._publish_campaign_choice()
        return self._json_response({"status": "success", "focused": campaign_id,
                                    "message": f"Mining {campaign.game.name}: {campaign.name} first"})

    async def _post_inventory_refresh(self, request: web.Request) -> web.Response:
        self._hub.add_log("INFO", "Refreshing inventory and drop campaigns from Twitch...")
        try:
            await self._twitch.fetch_inventory()
            campaigns = self._hub.get_inventory()
            return self._json_response({
                "status": "success",
                "campaigns": campaigns,
                "count": len(campaigns)
            })
        except Exception as e:
            logger.error(f"Failed to refresh inventory: {e}")
            return self._json_response({
                "status": "error",
                "message": str(e),
                "campaigns": self._hub.get_inventory()
            }, status=500)

    async def _get_games(self, request: web.Request) -> web.Response:
        """Games with drop campaigns on this account; the full catalog is /api/games/catalog."""
        games_dict: dict[str, dict[str, Any]] = {}
        for g in self._hub.available_games:
            g_name = g.get("name")
            if g_name:
                games_dict[g_name] = {"name": g_name, "id": g.get("id"), "discovered": True}

        campaign_counts: dict[str, int] = {}
        box_art: dict[str, str] = {}
        for camp in self._hub.counted_inventory():
            c_game = camp.get("game")
            if c_game:
                games_dict[c_game] = {"name": c_game, "discovered": True}
                campaign_counts[c_game] = campaign_counts.get(c_game, 0) + 1
                if camp.get("box_art"):
                    box_art.setdefault(c_game, camp["box_art"])

        for g in games_dict.values():
            g["campaigns_count"] = campaign_counts.get(g["name"], 0)
            g["box_art"] = box_art.get(g["name"], "")

        sorted_games = sorted(
            list(games_dict.values()),
            key=lambda x: (-x.get("campaigns_count", 0), not x.get("discovered", False), x["name"])
        )
        return self._json_response({
            "available": sorted_games,
            "campaign_counts": campaign_counts,
            "priority": list(self._twitch.settings.priority),
            "exclude": list(self._twitch.settings.exclude),
        })

    async def _catalog_call(self, coro) -> web.Response | Any:
        """Runs a Twitch catalog query; maps a missing account / Twitch failure to JSON errors."""
        if not self._twitch.account_available:
            coro.close()
            return self._json_response({"error": "Connect a Twitch account to browse games"}, status=409)
        try:
            return await coro
        except asyncio.CancelledError:
            if self._twitch.account_available:
                raise
            return self._json_response({"error": "Account changed; search cancelled"}, status=409)
        except Exception as exc:
            logger.warning(f"Twitch game catalog request failed: {exc}")
            return self._json_response({"error": "Twitch game catalog is unavailable right now"}, status=502)

    def _campaign_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for camp in self._hub.counted_inventory():
            if camp.get("game"):
                key = camp["game"].casefold()
                counts[key] = counts.get(key, 0) + 1
        return counts

    async def _get_games_catalog(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "").strip()
        after = request.query.get("after") or None
        try:
            first = int(request.query.get("first", "40"))
        except ValueError:
            return self._json_response({"error": "first must be an integer"}, status=400)
        if not 1 <= first <= CATALOG_PAGE_MAX:
            return self._json_response({"error": f"first must be between 1 and {CATALOG_PAGE_MAX}"}, status=400)
        if len(query) > 100 or (after is not None and len(after) > 200):
            return self._json_response({"error": "Search text is too long"}, status=400)
        if query and after:
            return self._json_response({"error": "Search results have no further pages"}, status=400)
        result = await self._catalog_call(self._twitch.fetch_game_catalog(query=query, after=after, first=first))
        if isinstance(result, web.Response):
            return result
        games, next_cursor = result
        counts = self._campaign_counts()
        for game in games:
            game["campaigns_count"] = counts.get(game["name"].casefold(), 0)
        return self._json_response({"games": games, "next": next_cursor})

    async def _get_games_lookup(self, request: web.Request) -> web.Response:
        names = list(dict.fromkeys(n.strip() for n in request.query.getall("name", []) if n.strip()))
        if not names:
            return self._json_response({"games": {}})
        if len(names) > CATALOG_LOOKUP_MAX or any(len(n) > 100 for n in names):
            return self._json_response(
                {"error": f"Look up at most {CATALOG_LOOKUP_MAX} names of up to 100 characters"}, status=400)
        result = await self._catalog_call(self._twitch.lookup_games(names))
        if isinstance(result, web.Response):
            return result
        return self._json_response({"games": result})

    async def _get_settings(self, request: web.Request) -> web.Response:
        s = self._twitch.settings
        return self._json_response({
            "priority": list(s.priority),
            "exclude": list(s.exclude),
            "priority_mode": s.priority_mode.value,
            "priority_mode_name": s.priority_mode.name,
            "tray_notifications": s.tray_notifications,
            "autostart_tray": s.autostart_tray,
            "available_drops_check": s.available_drops_check,
            "has_token": bool(getattr(s, "auth_token", False)),
            "auto_claim": s.auto_claim,
            "auto_select_linked": s.auto_select_linked,
        })

    async def _post_settings(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return self._json_response({"error": "Invalid JSON"}, status=400)

        if not isinstance(data, dict):
            return self._json_response({"error": "Settings must be a JSON object"}, status=400)

        # Validate the whole body before applying anything, so a bad field can't half-apply.
        updates: dict[str, Any] = {}
        for key in ("priority", "exclude"):
            if key in data:
                value = data[key]
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    return self._json_response({"error": f"{key} must be a list of strings"}, status=400)
                updates[key] = list(value) if key == "priority" else set(value)
        if "priority_mode" in data:
            mode = data["priority_mode"]
            # bool is an int subclass; reject it alongside floats and unknown values.
            if type(mode) is not int or mode not in PriorityMode._value2member_map_:
                return self._json_response({"error": "priority_mode is invalid"}, status=400)
            updates["priority_mode"] = PriorityMode(mode)
        for key in ("tray_notifications", "auto_claim", "auto_select_linked"):
            if key in data:
                if not isinstance(data[key], bool):
                    return self._json_response({"error": f"{key} must be a boolean"}, status=400)
                updates[key] = data[key]

        s = self._twitch.settings
        previous = {name.casefold() for name in s.priority}
        added_game = any(name.casefold() not in previous for name in updates.get("priority", []))
        if updates.get("auto_select_linked") and not s.auto_select_linked:
            # Turning it on applies to every linked campaign again, including games removed earlier.
            updates["auto_selected_campaigns"] = set()
        for key, value in updates.items():
            setattr(s, key, value)
        s.save(force=True)
        self._hub.broadcast_settings()
        # Discovery searches live streams of priority games, so a newly chosen game needs a
        # campaign refresh; other changes only re-rank the games already known.
        self._twitch.change_state(State.INVENTORY_FETCH if added_game else State.GAMES_UPDATE)
        return self._json_response({"status": "success", "message": "Settings updated"})

    async def _post_auth_token(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return self._json_response({"error": "Invalid JSON"}, status=400)

        if not isinstance(data, dict) or not isinstance(data.get("token"), str):
            return self._json_response({"error": "Token is required"}, status=400)
        token = data["token"].strip()
        if not token:
            return self._json_response({"error": "Token is required"}, status=400)

        try:
            await self._twitch.connect(token)
        except Exception:
            logger.exception("Unable to replace the Twitch account")
            return self._json_response({"error": "Unable to reconnect the Twitch account"}, status=500)
        return self._json_response({"status": "success", "message": "Token saved. Connecting..."})

    async def _post_auth_connect(self, request: web.Request) -> web.Response:
        try:
            await self._twitch.connect()
        except Exception:
            logger.exception("Unable to connect the Twitch account")
            return self._json_response({"error": "Unable to connect the Twitch account"}, status=500)
        return self._json_response({"status": "success", "message": "Twitch login started"})

    async def _post_auth_disconnect(self, request: web.Request) -> web.Response:
        try:
            await self._twitch.disconnect()
        except Exception:
            logger.exception("Unable to disconnect the Twitch account")
            return self._json_response({"error": "Unable to disconnect the Twitch account"}, status=500)
        return self._json_response({"status": "success", "message": "Disconnected from Twitch"})

    async def _get_logs(self, request: web.Request) -> web.Response:
        return self._json_response(list(self._hub.logs))

    async def _control_pause(self, request: web.Request) -> web.Response:
        self._twitch._paused = True
        self._twitch.stop_watching()
        self._twitch.change_state(State.IDLE)
        self._hub.set_status_text("Paused by user")
        self._hub.set_tray_icon("idle")
        return self._json_response({"status": "success", "state": "IDLE"})

    async def _control_resume(self, request: web.Request) -> web.Response:
        self._twitch._paused = False
        self._hub.set_status_text("Resuming mining...")
        self._twitch.change_state(State.INVENTORY_FETCH)
        return self._json_response({"status": "success", "state": "INVENTORY_FETCH"})

    async def _control_reload(self, request: web.Request) -> web.Response:
        self._hub.set_status_text("Reloading campaigns...")
        self._twitch.change_state(State.INVENTORY_FETCH)
        return self._json_response({"status": "success", "state": "INVENTORY_FETCH"})

    async def _control_switch(self, request: web.Request) -> web.Response:
        self._twitch._paused = False
        self._hub.set_status_text("Switching channel...")
        self._twitch.change_state(State.CHANNEL_SWITCH)
        return self._json_response({"status": "success", "state": "CHANNEL_SWITCH"})

    async def _control_watch(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except (ValueError, TypeError):
            return self._json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(data, dict) or type(data.get("channel_id")) is not int:
            return self._json_response({"error": "channel_id must be an integer"}, status=400)
        channel = self._twitch.channels.get(data["channel_id"])
        if channel is None:
            return self._json_response({"error": "Channel is no longer tracked"}, status=404)
        if not self._twitch.can_watch(channel):
            return self._json_response({"error": "This channel cannot earn your active drops"}, status=409)
        self._twitch._paused = False
        self._hub.channels.select(channel)
        self._twitch.watch(channel)
        self._twitch.restart_watching()
        self._twitch.change_state(State.CHANNEL_SWITCH)
        return self._json_response({"status": "success", "channel_id": channel.id, "message": f"Watching: {channel.name}"})

    async def _control_claim(self, request: web.Request) -> web.Response:
        claimed_count = 0
        claimed_names: list[str] = []
        try:
            await self._twitch.fetch_inventory()
        except Exception as e:
            logger.warning(f"Failed to refresh inventory before claim: {e}")

        for campaign in self._twitch.inventory:
            for drop in campaign.drops:
                if not drop.is_claimed:
                    if getattr(drop, "claim_id", None) is None and drop.current_minutes >= drop.required_minutes:
                        try:
                            await drop.generate_claim()
                        except Exception as e:
                            logger.debug(f"Could not generate claim ID for drop {drop.id}: {e}")
                    if drop.can_claim:
                        success = await drop.claim()
                        if success:
                            claimed_count += 1
                            claimed_names.append(drop.name)

        if claimed_count > 0:
            msg = f"Successfully claimed {claimed_count} drop(s): {', '.join(claimed_names)}"
        else:
            msg = "No drops currently ready to claim (in progress or already claimed)"

        self._hub.add_log("INFO", f"Claim check complete: {msg}")
        return self._json_response({
            "status": "success",
            "claimed": claimed_count,
            "drops": claimed_names,
            "message": msg,
        })

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=15.0)
        await ws.prepare(request)
        self._ws_clients.add(ws)
        q = self._hub.subscribe()

        # Send initial snapshot immediately
        init_data = self._hub.get_full_status()
        init_data["channels"] = list(self._hub.channels_map.values())
        init_data["inventory"] = self._hub.get_inventory()
        init_data["settings"] = {
            "priority": list(self._twitch.settings.priority),
            "priority_mode": self._twitch.settings.priority_mode.name,
            "auto_claim": self._twitch.settings.auto_claim,
        }
        await ws.send_json({"event": "init", "data": init_data})

        consumer_task = asyncio.create_task(self._ws_consumer(ws, q))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        payload = json.loads(msg.data)
                        action = payload.get("action")
                        if action == "pause":
                            await self._control_pause(request)
                        elif action == "resume":
                            await self._control_resume(request)
                        elif action == "reload":
                            await self._control_reload(request)
                        elif action == "switch":
                            await self._control_switch(request)
                    except Exception:
                        pass
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
        finally:
            consumer_task.cancel()
            self._hub.unsubscribe(q)
            self._ws_clients.discard(ws)

        return ws

    async def _ws_consumer(self, ws: web.WebSocketResponse, q: asyncio.Queue) -> None:
        try:
            while not ws.closed:
                event = await q.get()
                if not ws.closed:
                    await ws.send_json(event)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        
        # Try configured port, or fallback up to 10 ports
        actual_port = self._port
        for offset in range(10):
            try:
                candidate_port = self._port + offset
                self._site = web.TCPSite(self._runner, self._host, candidate_port)
                await self._site.start()
                actual_port = candidate_port
                break
            except OSError:
                continue
        print(f"==> API Server & Web Dashboard running at http://{self._host}:{actual_port}/", flush=True)
        logger.info(f"API Server & Web Dashboard running at http://{self._host}:{actual_port}/")
        self._hub.add_log("INFO", f"Dashboard & Local API online: http://{self._host}:{actual_port}/")
    async def stop(self) -> None:
        for ws in list(self._ws_clients):
            try:
                await ws.close()
            except Exception:
                pass
        if self._runner is not None:
            await self._runner.cleanup()
