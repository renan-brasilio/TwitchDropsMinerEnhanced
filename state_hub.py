from __future__ import annotations

import os
import sys
import time
import json
import asyncio
import logging
import subprocess
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from yarl import URL
from constants import State, PriorityMode, MAX_INT

if TYPE_CHECKING:
    from twitch import Twitch
    from channel import Channel
    from inventory import DropsCampaign, TimedDrop
    from utils import Game

logger = logging.getLogger("TwitchDrops")


class _HubComponent:
    def __init__(self, hub: StateHub) -> None:
        self._hub = hub


class StatusHub(_HubComponent):
    def update(self, text: str) -> None:
        self._hub.set_status_text(text)


class TrayHub(_HubComponent):
    def change_icon(self, icon_name: str) -> None:
        self._hub.set_tray_icon(icon_name)

    def notify(self, text: str, title: str = "Twitch Drops Miner") -> None:
        self._hub.send_notification(title, text)


class WebsocketHub(_HubComponent):
    def update(self, index: int, status: str | None = None, topics: int | None = None) -> None:
        self._hub.update_websocket(index, status, topics)

    def remove(self, index: int) -> None:
        self._hub.remove_websocket(index)


class LoginHub(_HubComponent):
    def __init__(self, hub: StateHub) -> None:
        super().__init__(hub)
        self.code_entered = asyncio.Event()

    async def ask_enter_code(self, verification_uri: URL, user_code: str) -> None:
        self._hub.set_device_code(str(verification_uri), user_code)
        # Attempt to auto-open in browser if on macOS
        if sys.platform == "darwin":
            try:
                subprocess.run(
                    ["open", str(verification_uri)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            except Exception:
                pass

    def update(self, status_text: str, user_id: int | None = None) -> None:
        self._hub.update_auth_status(status_text, user_id)


class ProgressHub(_HubComponent):
    ALMOST_DONE_SECONDS = 15

    def __init__(self, hub: StateHub) -> None:
        super().__init__(hub)
        self._seconds: int = 0
        self._timer_task: asyncio.Task[None] | None = None
        self._current_drop: TimedDrop | None = None

    def stop_timer(self) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()

    def clear(self) -> None:
        self.stop_timer()
        self._current_drop = None
        self._seconds = 0

    def start_timer(self) -> None:
        self._start_countdown()

    def minute_almost_done(self) -> bool:
        return self._timer_task is None or self._seconds <= self.ALMOST_DONE_SECONDS

    def display(self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False) -> None:
        self._current_drop = drop
        self._hub.update_active_drop(drop)
        if countdown:
            self._start_countdown(subone=subone)

    def _start_countdown(self, subone: bool = False) -> None:
        if self._timer_task is not None and not self._timer_task.done():
            self._timer_task.cancel()
        self._seconds = 60 if not subone else 59
        self._timer_task = self._hub._twitch.create_account_task(self._countdown_loop())

    async def _countdown_loop(self) -> None:
        try:
            while self._seconds > 0:
                await asyncio.sleep(1)
                self._seconds -= 1
                if self._current_drop:
                    self._hub.update_countdown_seconds(self._seconds)
        except asyncio.CancelledError:
            pass


class ChannelListHub(_HubComponent):
    def __init__(self, hub: StateHub) -> None:
        super().__init__(hub)
        self._selection: Channel | None = None

    def clear(self) -> None:
        self._selection = None
        self._hub.clear_channels()

    def clear_watching(self) -> None:
        self._hub.set_watching_channel(None)

    def display(self, channel: Channel, add: bool = False) -> None:
        self._hub.upsert_channel(channel)

    def display_many(self, channels: list[Channel]) -> None:
        self._hub.set_channels(channels)

    def remove(self, channel: Channel) -> None:
        self._hub.remove_channel(channel)

    def set_watching(self, channel: Channel | None) -> None:
        self._hub.set_watching_channel(channel)

    def select(self, channel: Channel) -> None:
        self._selection = channel

    def get_selection(self) -> Channel | None:
        channel = self._selection
        self._selection = None
        return channel

class InventoryHub(_HubComponent):
    def clear(self) -> None:
        self._hub.clear_inventory()

    def update_drop(self, drop: TimedDrop) -> None:
        self._hub.update_inventory_drop(drop)
    def update_campaign(self, campaign: DropsCampaign) -> None:
        self._hub.update_inventory_campaign(campaign)

    async def add_campaign(self, campaign: DropsCampaign) -> None:
        self._hub.update_inventory_campaign(campaign)


class _DummyWidget:
    def config(self, **kwargs) -> None:
        pass


class HelpHub(_HubComponent):
    def __init__(self, hub: StateHub) -> None:
        super().__init__(hub)
        self._invalidate_button = _DummyWidget()


class StateHub:
    def __init__(self, twitch: Twitch) -> None:
        self._twitch = twitch
        self._close_requested = asyncio.Event()
        self._closed = asyncio.Event()

        # Components for GUI compatibility
        self.status = StatusHub(self)
        self.tray = TrayHub(self)
        self.websockets = WebsocketHub(self)
        self.login = LoginHub(self)
        self.progress = ProgressHub(self)
        self.channels = ChannelListHub(self)
        self.inv = InventoryHub(self)
        self.help = HelpHub(self)

        # In-memory Application State
        self.state_enum: State = State.IDLE
        self.status_text: str = (
            "Disconnected from Twitch" if getattr(twitch.settings, "auth_disconnected", False)
            else "Initializing..."
        )
        self.tray_icon: str = "idle"
        self.is_logged_in: bool = False
        self.user_id: int | None = None
        self.username: str | None = None
        self.device_code_data: dict[str, str] | None = None
        # Why the last login attempt failed; cleared on reconnect or successful login.
        self.auth_error: str | None = None

        self.active_drop: dict[str, Any] | None = None
        self.active_channel: dict[str, Any] | None = None
        self.channels_map: dict[int, dict[str, Any]] = {}
        self.campaigns_map: dict[str, dict[str, Any]] = {}
        self._inventory_campaigns: dict[str, DropsCampaign] = {}
        self.available_games: list[dict[str, Any]] = []
        self.websockets_status: dict[int, dict[str, Any]] = {}

        self.logs: deque[dict[str, Any]] = deque(maxlen=250)
        self._subscribers: set[asyncio.Queue] = set()

        # Connect logging
        self._log_handler = _StateHubLogHandler(self)
        logger.addHandler(self._log_handler)

    @property
    def close_requested(self) -> bool:
        return self._close_requested.is_set()

    def start(self) -> None:
        self.set_status_text("Running")

    def stop(self) -> None:
        self._close_requested.set()
        self._closed.set()

    def close(self) -> None:
        self.stop()

    def close_window(self) -> None:
        self.stop()

    def prevent_close(self) -> None:
        pass

    async def wait_until_closed(self) -> None:
        await self._closed.wait()

    async def coro_unless_closed(self, coro: Any) -> Any:
        tasks = [asyncio.ensure_future(coro), asyncio.ensure_future(self._close_requested.wait())]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if self._close_requested.is_set():
                from exceptions import ExitRequest
                raise ExitRequest()
            return await next(iter(done))
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    def grab_attention(self, sound: bool = False) -> None:
        pass

    def save(self, force: bool = False) -> None:
        pass

    def print(self, message: str) -> None:
        self.add_log("INFO", message)

    def add_log(self, level: str, message: str) -> None:
        entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": level,
            "message": message.strip(),
        }
        self.logs.append(entry)
        self.broadcast_event("log", entry)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def broadcast_event(self, event_type: str, data: Any) -> None:
        if not self._subscribers:
            return
        payload = {"event": event_type, "data": data, "timestamp": time.time()}
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    def broadcast_settings(self) -> None:
        """Tell dashboards the mining preferences changed (by the API or by the miner itself)."""
        s = self._twitch.settings
        self.broadcast_event("settings", {
            "priority": list(s.priority),
            "exclude": list(s.exclude),
            "priority_mode": s.priority_mode.value,
            "auto_claim": s.auto_claim,
            "auto_select_linked": s.auto_select_linked,
        })

    def set_status_text(self, text: str) -> None:
        self.status_text = text
        self.broadcast_event("status", {"status": text, "state": self.state_enum.name})

    def set_tray_icon(self, icon_name: str) -> None:
        self.tray_icon = icon_name
        self.broadcast_event("tray", {"icon": icon_name})

    def set_device_code(self, uri: str, code: str) -> None:
        self.device_code_data = {"uri": uri, "code": code}
        self.add_log("WARNING", f"Twitch activation code required: {code} at {uri}")
        self.broadcast_event("auth_code", self.device_code_data)

    def clear_drop(self) -> None:
        self.progress.clear()
        self.active_drop = None
        self.broadcast_event("drop", None)

    def update_auth_status(self, status_text: str, user_id: int | None = None) -> None:
        self.is_logged_in = user_id is not None
        self.user_id = user_id
        if user_id:
            self.device_code_data = None
            self.auth_error = None
        self.broadcast_event("auth", {
            "status": status_text,
            "logged_in": self.is_logged_in,
            "user_id": user_id,
            "username": self.username,
            "auth_disconnected": getattr(self._twitch.settings, "auth_disconnected", False),
            "auth_pending": self._twitch._account_transitioning or bool(self._twitch._disconnect_pending),
            "device_code": self.device_code_data,
            "auth_error": self.auth_error,
        })

    def clear_account(self) -> None:
        self.username = None
        self.device_code_data = None
        self.login.code_entered.clear()
        self.clear_drop()
        self.channels.clear()
        self.channels.clear_watching()
        self.clear_inventory()
        self.set_games(set())
        self.websockets_status.clear()
        self.broadcast_event("websockets", [])
        self.update_auth_status("Disconnected" if getattr(
            self._twitch.settings, "auth_disconnected", False
        ) else "Not connected")

    def update_countdown_seconds(self, seconds: int) -> None:
        if self.active_drop:
            self.active_drop["seconds"] = seconds
            self.broadcast_event("countdown", {"seconds": seconds})

    def update_active_drop(self, drop: TimedDrop) -> None:
        req = drop.required_minutes or 1
        curr = drop.current_minutes
        pct = round(min(100.0, (curr / req) * 100), 1)
        remaining = max(0, req - curr)
        
        image_url = ""
        if hasattr(drop, "image_url") and drop.image_url:
            image_url = str(drop.image_url)
        elif hasattr(drop, "benefits") and drop.benefits and drop.benefits[0].image_url:
            image_url = str(drop.benefits[0].image_url)

        game_box_art = ""
        if hasattr(drop.campaign, "image_url") and drop.campaign.image_url:
            game_box_art = str(drop.campaign.image_url)

        self.active_drop = {
            "id": drop.id,
            "name": drop.name,
            "campaign_id": drop.campaign.id,
            "campaign_name": drop.campaign.name,
            "game": str(drop.campaign.game),
            "game_box_art": game_box_art,
            "current_minutes": curr,
            "required_minutes": req,
            "remaining_minutes": remaining,
            "percentage": pct,
            "image_url": image_url,
            "seconds": 60,
        }
        self.broadcast_event("drop", self.active_drop)

    def update_countdown_seconds(self, seconds: int) -> None:
        if self.active_drop:
            self.active_drop["seconds"] = seconds
            self.broadcast_event("countdown", {"seconds": seconds})

    def display_drop(self, drop: TimedDrop, *, countdown: bool = True, subone: bool = False) -> None:
        self.progress.display(drop, countdown=countdown, subone=subone)

    def set_watching_channel(self, channel: Channel | None) -> None:
        if channel is None:
            self.active_channel = None
        else:
            self.active_channel = {
                "id": channel.id,
                "name": channel.name,
                "viewers": channel.viewers,
                "url": f"https://www.twitch.tv/{channel.name}",
                "online": channel.online,
                "game": str(channel.game) if channel.game else "",
            }
        self.broadcast_event("watching", self.active_channel)
        for channel_data in self.channels_map.values():
            channel_data["watching"] = channel is not None and channel_data["id"] == channel.id
        self.broadcast_event("channels", list(self.channels_map.values()))

    def clear_channels(self) -> None:
        self.channels_map.clear()
        self.broadcast_event("channels", [])

    def upsert_channel(self, channel: Channel) -> None:
        self.channels_map[channel.id] = {
            "id": channel.id,
            "name": channel.name,
            "viewers": channel.viewers,
            "online": channel.online,
            "priority": self._twitch.get_priority(channel) if hasattr(self._twitch, "get_priority") else 0,
            "game": str(channel.game) if channel.game else "",
            "watching": bool(self.active_channel and self.active_channel["id"] == channel.id),
        }
        self.broadcast_event("channels", list(self.channels_map.values()))

    def set_channels(self, channels: list[Channel]) -> None:
        self.channels_map = {
            ch.id: {
                "id": ch.id,
                "name": ch.name,
                "viewers": ch.viewers,
                "online": ch.online,
                "priority": self._twitch.get_priority(ch) if hasattr(self._twitch, "get_priority") else 0,
                "game": str(ch.game) if ch.game else "",
                "watching": bool(self.active_channel and self.active_channel["id"] == ch.id),
            }
            for ch in channels
        }
        self.broadcast_event("channels", list(self.channels_map.values()))

    def remove_channel(self, channel: Channel) -> None:
        self.channels_map.pop(channel.id, None)
        self.broadcast_event("channels", list(self.channels_map.values()))

    def clear_inventory(self) -> None:
        self.campaigns_map.clear()
        self._inventory_campaigns.clear()
        self.broadcast_event("inventory", [])

    def update_inventory_drop(self, drop: TimedDrop) -> None:
        camp_id = drop.campaign.id
        if camp_id not in self.campaigns_map:
            self.update_inventory_campaign(drop.campaign)
        else:
            campaign_data = self.campaigns_map[camp_id]
            for d in campaign_data["drops"]:
                if d["id"] == drop.id:
                    d["current_minutes"] = drop.current_minutes
                    d["percentage"] = round((drop.current_minutes / (drop.required_minutes or 1)) * 100, 1)
                    d["is_claimed"] = drop.is_claimed
                    d["can_claim"] = drop.can_claim
            self.broadcast_event("inventory", self.get_inventory())

    def update_inventory_campaign(self, campaign: DropsCampaign) -> None:
        self._inventory_campaigns[campaign.id] = campaign
        drops_list = []
        for drop in campaign.drops:
            image_url = ""
            if hasattr(drop, "image_url") and drop.image_url:
                image_url = str(drop.image_url)
            elif hasattr(drop, "benefits") and drop.benefits and drop.benefits[0].image_url:
                image_url = str(drop.benefits[0].image_url)

            drops_list.append({
                "id": drop.id,
                "name": drop.name,
                "image_url": image_url,
                "current_minutes": getattr(drop, "current_minutes", 0),
                "required_minutes": getattr(drop, "required_minutes", 60),
                "percentage": round(
                    (getattr(drop, "current_minutes", 0) / (getattr(drop, "required_minutes", 60) or 1)) * 100, 1
                ),
                "is_claimed": getattr(drop, "is_claimed", False),
                "can_claim": getattr(drop, "can_claim", False),
            })

        box_art = ""
        if hasattr(campaign, "image_url") and campaign.image_url:
            box_art = str(campaign.image_url)

        self.campaigns_map[campaign.id] = {
            "id": campaign.id,
            "name": campaign.name,
            "game": str(campaign.game),
            "box_art": box_art,
            "linked": campaign.linked,
            "link_unknown": campaign.link_unknown,
            "link_url": campaign.link_url if campaign.link_url.startswith("https://") else "",
            "upcoming": campaign.upcoming,
            "active": campaign.active,
            "starts_at": campaign.starts_at.isoformat() if hasattr(campaign, "starts_at") else "",
            "ends_at": campaign.ends_at.isoformat() if hasattr(campaign, "ends_at") else "",
            "drops": drops_list,
        }
        self.broadcast_event("inventory", self.get_inventory())

    def get_inventory(self) -> list[dict[str, Any]]:
        visible = []
        for campaign_id, data in self.campaigns_map.items():
            campaign = self._inventory_campaigns[campaign_id]
            ongoing = campaign.active or campaign.upcoming
            claimable = {
                drop.id for drop in campaign.drops
                if campaign.eligible and not drop.is_claimed and drop.can_claim
            }
            if not claimable and (not ongoing or campaign.finished):
                continue
            visible.append({
                **data,
                "active": campaign.active,
                "upcoming": campaign.upcoming,
                "ignored": campaign.ignored,
                "focused": campaign.focused,
                # "Mine now" needs something left to earn right now
                "can_start": campaign.active and not campaign.ignored and any(
                    not drop.is_claimed and drop.current_minutes < drop.required_minutes
                    for drop in campaign.drops
                ),
                "drops": [
                    {**drop, "can_claim": drop["id"] in claimable}
                    for drop in data["drops"]
                    if ongoing or drop["id"] in claimable
                ],
            })
        return visible

    def counted_inventory(self) -> list[dict[str, Any]]:
        """Visible campaigns the user hasn't ignored; these drive totals and the drops view."""
        return [campaign for campaign in self.get_inventory() if not campaign["ignored"]]

    def set_games(self, games: set[Game]) -> None:
        self.available_games = [
            {"id": g.id, "name": g.name, "slug": getattr(g, "slug", "")}
            for g in sorted(games, key=lambda g: g.name)
        ]
        self.broadcast_event("games", self.available_games)

    def update_websocket(self, index: int, status: str | None = None, topics: int | None = None) -> None:
        if index not in self.websockets_status:
            self.websockets_status[index] = {"index": index, "status": "unknown", "topics": 0}
        if status is not None:
            self.websockets_status[index]["status"] = status
        if topics is not None:
            self.websockets_status[index]["topics"] = topics
        self.broadcast_event("websockets", list(self.websockets_status.values()))

    def remove_websocket(self, index: int) -> None:
        self.websockets_status.pop(index, None)
        self.broadcast_event("websockets", list(self.websockets_status.values()))

    def send_notification(self, title: str, text: str) -> None:
        self.add_log("INFO", f"Notification: {title} - {text}")
        if sys.platform == "darwin":
            try:
                # Use macOS AppleScript notification
                apple_script = f'display notification "{text}" with title "{title}"'
                subprocess.Popen(["osascript", "-e", apple_script])
            except Exception:
                pass
        self.broadcast_event("notification", {"title": title, "text": text})

    def get_full_status(self) -> dict[str, Any]:
        return {
            "state": self.state_enum.name,
            "status_text": self.status_text,
            "tray_icon": self.tray_icon,
            "is_logged_in": self.is_logged_in,
            "user_id": self.user_id,
            "username": self.username,
            "auth_disconnected": getattr(self._twitch.settings, "auth_disconnected", False),
            "auth_pending": self._twitch._account_transitioning or bool(self._twitch._disconnect_pending),
            "active_drop": self.active_drop,
            "active_channel": self.active_channel,
            "device_code": self.device_code_data,
            "auth_error": self.auth_error,
            "channels_count": len(self.channels_map),
            "campaigns_count": len(self.counted_inventory()),
            "websockets": list(self.websockets_status.values()),
        }


class _StateHubLogHandler(logging.Handler):
    def __init__(self, hub: StateHub) -> None:
        super().__init__()
        self._hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self._hub.add_log(record.levelname, msg)
        except Exception:
            pass
