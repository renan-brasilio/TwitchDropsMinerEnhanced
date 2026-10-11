"""Reusable E2E harness: real dashboard + backend, virtual Twitch upstream.

The backend (``Twitch`` + ``APIServer``) runs in-process on a dedicated asyncio loop thread
so the synchronous Playwright API can drive Chromium from the test thread. Every request the
miner makes to a Twitch host is rerouted at the ``aiohttp.ClientSession`` boundary to a local
aiohttp server that answers OAuth, GQL, playlist and PubSub traffic with synthetic data.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]


def _bootstrap_product_paths() -> None:
    """Make product imports independent of the cwd and keep them out of the checkout.

    ``constants`` derives WORKING_DIR (``lang``/``web`` lookup) and DATA_DIR from
    ``sys.argv[0]``, and importing ``translate`` rewrites ``<WORKING_DIR>/lang/English.json``.
    Point them at a throwaway runtime dir holding a copy of ``lang`` and a link to ``web``.
    """
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    if "constants" in sys.modules:
        return
    runtime = Path(tempfile.mkdtemp(prefix="tdm-e2e-runtime-"))
    atexit.register(shutil.rmtree, runtime, ignore_errors=True)
    shutil.copytree(REPO / "lang", runtime / "lang")
    for name in ("web", "icons"):
        if (REPO / name).exists():
            (runtime / name).symlink_to(REPO / name, target_is_directory=True)
    argv0 = sys.argv[0] if sys.argv else ""
    data_dir = os.environ.get("DATA_DIR")
    if sys.argv:
        sys.argv[0] = str(runtime / "main.py")
    else:
        sys.argv.append(str(runtime / "main.py"))
    os.environ["DATA_DIR"] = str(runtime / "data")
    try:
        import constants  # noqa: F401
        import translate  # noqa: F401
    finally:
        sys.argv[0] = argv0
        if data_dir is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = data_dir


_bootstrap_product_paths()

import aiohttp  # noqa: E402
from aiohttp import web, WSMsgType  # noqa: E402
from yarl import URL  # noqa: E402

import api  # noqa: E402
from api import APIServer  # noqa: E402
from constants import ClientType, PriorityMode  # noqa: E402
from settings import Settings  # noqa: E402
from twitch import Twitch  # noqa: E402

REQUIRE_E2E = os.environ.get("REQUIRE_E2E") == "1"
try:
    from playwright.sync_api import Error as PlaywrightError, expect, sync_playwright
except ImportError as exc:  # pragma: no cover - depends on the environment
    if REQUIRE_E2E:
        raise
    PlaywrightError = expect = sync_playwright = None
    PLAYWRIGHT_MISSING = (
        f"Playwright is not installed ({exc}); run `pip install -r requirements-dev.txt` "
        "and `python -m playwright install chromium`, or set REQUIRE_E2E=1 to fail instead"
    )
else:
    PLAYWRIGHT_MISSING = None
    expect.set_options(timeout=10_000)

# ---------------------------------------------------------------------------------------------
# Synthetic Twitch world
# ---------------------------------------------------------------------------------------------
PRIMARY_TOKEN = "e2e-token-primary"
SECOND_TOKEN = "e2e-token-second"
REJECTED_TOKEN = "e2e-token-rejected"
USERS = {
    PRIMARY_TOKEN: ("5001", "e2e_viewer"),
    SECOND_TOKEN: ("5002", "second_viewer"),
}
GAME_NAME = "E2E Quest"
GAME = {"id": "9001", "name": GAME_NAME, "displayName": GAME_NAME, "slug": "e2e-quest", "boxArtURL": ""}
CAMPAIGN_NAME = "E2E Launch Campaign"
DROP_NAME = "E2E Reward"
STREAMER = {"id": "7001", "login": "e2e_streamer", "displayName": "e2e_streamer"}
BROADCAST_ID = "880001"
VIEWERS = 1234


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class Clock(datetime):
    """Frozen clock for ``inventory.datetime`` (campaign windows are built around it)."""
    current = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class VirtualTwitch:
    """Local aiohttp server answering the Twitch endpoints the miner uses."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.requests: list[tuple[str, str]] = []  # (METHOD, "host/path")
        self.port = 0
        self._runner: web.AppRunner | None = None
        self._sockets: set[web.WebSocketResponse] = set()
        app = web.Application()
        app.router.add_route("*", "/{host}/{path:.*}", self._dispatch)
        self._app = app

    # -- lifecycle (loop thread) --------------------------------------------------------------
    async def start(self) -> None:
        self._runner = web.AppRunner(self._app, shutdown_timeout=1)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", 0).start()
        self.port = self._runner.addresses[0][1]

    async def stop(self) -> None:
        for ws in list(self._sockets):
            await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()

    @property
    def base_url(self) -> URL:
        return URL.build(scheme="http", host="127.0.0.1", port=self.port)

    def count(self, method: str, target: str) -> int:
        return sum(1 for entry in list(self.requests) if entry == (method, target))

    # -- payloads -----------------------------------------------------------------------------
    def campaign(self) -> dict:
        start, end = self.now - timedelta(days=1), self.now + timedelta(days=7)
        return {
            "id": "e2e-campaign", "name": CAMPAIGN_NAME, "game": dict(GAME),
            "self": {"isAccountConnected": True}, "accountLinkURL": "",
            "startAt": _iso(start), "endAt": _iso(end), "status": "ACTIVE",
            "allow": {"channels": None, "isEnabled": True},
            "timeBasedDrops": [{
                "id": "e2e-drop", "name": DROP_NAME, "startAt": _iso(start), "endAt": _iso(end),
                "benefitEdges": [{"benefit": {
                    "id": "e2e-benefit", "name": DROP_NAME,
                    "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": "",
                }}],
                "preconditionDrops": None, "requiredMinutesWatched": 60,
                "self": {"dropInstanceID": None, "isClaimed": False, "currentMinutesWatched": 20},
            }],
        }

    def _gql(self, op: dict) -> dict:
        name = op.get("operationName")
        variables = op.get("variables") or {}
        if name == "Inventory":
            return {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": [], "gameEventDrops": [],
            }}}}
        if name == "ViewerDropsDashboard":
            full = self.campaign()
            summary = {key: full[key] for key in (
                "id", "name", "game", "self", "startAt", "endAt", "status", "accountLinkURL")}
            return {"data": {"currentUser": {"dropCampaigns": [summary]}}}
        if name == "DropCampaignDetails":
            return {"data": {"user": {"dropCampaign": self.campaign()}}}
        if name == "DirectoryPage_Game":
            edges = []
            if variables.get("slug") == GAME["slug"]:
                edges.append({"node": {
                    "id": BROADCAST_ID, "title": "E2E stream", "viewersCount": VIEWERS,
                    "game": dict(GAME), "broadcaster": dict(STREAMER),
                }})
            return {"data": {"game": {"streams": {"edges": edges}}}}
        if name == "VideoPlayerStreamInfoOverlayChannel":
            user = None
            if variables.get("channel") == STREAMER["login"]:
                user = {**STREAMER, "stream": {"id": BROADCAST_ID, "viewersCount": VIEWERS},
                        "broadcastSettings": {"title": "E2E stream", "game": dict(GAME)}}
            return {"data": {"user": user}}
        if name == "DropsHighlightService_AvailableDrops":
            return {"data": {"channel": {"id": variables.get("channelID"),
                                         "viewerDropCampaigns": [{"id": "e2e-campaign"}]}}}
        if name == "PlaybackAccessToken":
            return {"data": {"streamPlaybackAccessToken": {"value": "e2e-playback", "signature": "e2esig"}}}
        if name == "DropCurrentSessionContext":
            return {"data": {"currentUser": {"dropCurrentSession": None}}}
        if name == "DropsPage_ClaimDropRewards":
            return {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}
        if "query" in op:  # sendSpadeEvents mutation
            return {"data": {"sendSpadeEvents": {"statusCode": 204}}}
        return {"errors": [{"message": f"virtual Twitch: unsupported operation {name!r}"}],
                "extensions": {"operationName": name}}

    # -- routing ------------------------------------------------------------------------------
    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        host = request.match_info["host"]
        path = "/" + request.match_info["path"]
        self.requests.append((request.method, host + path))
        if host == "pubsub-edge.twitch.tv":
            return await self._pubsub(request)
        if host == "www.twitch.tv":
            return web.Response(text="<html><body>twitch</body></html>", content_type="text/html")
        if host == "id.twitch.tv":
            return await self._oauth(request, path)
        if host == "gql.twitch.tv" and path == "/gql":
            body = await request.json()
            if isinstance(body, list):
                return web.json_response([self._gql(op) for op in body])
            return web.json_response(self._gql(body))
        if host == "usher.ttvnw.net":
            return web.Response(text=(
                "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=160000\n"
                "https://video-weaver.e2e.hls.ttvnw.net/v1/playlist/e2e.m3u8\n"
            ))
        if host == "video-weaver.e2e.hls.ttvnw.net":
            return web.Response(text="#EXTM3U\nhttps://video-edge.e2e.ttvnw.net/v1/segment/0001.ts\n")
        if host == "video-edge.e2e.ttvnw.net":
            return web.Response(status=200)
        return web.json_response({"error": "Not Found"}, status=404)

    async def _oauth(self, request: web.Request, path: str) -> web.Response:
        if path == "/oauth2/validate":
            token = request.headers.get("Authorization", "").removeprefix("OAuth ")
            if token not in USERS:
                return web.json_response({"status": 401, "message": "invalid access token"}, status=401)
            user_id, login = USERS[token]
            return web.json_response({"client_id": ClientType.WEB.CLIENT_ID, "login": login,
                                      "user_id": user_id, "scopes": [], "expires_in": 3600})
        if path == "/oauth2/device":
            return web.json_response({"device_code": "e2e-device-code", "user_code": "E2EC0DE1",
                                      "verification_uri": "https://www.twitch.tv/activate",
                                      "interval": 5, "expires_in": 1800})
        if path == "/oauth2/token":
            return web.json_response({"status": 400, "message": "authorization_pending"}, status=400)
        return web.json_response({"status": 404}, status=404)

    async def _pubsub(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._sockets.add(ws)
        try:
            async for message in ws:
                if message.type is not WSMsgType.TEXT:
                    continue
                payload = json.loads(message.data)
                if payload.get("type") == "PING":
                    await ws.send_json({"type": "PONG"})
                elif payload.get("type") in ("LISTEN", "UNLISTEN"):
                    await ws.send_json({"type": "RESPONSE", "nonce": payload.get("nonce"), "error": ""})
        finally:
            self._sockets.discard(ws)
        return ws


_RealClientSession = aiohttp.ClientSession


class RoutedSession:
    """``aiohttp.ClientSession`` stand-in that sends every Twitch request to VirtualTwitch."""

    def __init__(self, upstream: VirtualTwitch, **kwargs) -> None:
        self._upstream = upstream
        self._session = _RealClientSession(**kwargs)
        # Twitch sets this cookie on the first page load; rerouted requests can't set it.
        self._session.cookie_jar.update_cookies({"unique_id": "e2e-device"}, ClientType.WEB.CLIENT_URL)

    def __getattr__(self, name):
        return getattr(self._session, name)

    def _route(self, url) -> URL:
        target = URL(url)
        if target.host in ("127.0.0.1", "localhost"):
            return target
        return self._upstream.base_url.with_path(f"/{target.host}{target.path}").with_query(target.query)

    def request(self, method, url, **kwargs):
        return self._session.request(method, self._route(url), **kwargs)

    def ws_connect(self, url, **kwargs):
        return self._session.ws_connect(self._route(url), **kwargs)


class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._serve, name="e2e-backend-loop", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, coro, timeout: float = 20):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self) -> None:
        async def cancel_leftovers():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.loop.shutdown_asyncgens()

        try:
            self.call(cancel_leftovers(), timeout=10)
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(10)
            self.loop.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class DashboardHarness:
    """Owns temp data, the virtual upstream, and one (restartable) backend instance."""

    def __init__(self, *, token: str = PRIMARY_TOKEN, disconnected: bool = False,
                 auto_claim: bool = False, priority: tuple[str, ...] = (GAME_NAME,),
                 priority_mode: PriorityMode = PriorityMode.PRIORITY_ONLY) -> None:
        self._seed = dict(token="" if disconnected else token, disconnected=disconnected,
                          auto_claim=auto_claim, priority=list(priority), priority_mode=priority_mode)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/"
        self.upstream = VirtualTwitch(self.now)
        self.backend_errors: list[BaseException] = []
        self.twitch: Twitch | None = None
        self._server: APIServer | None = None
        self._owner: asyncio.Task | None = None
        self._loop: _LoopThread | None = None
        self._temp: tempfile.TemporaryDirectory | None = None
        self._patchers: list = []

    @property
    def settings_path(self) -> Path:
        assert self._temp is not None
        return Path(self._temp.name, "settings.json")

    @property
    def cookies_path(self) -> Path:
        assert self._temp is not None
        return Path(self._temp.name, "cookies.jar")

    def start(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="tdm-e2e-")
        Clock.current = self.now
        upstream = self.upstream
        self._patchers = [
            patch("settings.SETTINGS_PATH", self.settings_path),
            patch("twitch.COOKIES_PATH", self.cookies_path),
            patch.object(api, "WEB_DIR", REPO / "web"),
            patch("inventory.datetime", Clock),
            patch.dict(os.environ, {"API_KEY": "", "TWITCH_AUTH_TOKEN": ""}),
            patch("twitch.aiohttp.ClientSession", lambda **kwargs: RoutedSession(upstream, **kwargs)),
        ]
        for patcher in self._patchers:
            patcher.start()
        try:
            self._write_seed_settings()
            self._loop = _LoopThread()
            self._loop.call(self.upstream.start())
            self._start_backend()
        except BaseException:
            self.stop()
            raise

    def _write_seed_settings(self) -> None:
        seed = self._seed
        settings = Settings(SimpleNamespace(dump=False))
        settings.priority = list(seed["priority"])
        settings.priority_mode = seed["priority_mode"]
        settings.auto_claim = seed["auto_claim"]
        settings.auth_token = seed["token"]
        settings.auth_disconnected = seed["disconnected"]
        settings.save(force=True)

    def _start_backend(self) -> None:
        assert self._loop is not None
        self._loop.call(self._boot())

    async def _boot(self) -> None:
        twitch = Twitch(Settings(SimpleNamespace(dump=False)))
        server = APIServer(twitch, twitch.gui, host="127.0.0.1", port=self.port)
        runner = web.AppRunner(server._app, shutdown_timeout=1)
        await runner.setup()
        server._runner = runner
        self.twitch, self._server = twitch, server
        await web.TCPSite(runner, "127.0.0.1", self.port).start()
        self._owner = asyncio.create_task(twitch.run(), name="e2e-twitch-run")
        if not twitch.settings.auth_disconnected and twitch.settings.auth_token in USERS:
            await asyncio.wait_for(twitch.wait_until_login(), 10)

    async def _halt(self) -> None:
        twitch, server, owner = self.twitch, self._server, self._owner
        self.twitch = self._server = self._owner = None
        if twitch is None:
            return
        twitch.gui.close()  # main.py closes the hub; run() then shuts the account down.
        if owner is not None:
            done, _ = await asyncio.wait({owner}, timeout=10)
            if not done:
                owner.cancel()
                await asyncio.gather(owner, return_exceptions=True)
            elif not owner.cancelled() and owner.exception() is not None:
                self.backend_errors.append(owner.exception())
        twitch.gui.progress.stop_timer()
        if server is not None:
            await server.stop()
        logging.getLogger("TwitchDrops").removeHandler(twitch.gui._log_handler)

    def restart_backend(self) -> None:
        """Simulate an app restart: new Twitch/APIServer on the same port and data files."""
        assert self._loop is not None
        self._loop.call(self._halt())
        self._start_backend()

    def stop(self) -> None:
        try:
            if self._loop is not None:
                try:
                    self._loop.call(self._halt())
                    self._loop.call(self.upstream.stop())
                finally:
                    self._loop.close()
                    self._loop = None
        finally:
            for patcher in reversed(self._patchers):
                patcher.stop()
            self._patchers = []
            if self._temp is not None:
                self._temp.cleanup()
                self._temp = None


# ---------------------------------------------------------------------------------------------
# Simulated macOS native bridge (mac/main.swift contract)
# ---------------------------------------------------------------------------------------------
_NATIVE_BRIDGE_JS = """
(() => {
  const config = %s;
  const bridge = {
    messages: [],
    autoReply: config.autoReply,
    connectionError: config.connectionError,
    state: config.state,
    apiKey: config.apiKey,
    publish(error) {
      // main.swift publishNativeSettings(): the API key itself never reaches the page.
      const settings = JSON.parse(JSON.stringify(bridge.state));
      settings.connection.hasApiKey = Boolean(bridge.apiKey);
      if (error) settings.error = error;
      window.nativeSettings = settings;
      window.dispatchEvent(new CustomEvent('native-settings', {detail: window.nativeSettings}));
    },
    handle(message) {
      if (message.connection) {
        if (bridge.connectionError) { bridge.publish(bridge.connectionError); return; }
        const c = message.connection;
        bridge.state.connection.mode = c.mode;
        bridge.state.connection.serverURL = c.mode === 'remote' ? c.serverURL : '';
        if (typeof c.apiKey === 'string') bridge.apiKey = c.apiKey.trim();
        bridge.reconnected = true;  // the real app now reloads the page from the new miner
        return;
      }
      if (typeof message.keepRunningOnClose === 'boolean') bridge.state.keepRunningOnClose = message.keepRunningOnClose;
      if (typeof message.menuBarStyle === 'string') bridge.state.menuBarStyle = message.menuBarStyle;
      bridge.publish();
    },
    replyToPending() {
      const pending = bridge.messages.slice(bridge.handled);
      bridge.handled = bridge.messages.length;
      pending.forEach(message => bridge.handle(message));
    },
    handled: 0,
    reconnected: false,
  };
  window.__nativeBridge = bridge;
  window.webkit = {messageHandlers: {settings: {postMessage(body) {
    // WKScriptMessage bodies are structured clones.
    bridge.messages.push(JSON.parse(JSON.stringify(body)));
    if (bridge.autoReply) setTimeout(() => bridge.replyToPending(), 0);
  }}}};
  // main.swift publishes on webView(_:didFinish:).
  window.addEventListener('load', () => bridge.publish());
})();
"""


def native_bridge_script(*, keep_running: bool = True, menu_bar_style: str = "iconOnly",
                         mode: str = "local", server_url: str = "", api_key: str = "",
                         auto_reply: bool = True, connection_error: str | None = None) -> str:
    config = {
        "autoReply": auto_reply,
        "connectionError": connection_error,
        "apiKey": api_key,
        "state": {
            "keepRunningOnClose": keep_running,
            "menuBarStyle": menu_bar_style,
            "connection": {"mode": mode, "serverURL": server_url, "hasApiKey": bool(api_key)},
        },
    }
    return _NATIVE_BRIDGE_JS % json.dumps(config)


# ---------------------------------------------------------------------------------------------
# unittest base class
# ---------------------------------------------------------------------------------------------
class DashboardTestCase(unittest.TestCase):
    """One Chromium per class; a fresh backend, data dir and browser context per test."""

    harness_options: dict = {}

    @classmethod
    def setUpClass(cls) -> None:
        if PLAYWRIGHT_MISSING:
            raise unittest.SkipTest(PLAYWRIGHT_MISSING)
        cls._playwright = sync_playwright().start()
        try:
            cls.browser = cls._playwright.chromium.launch()
        except PlaywrightError as exc:
            cls._playwright.stop()
            if REQUIRE_E2E:
                raise
            raise unittest.SkipTest(
                f"Chromium for Playwright is unavailable ({exc.message.splitlines()[0]}); "
                "run `python -m playwright install chromium`, or set REQUIRE_E2E=1 to fail instead"
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls._playwright.stop()

    def setUp(self) -> None:
        self.harness = DashboardHarness(**self.harness_options)
        self.harness.start()
        self.addCleanup(self.harness.stop)
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)  # cleanups are LIFO: the page goes before the backend
        # Nothing in the dashboard may reach the internet during tests.
        self.context.route(lambda url: not url.startswith("http://127.0.0.1:"), lambda route: route.abort())
        self.page = self.context.new_page()

    def open_dashboard(self) -> None:
        self.page.goto(self.harness.url)

    def open_settings(self) -> None:
        self.page.get_by_role("button", name="Settings", exact=True).click()
        expect(self.page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()

    def open_campaigns(self) -> None:
        self.page.get_by_role("button", name="Campaigns & Rewards").click()
