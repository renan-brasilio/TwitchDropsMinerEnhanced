from __future__ import annotations

import os
import json
import asyncio
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

from aiohttp import web, WSMsgType
from constants import State, PriorityMode, WORKING_DIR

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
        self._app.router.add_get("/api/games", self._get_games)
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
        popular = [
            "Overwatch", "Rust", "Rainbow Six Siege", "Dead by Daylight",
            "Valorant", "World of Warcraft", "Escape from Tarkov", "Apex Legends",
            "Warframe", "Palworld", "Path of Exile", "Path of Exile 2",
            "Hunt: Showdown", "Sea of Thieves", "Rocket League", "Counter-Strike 2",
            "Cyberpunk 2077", "Destiny 2", "The Division 2", "Warhammer 40,000: Space Marine 2",
            "Dune: Awakening", "Delta Force", "Marvel Rivals"
        ]
        games_dict: dict[str, dict[str, Any]] = {}
        for p in popular:
            games_dict[p] = {"name": p, "discovered": False}

        for g in self._hub.available_games:
            g_name = g.get("name")
            if g_name:
                games_dict[g_name] = {"name": g_name, "id": g.get("id"), "discovered": True}

        campaign_counts: dict[str, int] = {}
        for camp in self._hub.get_inventory():
            c_game = camp.get("game")
            if c_game:
                games_dict[c_game] = {"name": c_game, "discovered": True}
                campaign_counts[c_game] = campaign_counts.get(c_game, 0) + 1

        for g in games_dict.values():
            g["campaigns_count"] = campaign_counts.get(g["name"], 0)

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
        })

    async def _post_settings(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return self._json_response({"error": "Invalid JSON"}, status=400)

        s = self._twitch.settings
        if "priority" in data and isinstance(data["priority"], list):
            s.priority = data["priority"]
        if "exclude" in data and isinstance(data["exclude"], list):
            s.exclude = set(data["exclude"])
        if "priority_mode" in data and isinstance(data["priority_mode"], int):
            try:
                s.priority_mode = PriorityMode(data["priority_mode"])
            except Exception:
                pass
        if "tray_notifications" in data:
            s.tray_notifications = bool(data["tray_notifications"])
        if "auto_claim" in data:
            s.auto_claim = bool(data["auto_claim"])
        s.save(force=True)
        self._hub.broadcast_event("settings", {
            "priority": list(s.priority),
            "exclude": list(s.exclude),
            "priority_mode": s.priority_mode.value,
            "auto_claim": s.auto_claim,
        })
        # Trigger reload of games
        self._twitch.change_state(State.GAMES_UPDATE)
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
        self._twitch.stop_watching()
        self._twitch.change_state(State.IDLE)
        self._hub.set_status_text("Paused by user")
        self._hub.set_tray_icon("idle")
        return self._json_response({"status": "success", "state": "IDLE"})

    async def _control_resume(self, request: web.Request) -> web.Response:
        self._hub.set_status_text("Resuming mining...")
        self._twitch.change_state(State.INVENTORY_FETCH)
        return self._json_response({"status": "success", "state": "INVENTORY_FETCH"})

    async def _control_reload(self, request: web.Request) -> web.Response:
        self._hub.set_status_text("Reloading campaigns...")
        self._twitch.change_state(State.INVENTORY_FETCH)
        return self._json_response({"status": "success", "state": "INVENTORY_FETCH"})

    async def _control_switch(self, request: web.Request) -> web.Response:
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
                        await drop.generate_claim()
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
