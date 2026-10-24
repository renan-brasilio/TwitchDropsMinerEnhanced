"""REST/WebSocket contract and remote-security regression tests for api.py."""
import asyncio
import copy
import json
import logging
import os
import tempfile
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

from aiohttp import web, WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from api import APIServer
from constants import IRL_TAG_ID, PriorityMode, State
from inventory import DropsCampaign
from settings import Settings
from twitch import Twitch
from utils import Game
from version import __version__

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
API_KEY = "synthetic-api-key-5c1e"
AUTH_TOKEN = "synthetic-auth-token-9b7d42"
UNAUTHORIZED = {"error": "Unauthorized", "message": "Invalid or missing API key"}


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def campaign_payload(name, *, game="Game", game_id="1", start=NOW - timedelta(days=1),
                     end=NOW + timedelta(days=1), minutes=20, claim_id=None, claimed=False,
                     drop_name=None):
    """Twitch-shaped DropCampaign payload with a single time-based drop."""
    return {
        "id": name, "name": name, "game": {"id": game_id, "name": game, "boxArtURL": ""},
        "self": {"isAccountConnected": True}, "accountLinkURL": "",
        "startAt": stamp(start), "endAt": stamp(end),
        "status": "ACTIVE" if end > NOW else "EXPIRED",
        "allow": {"channels": []},
        "timeBasedDrops": [{
            "id": f"{name}-drop", "name": drop_name or f"{name} reward",
            "startAt": stamp(start), "endAt": stamp(end),
            "benefitEdges": [{"benefit": {"id": f"{name}-benefit", "name": drop_name or f"{name} reward",
                                          "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": ""}}],
            "preconditionDrops": [], "requiredMinutesWatched": 60,
            "self": {"dropInstanceID": claim_id, "isClaimed": claimed, "currentMinutesWatched": minutes},
        }],
    }


class FakeTwitchGQL:
    """Virtualized Twitch GQL boundary: inventory, campaign list and ClaimDrop."""

    def __init__(self, campaigns, claim_status="ELIGIBLE_FOR_ALL"):
        self.campaigns = campaigns
        self.claim_status = claim_status
        self.claimed_instances = []

    async def request(self, ops):
        if isinstance(ops, list):
            return [await self.request(op) for op in ops]
        if "query" in ops:  # campaign discovery: no live Drops Enabled streams, no games found
            if "DropStreams" in ops["query"]:
                return {"data": {"streams": {"edges": [], "pageInfo": {"hasNextPage": False}}}}
            return {"data": {}}
        name = ops["operationName"]
        if name == "Inventory":
            return {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": copy.deepcopy(self.campaigns), "gameEventDrops": [],
            }}}}
        if name == "ViewerDropsDashboard":
            return {"data": {"currentUser": {"dropCampaigns": []}}}
        if name == "DropsPage_ClaimDropRewards":
            self.claimed_instances.append(ops["variables"]["input"]["dropInstanceID"])
            return {"data": {"claimDropRewards": {"status": self.claim_status}}}
        raise AssertionError(f"unexpected GQL operation {name}")


class ApiContractTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.temp = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp.name, "settings.json")
        self.patchers = [
            patch("inventory.datetime", Clock),
            patch("settings.SETTINGS_PATH", self.settings_path),
            patch("twitch.COOKIES_PATH", Path(self.temp.name, "cookies.jar")),
            patch.dict("os.environ", {"API_KEY": ""}),
            patch("twitch.fetch_public_catalog", AsyncMock(return_value=[])),
            # Claims raise desktop notifications through osascript on macOS.
            patch("state_hub.subprocess.Popen"),
        ]
        for patcher in self.patchers:
            patcher.start()
        os.environ.pop("TWITCH_AUTH_TOKEN", None)  # restored by patch.dict
        self.settings = Settings(SimpleNamespace(dump=False))
        self.settings.priority = ["Overwatch", "Game"]
        self.settings.exclude = {"Rust"}
        self.settings.priority_mode = PriorityMode.PRIORITY_ONLY
        self.settings.auto_claim = False
        self.settings.tray_notifications = True
        self.settings.auth_token = AUTH_TOKEN
        self.settings.save(force=True)
        self.twitch = Twitch(self.settings)
        self.hub = self.twitch.gui
        self.api_server = APIServer(self.twitch, self.hub)
        self.client = TestClient(TestServer(self.api_server._app))
        await self.client.start_server()
    async def asyncTearDown(self):
        await self.client.close()
        self.hub.progress.stop_timer()
        if self.twitch._mnt_task is not None:
            self.twitch._mnt_task.cancel()
        logging.getLogger("TwitchDrops").removeHandler(self.hub._log_handler)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def add_campaign(self, name, **kwargs):
        campaign = DropsCampaign(self.twitch, campaign_payload(name, **kwargs), {})
        self.twitch.inventory.append(campaign)
        if campaign.game not in self.twitch.wanted_games:
            self.twitch.wanted_games.append(campaign.game)
        self.twitch._drops.update(campaign.timed_drops)
        self.hub.update_inventory_campaign(campaign)
        return campaign

    def add_channel(self, campaign, channel_id, online=True):
        channel = SimpleNamespace(id=channel_id, name=f"streamer{channel_id}", viewers=10,
                                  online=online, game=campaign.game, drops_enabled=True)
        self.twitch.channels[channel_id] = channel
        self.hub.upsert_channel(channel)
        return channel

    def persisted(self):
        s = Settings(SimpleNamespace(dump=False))
        return {
            "priority": s.priority, "exclude": set(s.exclude), "priority_mode": s.priority_mode,
            "auto_claim": s.auto_claim, "tray_notifications": s.tray_notifications,
            "auth_token": s.auth_token, "auth_disconnected": s.auth_disconnected, "api_port": s.api_port,
            "auto_select_linked": s.auto_select_linked, "auto_selected_campaigns": set(s.auto_selected_campaigns),
        }

    async def get_json(self, path, **kwargs):
        response = await self.client.get(path, **kwargs)
        self.assertEqual(response.status, 200, path)
        return await response.json()

    def silence_server_errors(self):
        """Keep known-bug 500 tracebacks out of the test output (status is still asserted)."""
        server_logger = logging.getLogger("aiohttp.server")
        previous = server_logger.disabled
        server_logger.disabled = True
        self.addCleanup(setattr, server_logger, "disabled", previous)

    @staticmethod
    def drain(queue):
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        return events


class ApiKeyMiddlewareTests(ApiContractTestCase):
    PROTECTED = [
        ("GET", "/api/status"), ("GET", "/api/settings"), ("GET", "/api/logs"),
        ("GET", "/api/inventory"), ("GET", "/api/channels"), ("GET", "/api/games"),
        ("POST", "/api/settings"), ("POST", "/api/control/pause"), ("POST", "/api/control/claim"),
        ("POST", "/api/auth/token"), ("POST", "/api/auth/disconnect"),
    ]

    async def test_protected_routes_without_key_return_401_json_and_have_no_effect(self):
        before = self.persisted()
        status_text = self.hub.status_text
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            for method, path in self.PROTECTED:
                with self.subTest(method=method, path=path):
                    response = await self.client.request(
                        method, path, json={"auto_claim": True, "priority": ["Valorant"], "token": "x"})
                    self.assertEqual(response.status, 401)
                    self.assertEqual(response.content_type, "application/json")
                    self.assertEqual(await response.json(), UNAUTHORIZED)
        self.assertEqual(self.persisted(), before)
        self.assertEqual(self.hub.status_text, status_text)
        self.assertFalse(self.settings.auth_disconnected)

    async def test_key_is_accepted_from_bearer_header_x_api_key_header_and_query(self):
        sources = {
            "bearer": {"headers": {"Authorization": f"Bearer {API_KEY}"}},
            "x-api-key": {"headers": {"X-API-Key": API_KEY}},
            "query": {"params": {"key": API_KEY}},
        }
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            for source, kwargs in sources.items():
                with self.subTest(source=source):
                    status = await self.get_json("/api/status", **kwargs)
                    self.assertIn("state", status)

    async def test_wrong_partial_or_empty_keys_are_rejected_from_every_source(self):
        attempts = {
            "bearer-wrong": {"headers": {"Authorization": "Bearer not-the-key"}},
            "bearer-prefix": {"headers": {"Authorization": f"Bearer {API_KEY[:-1]}"}},
            "bearer-suffix": {"headers": {"Authorization": f"Bearer {API_KEY}x"}},
            "bearer-empty": {"headers": {"Authorization": "Bearer "}},
            "raw-authorization": {"headers": {"Authorization": API_KEY}},
            "x-api-key-wrong": {"headers": {"X-API-Key": "not-the-key"}},
            "query-wrong": {"params": {"key": "not-the-key"}},
            "query-empty": {"params": {"key": ""}},
        }
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            for name, kwargs in attempts.items():
                with self.subTest(attempt=name):
                    response = await self.client.get("/api/settings", **kwargs)
                    self.assertEqual(response.status, 401)
                    self.assertEqual(await response.json(), UNAUTHORIZED)

    async def test_dashboard_static_files_and_preflight_are_exempt_from_the_key(self):
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            for path in ("/", "/index.html"):
                with self.subTest(path=path):
                    response = await self.client.get(path)
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.content_type, "text/html")
            for path in ("/assets/missing.css", "/favicon.ico"):
                with self.subTest(path=path):
                    response = await self.client.get(path)
                    self.assertNotEqual(response.status, 401)  # reaches routing, not the key gate
            response = await self.client.options("/api/settings")
            self.assertEqual(response.status, 204)

    async def test_api_key_is_read_from_environment_on_every_request(self):
        self.assertEqual((await self.client.get("/api/settings")).status, 200)
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            self.assertEqual((await self.client.get("/api/settings")).status, 401)
            response = await self.client.get("/api/settings", headers={"Authorization": f"Bearer {API_KEY}"})
            self.assertEqual(response.status, 200)
        self.assertEqual((await self.client.get("/api/settings")).status, 200)

    async def test_websocket_requires_the_key_and_accepts_it_from_the_query(self):
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            with self.assertRaises(WSServerHandshakeError) as raised:
                await self.client.ws_connect("/ws")
            self.assertEqual(raised.exception.status, 401)
            ws = await self.client.ws_connect("/ws", params={"key": API_KEY})
            try:
                self.assertEqual((await ws.receive_json(timeout=2))["event"], "init")
            finally:
                await ws.close()


class CorsTests(ApiContractTestCase):
    async def test_preflight_returns_204_allowing_dashboard_methods_and_headers(self):
        response = await self.client.options("/api/control/watch", headers={
            "Origin": "https://dashboard.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, authorization",
        })
        self.assertEqual(response.status, 204)
        self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
        methods = {m.strip() for m in response.headers["Access-Control-Allow-Methods"].split(",")}
        self.assertTrue({"GET", "POST", "OPTIONS"} <= methods)
        allowed = {h.strip().lower() for h in response.headers["Access-Control-Allow-Headers"].split(",")}
        self.assertTrue({"content-type", "authorization"} <= allowed)

    async def test_json_responses_allow_cross_origin_reads(self):
        for method, path in (("GET", "/api/status"), ("GET", "/api/settings"), ("POST", "/api/settings")):
            with self.subTest(path=path, method=method):
                response = await self.client.request(method, path, json={})
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")


class SettingsEndpointTests(ApiContractTestCase):
    async def test_invalid_json_is_rejected_with_400_and_nothing_is_saved(self):
        before = self.settings_path.read_bytes()
        for body in ("{not json", "", "{\"auto_claim\": true"):
            with self.subTest(body=body):
                response = await self.client.post(
                    "/api/settings", data=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status, 400)
                self.assertEqual(await response.json(), {"error": "Invalid JSON"})
        self.assertEqual(self.settings_path.read_bytes(), before)

    async def test_wrong_types_and_protected_fields_are_ignored(self):
        before_disk = self.persisted()
        before_api = await self.get_json("/api/settings")
        payloads = [
            {"priority": "Valorant"},
            {"priority": {"0": "Valorant"}},
            {"exclude": "Overwatch"},
            {"exclude": {"Overwatch": True}},
            {"priority_mode": "1"},
            {"priority_mode": 1.0},
            {"priority_mode": 99},
            {"priority_mode": -1},
            {"priority_mode": None},
            {"auth_token": "attacker-token", "auth_disconnected": True, "api_port": 1},
            {"auto_select_linked": "yes"},
            {"auto_select_linked": 1},
            {"auto_selected_campaigns": ["campaign"]},
        ]
        for payload in payloads:
            with self.subTest(payload=payload):
                response = await self.client.post("/api/settings", json=payload)
                # Rejecting (400) or ignoring (200) are both acceptable; applying is not.
                self.assertIn(response.status, (200, 400))
                self.assertEqual(await self.get_json("/api/settings"), before_api)
                self.assertEqual(self.persisted(), before_disk)
        self.assertTrue(self.twitch.account_available)

    async def test_valid_update_persists_and_broadcasts_settings_event(self):
        queue = self.hub.subscribe()
        self.addCleanup(self.hub.unsubscribe, queue)
        update = {
            "priority": ["Valorant", "Overwatch"],
            "exclude": ["Rust", "Apex Legends"],
            "priority_mode": PriorityMode.ENDING_SOONEST.value,
            "auto_claim": True,
            "tray_notifications": False,
        }
        response = await self.client.post("/api/settings", json=update)
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())["status"], "success")

        persisted = self.persisted()
        self.assertEqual(persisted["priority"], ["Valorant", "Overwatch"])
        self.assertEqual(persisted["exclude"], {"Rust", "Apex Legends"})
        self.assertEqual(persisted["priority_mode"], PriorityMode.ENDING_SOONEST)
        self.assertTrue(persisted["auto_claim"])
        self.assertFalse(persisted["tray_notifications"])
        self.assertEqual(persisted["auth_token"], AUTH_TOKEN)

        current = await self.get_json("/api/settings")
        self.assertEqual(current["priority"], ["Valorant", "Overwatch"])
        self.assertEqual(set(current["exclude"]), {"Rust", "Apex Legends"})
        self.assertEqual(current["priority_mode"], PriorityMode.ENDING_SOONEST.value)
        self.assertEqual(current["priority_mode_name"], "ENDING_SOONEST")
        self.assertTrue(current["auto_claim"])
        self.assertFalse(current["tray_notifications"])

        events = [e for e in self.drain(queue) if e["event"] == "settings"]
        self.assertEqual(len(events), 1)
        data = events[0]["data"]
        self.assertEqual(data["priority"], ["Valorant", "Overwatch"])
        self.assertEqual(set(data["exclude"]), {"Rust", "Apex Legends"})
        self.assertEqual(data["priority_mode"], PriorityMode.ENDING_SOONEST.value)
        self.assertTrue(data["auto_claim"])

    async def test_turning_on_auto_select_reapplies_to_every_linked_campaign(self):
        queue = self.hub.subscribe()
        self.addCleanup(self.hub.unsubscribe, queue)
        self.twitch.settings.auto_selected_campaigns = {"handled-while-on-before"}

        self.assertEqual((await self.client.post("/api/settings", json={"auto_select_linked": True})).status, 200)
        persisted = self.persisted()
        self.assertTrue(persisted["auto_select_linked"])
        self.assertEqual(persisted["auto_selected_campaigns"], set())
        self.assertTrue((await self.get_json("/api/settings"))["auto_select_linked"])
        self.assertTrue([e for e in self.drain(queue) if e["event"] == "settings"][-1]["data"]["auto_select_linked"])

        # While it stays on, other saves keep the record of handled campaigns.
        self.twitch.settings.auto_selected_campaigns.add("handled")
        await self.client.post("/api/settings", json={"auto_select_linked": True, "auto_claim": False})
        self.assertEqual(self.persisted()["auto_selected_campaigns"], {"handled"})

    async def test_choosing_a_new_game_refreshes_campaigns_but_reordering_does_not(self):
        # Discovery searches priority games' live streams, so only an added game needs a refetch.
        cases = [
            ({"priority": ["Game", "Overwatch"]}, State.GAMES_UPDATE),       # reorder
            ({"priority": ["Game"]}, State.GAMES_UPDATE),                    # remove
            ({"priority": ["GAME", "Valorant"]}, State.INVENTORY_FETCH),     # case change + new game
            ({"auto_claim": True}, State.GAMES_UPDATE),
        ]
        for update, expected in cases:
            with self.subTest(update=update):
                self.twitch._state = State.IDLE
                self.assertEqual((await self.client.post("/api/settings", json=update)).status, 200)
                self.assertIs(self.twitch._state, expected)

    async def test_partial_update_keeps_unspecified_settings(self):
        response = await self.client.post("/api/settings", json={"auto_claim": True})
        self.assertEqual(response.status, 200)
        persisted = self.persisted()
        self.assertTrue(persisted["auto_claim"])
        self.assertEqual(persisted["priority"], ["Overwatch", "Game"])
        self.assertEqual(persisted["exclude"], {"Rust"})
        self.assertEqual(persisted["priority_mode"], PriorityMode.PRIORITY_ONLY)
        self.assertTrue(persisted["tray_notifications"])

    async def test_non_object_json_body_is_rejected_with_400(self):
        self.silence_server_errors()
        for body in ("null", "5", "[]", "\"priority\""):
            with self.subTest(body=body):
                response = await self.client.post(
                    "/api/settings", data=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status, 400)

    async def test_string_false_does_not_enable_auto_claim(self):
        response = await self.client.post("/api/settings", json={"auto_claim": "false"})
        self.assertIn(response.status, (200, 400))
        self.assertFalse(self.persisted()["auto_claim"])
        self.assertFalse((await self.get_json("/api/settings"))["auto_claim"])

    async def test_unhashable_exclude_entries_do_not_half_apply_an_update(self):
        self.silence_server_errors()
        response = await self.client.post(
            "/api/settings", json={"priority": ["Valorant"], "exclude": [["nested"]]})
        self.assertIn(response.status, (200, 400))
        self.assertEqual((await self.get_json("/api/settings"))["priority"], ["Overwatch", "Game"])


class CredentialExposureTests(ApiContractTestCase):
    async def test_auth_token_and_api_key_never_appear_in_api_responses_or_logs(self):
        campaign = self.add_campaign("active")
        self.add_channel(campaign, 1)
        auth = {"Authorization": f"Bearer {API_KEY}"}
        bodies = {}
        with patch.dict("os.environ", {"API_KEY": API_KEY}):
            settings = await self.client.get("/api/settings", headers=auth)
            settings_text = await settings.text()
            self.assertTrue(json.loads(settings_text)["has_token"])  # token is configured
            bodies["settings"] = settings_text
            status = await self.client.get("/api/status", headers=auth)
            status_text = await status.text()
            self.assertTrue(json.loads(status_text)["settings"]["has_auth_token"])
            bodies["status"] = status_text
            response = await self.client.post("/api/settings", headers=auth, json={"auto_claim": True})
            bodies["settings-post"] = await response.text()
            for path in ("/api/games", "/api/inventory", "/api/channels"):
                bodies[path] = await (await self.client.get(path, headers=auth)).text()
            with patch.object(self.twitch, "gql_request", side_effect=FakeTwitchGQL([]).request):
                for path in ("/api/inventory/refresh", "/api/control/claim"):
                    response = await self.client.post(path, headers=auth)
                    self.assertEqual(response.status, 200, path)
                    bodies[path] = await response.text()
            ws = await self.client.ws_connect("/ws", params={"key": API_KEY})
            try:
                bodies["ws-init"] = json.dumps(await ws.receive_json(timeout=2))
            finally:
                await ws.close()
            bodies["logs"] = await (await self.client.get("/api/logs", headers=auth)).text()
        self.assertTrue(json.loads(bodies["logs"]))  # refresh/claim wrote to the log feed
        for name, body in bodies.items():
            with self.subTest(response=name):
                self.assertNotIn(AUTH_TOKEN, body)
                self.assertNotIn(API_KEY, body)


class WatchValidationTests(ApiContractTestCase):
    async def test_watch_rejects_non_integer_channel_ids_with_400_without_switching(self):
        campaign = self.add_campaign("active")
        first = self.add_channel(campaign, 1)
        self.add_channel(campaign, 2)
        self.twitch.watch(first)
        self.hub.progress.stop_timer()
        bodies = [
            json.dumps({"channel_id": "2"}), json.dumps({"channel_id": 2.0}),
            json.dumps({"channel_id": True}), json.dumps({"channel_id": None}),
            json.dumps({}), json.dumps([2]), json.dumps(2), "null", "{not json",
        ]
        for body in bodies:
            with self.subTest(body=body):
                response = await self.client.post(
                    "/api/control/watch", data=body, headers={"Content-Type": "application/json"})
                self.assertEqual(response.status, 400)
                self.assertIn("error", await response.json())
                self.assertEqual(self.twitch.watching_channel.get_with_default(None).id, 1)
                self.assertEqual((await self.get_json("/api/status"))["active_channel"]["id"], 1)


class ClaimEndpointTests(ApiContractTestCase):
    def ended_ready(self, name="ended-ready", drop_name="Ready reward"):
        return campaign_payload(name, end=NOW - timedelta(hours=1), minutes=60,
                                claim_id=f"instance-{name}", drop_name=drop_name)

    async def test_claim_claims_every_ready_drop_and_reports_count_and_names(self):
        gql = FakeTwitchGQL([
            self.ended_ready(),
            campaign_payload("active-ready", minutes=60, claim_id="instance-active-ready",
                             drop_name="Active reward"),
            campaign_payload("in-progress", minutes=20, claim_id=None),
            campaign_payload("already-claimed", minutes=60, claim_id="instance-claimed", claimed=True),
        ])
        with patch.object(self.twitch, "gql_request", side_effect=gql.request):
            response = await self.client.post("/api/control/claim")
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["claimed"], 2)
        self.assertEqual(sorted(result["drops"]), ["Active reward", "Ready reward"])
        self.assertIn("Ready reward", result["message"])
        self.assertEqual(sorted(gql.claimed_instances), ["instance-active-ready", "instance-ended-ready"])
        inventory = await self.get_json("/api/inventory")
        # Both claimed campaigns had a single reward, so they are finished and leave the list.
        self.assertEqual([c["id"] for c in inventory], ["in-progress"])
        status = await self.get_json("/api/status")
        self.assertEqual(status["campaigns_count"], 1)

    async def test_claim_with_nothing_ready_reports_zero_without_calling_twitch_claim(self):
        gql = FakeTwitchGQL([
            campaign_payload("in-progress", minutes=20, claim_id=None),
            campaign_payload("already-claimed", minutes=60, claim_id="instance-claimed", claimed=True),
            # Claim window (24h after campaign end) has passed.
            campaign_payload("window-expired", end=NOW - timedelta(hours=25), minutes=60,
                             claim_id="instance-expired"),
        ])
        with patch.object(self.twitch, "gql_request", side_effect=gql.request):
            response = await self.client.post("/api/control/claim")
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual(result["claimed"], 0)
        self.assertEqual(result["drops"], [])
        self.assertEqual(result["message"], "No drops currently ready to claim (in progress or already claimed)")
        self.assertEqual(gql.claimed_instances, [])
        # The pre-claim refresh ran against Twitch: only the in-progress campaign is listed.
        self.assertEqual([c["id"] for c in await self.get_json("/api/inventory")], ["in-progress"])

    async def test_rejected_twitch_claim_is_not_reported_and_reward_stays_claimable(self):
        gql = FakeTwitchGQL([self.ended_ready()], claim_status="DROP_INSTANCE_NOT_FOUND")
        with patch.object(self.twitch, "gql_request", side_effect=gql.request):
            response = await self.client.post("/api/control/claim")
        self.assertEqual(response.status, 200)
        result = await response.json()
        self.assertEqual((result["claimed"], result["drops"]), (0, []))
        self.assertEqual(gql.claimed_instances, ["instance-ended-ready"])
        inventory = await self.get_json("/api/inventory")
        self.assertEqual([c["id"] for c in inventory], ["ended-ready"])
        self.assertEqual([d["can_claim"] for d in inventory[0]["drops"]], [True])


class WebSocketInitTests(ApiContractTestCase):
    async def test_init_snapshot_matches_rest_and_live_events_follow(self):
        active = self.add_campaign("active")
        self.add_campaign("upcoming", start=NOW + timedelta(hours=2), minutes=0)
        self.add_campaign("ended-incomplete", end=NOW - timedelta(hours=1), minutes=10)  # hidden
        first = self.add_channel(active, 1)
        self.add_channel(active, 2)
        self.twitch.watch(first)
        self.hub.progress.stop_timer()  # no countdown events during the test

        status = await self.get_json("/api/status")
        inventory = await self.get_json("/api/inventory")
        channels = await self.get_json("/api/channels")

        ws = await self.client.ws_connect("/ws")
        try:
            message = await ws.receive_json(timeout=2)
            self.assertEqual(message["event"], "init")
            init = message["data"]
            for key in ("state", "status_text", "is_logged_in", "user_id", "username",
                        "auth_disconnected", "auth_pending", "active_drop", "active_channel",
                        "device_code", "channels_count", "campaigns_count", "claimable_count", "can_start_count", "websockets", "version"):
                with self.subTest(key=key):
                    self.assertEqual(init[key], status[key])
            self.assertEqual(init["campaigns_count"], 2)
            self.assertEqual(status["version"], __version__)  # Settings → About shows it
            self.assertEqual(init["inventory"], inventory)
            self.assertEqual(init["channels"], channels)
            self.assertEqual(init["settings"], {
                "priority": status["settings"]["priority"],
                "priority_mode": status["settings"]["priority_mode"],
                "auto_claim": status["settings"]["auto_claim"],
            })
            self.assertEqual(init["active_channel"]["id"], 1)

            self.hub.set_status_text("Synthetic live update")
            while True:
                event = await ws.receive_json(timeout=2)
                if event["event"] == "status":
                    break
            self.assertEqual(event["data"]["status"], "Synthetic live update")
        finally:
            await ws.close()


class GamesEndpointTests(ApiContractTestCase):
    async def test_campaign_counts_follow_visible_inventory_and_priority_is_included(self):
        self.add_campaign("alpha-active", game="Alpha", game_id="10")
        self.add_campaign("alpha-upcoming", game="Alpha", game_id="10",
                          start=NOW + timedelta(hours=2), minutes=0)
        self.add_campaign("beta-active", game="Beta", game_id="20")
        # Ended without a claimable reward: hidden from inventory, so not counted.
        self.add_campaign("gamma-ended", game="Gamma", game_id="30",
                          end=NOW - timedelta(hours=1), minutes=10)
        self.hub.set_games({Game({"id": "30", "name": "Gamma"}), Game({"id": "10", "name": "Alpha"})})

        inventory = await self.get_json("/api/inventory")
        games = await self.get_json("/api/games")
        expected_counts = {}
        for campaign in inventory:
            expected_counts[campaign["game"]] = expected_counts.get(campaign["game"], 0) + 1
        self.assertEqual(expected_counts, {"Alpha": 2, "Beta": 1})
        self.assertEqual(games["campaign_counts"], expected_counts)

        by_name = {g["name"]: g for g in games["available"]}
        self.assertEqual(len(by_name), len(games["available"]))  # no duplicate entries
        for game in games["available"]:
            with self.subTest(game=game["name"]):
                self.assertEqual(game["campaigns_count"], expected_counts.get(game["name"], 0))
        self.assertTrue(by_name["Gamma"]["discovered"])
        self.assertEqual([g["name"] for g in games["available"][:2]], ["Alpha", "Beta"])

        self.assertEqual(games["priority"], ["Overwatch", "Game"])
        self.assertEqual(games["exclude"], ["Rust"])
        status = await self.get_json("/api/status")
        self.assertEqual(status["campaigns_count"], sum(games["campaign_counts"].values()))


class CampaignChoiceEndpointTests(ApiContractTestCase):
    """/api/campaigns/ignore and /api/campaigns/focus ("Ignore" and "Mine now")."""

    async def post(self, path, body):
        response = await self.client.post(path, json=body)
        return response.status, await response.json()

    async def test_ignored_campaign_leaves_counts_but_stays_listed_and_persists(self):
        self.add_campaign("alpha", game="Alpha", game_id="10")
        self.add_campaign("beta", game="Beta", game_id="20")
        status, body = await self.post("/api/campaigns/ignore", {"campaign_id": "alpha", "ignored": True})
        self.assertEqual((status, body["ignored"]), (200, ["alpha"]))

        inventory = {c["id"]: c for c in await self.get_json("/api/inventory")}
        self.assertTrue(inventory["alpha"]["ignored"])  # still listed, for the Ignored section
        self.assertFalse(inventory["alpha"]["can_start"])
        self.assertEqual((await self.get_json("/api/games"))["campaign_counts"], {"Beta": 1})
        self.assertEqual((await self.get_json("/api/status"))["campaigns_count"], 1)
        self.assertEqual(Settings(SimpleNamespace(dump=False)).ignored_campaigns, {"alpha"})
        self.assertIs(self.twitch._state, State.GAMES_UPDATE)

        status, body = await self.post("/api/campaigns/ignore", {"campaign_id": "alpha", "ignored": False})
        self.assertEqual((status, body["ignored"]), (200, []))
        self.assertEqual((await self.get_json("/api/games"))["campaign_counts"], {"Alpha": 1, "Beta": 1})

    async def test_mine_now_validates_the_campaign_and_ignoring_it_cancels(self):
        self.add_campaign("active", minutes=10)
        self.add_campaign("soon", start=NOW + timedelta(hours=2), minutes=0)
        self.add_campaign("done", minutes=60, claimed=True, end=NOW + timedelta(days=1))
        for body, expected in (
            ({"campaign_id": "missing"}, 404),
            ({"campaign_id": "soon"}, 409),
            ({"campaign_id": "done"}, 409),
            ({"campaign_id": 7}, 400),
            ({}, 200),  # absent/null id clears the choice
        ):
            with self.subTest(body=body):
                self.assertEqual((await self.post("/api/campaigns/focus", body))[0], expected)
        self.assertEqual(self.twitch.settings.focused_campaign, "")

        status, body = await self.post("/api/campaigns/focus", {"campaign_id": "active"})
        self.assertEqual((status, body["focused"]), (200, "active"))
        inventory = {c["id"]: c for c in await self.get_json("/api/inventory")}
        self.assertTrue(inventory["active"]["focused"])
        self.assertEqual(Settings(SimpleNamespace(dump=False)).focused_campaign, "active")

        await self.post("/api/campaigns/ignore", {"campaign_id": "active", "ignored": True})
        self.assertEqual(self.twitch.settings.focused_campaign, "")
        self.assertEqual((await self.post("/api/campaigns/focus", {"campaign_id": "active"}))[0], 409)

    async def test_invalid_ignore_requests_change_nothing(self):
        for body in ({"campaign_id": "a"}, {"campaign_id": "", "ignored": True},
                     {"campaign_id": "a" * 201, "ignored": True}, {"campaign_id": "a", "ignored": "yes"}):
            with self.subTest(body=body):
                self.assertEqual((await self.post("/api/campaigns/ignore", body))[0], 400)
        self.assertEqual(self.twitch.settings.ignored_campaigns, set())

    async def test_archive_past_ignored_campaigns(self):
        # Active campaign
        self.add_campaign("active-camp", end=NOW + timedelta(days=1))
        # Expired campaign
        self.add_campaign("expired-camp", start=NOW - timedelta(days=3), end=NOW - timedelta(days=1))

        # Ignore both
        await self.post("/api/campaigns/ignore", {"campaign_id": "active-camp", "ignored": True})
        await self.post("/api/campaigns/ignore", {"campaign_id": "expired-camp", "ignored": True})
        # Also add a ghost campaign ID that no longer exists in inventory
        self.twitch.settings.ignored_campaigns.add("non-existent-camp")

        # Call archive
        status, body = await self.post("/api/campaigns/archive", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["archived_count"], 2)  # expired-camp and non-existent-camp
        self.assertEqual(body["ignored"], ["active-camp"])  # active-camp is retained
        self.assertEqual(self.twitch.settings.ignored_campaigns, {"active-camp"})


class GameCatalogEndpointTests(ApiContractTestCase):
    """/api/games/catalog and /api/games/lookup: Twitch's full game list for the Games page."""

    def fake_gql(self, responses):
        sent = []

        async def request(op):
            sent.append(op)
            key = next(k for k in ("GameSearch", "TopGames", "GameLookup") if k in op["query"])
            return responses[key](op["variables"])
        patcher = patch.object(self.twitch, "gql_request", side_effect=request)
        patcher.start()
        self.addCleanup(patcher.stop)
        return sent

    @staticmethod
    def node(name, display=None, viewers=10, *, id="1", tags=()):
        return {"id": id, "name": name.lower(), "displayName": display or name,
                "boxArtURL": f"https://cdn.example/{name}.jpg", "viewersCount": viewers,
                "tags": [{"id": t} for t in tags]}

    async def test_non_game_categories_are_left_out_of_browse_and_search(self):
        shooter = "523945d1-b36a-4aed-a0e1-7d9a7a3e94f9"  # any other content tag
        nodes = [self.node("Just Chatting", id="509658", tags=[IRL_TAG_ID]),
                 self.node("Counter-Strike", id="32399", tags=[shooter]),
                 self.node("IRL", id="509672", tags=[IRL_TAG_ID]),  # special-cased elsewhere, still not a game
                 self.node("Music", id="26936", tags=[IRL_TAG_ID, shooter]),
                 self.node("Special Events", id="509663", tags=[IRL_TAG_ID]),  # runs drop campaigns
                 self.node("Untagged Game", id="5")]
        self.fake_gql({
            "TopGames": lambda v: {"data": {"games": {
                "edges": [{"cursor": f"c{i}", "node": n} for i, n in enumerate(nodes)],
                "pageInfo": {"hasNextPage": True}}}},
            "GameSearch": lambda v: {"data": {"searchCategories": {"edges": [{"node": n} for n in nodes]}}},
        })
        games = ["Counter-Strike", "Special Events", "Untagged Game"]
        browse = await self.get_json("/api/games/catalog?first=5")
        self.assertEqual([g["name"] for g in browse["games"]], games)
        self.assertEqual(browse["next"], "c5")  # paging continues past dropped categories
        search = await self.get_json("/api/games/catalog?q=a")
        self.assertEqual([g["name"] for g in search["games"]], games)

    async def test_search_returns_display_names_and_drop_counts_without_paging(self):
        self.add_campaign("active", game="VALORANT", game_id="7")
        sent = self.fake_gql({"GameSearch": lambda v: {"data": {"searchCategories": {"edges": [
            {"node": self.node("valorant", "VALORANT", 5000)}, {"node": None},
        ]}}}})
        data = await self.get_json("/api/games/catalog?q=%20valo%20&first=5")
        self.assertEqual(data, {"next": None, "games": [{
            "id": "1", "name": "VALORANT", "box_art": "https://cdn.example/valorant.jpg",
            "viewers": 5000, "campaigns_count": 1}]})
        # The search text travels as a GQL variable, trimmed, never spliced into the query.
        self.assertEqual(sent[0]["variables"], {"query": "valo", "first": 5})
        self.assertNotIn("valo", sent[0]["query"])

    async def test_browse_pages_with_the_last_cursor_until_twitch_says_done(self):
        pages = {None: (["A", "B"], True), "c2": (["C"], False)}

        def top(variables):
            names, more = pages[variables["after"]]
            return {"data": {"games": {
                "edges": [{"cursor": f"c{i + 1}", "node": self.node(n)} for i, n in enumerate(names)],
                "pageInfo": {"hasNextPage": more}}}}
        self.fake_gql({"TopGames": top})
        first = await self.get_json("/api/games/catalog?first=2")
        self.assertEqual(([g["name"] for g in first["games"]], first["next"]), (["A", "B"], "c2"))
        second = await self.get_json("/api/games/catalog?first=2&after=c2")
        self.assertEqual(([g["name"] for g in second["games"]], second["next"]), (["C"], None))

    async def test_invalid_catalog_requests_are_rejected_before_contacting_twitch(self):
        sent = self.fake_gql({})
        for query in ("first=0", "first=61", "first=ten", "q=" + "x" * 101, "q=a&after=c1"):
            with self.subTest(query=query):
                response = await self.client.get("/api/games/catalog?" + query)
                self.assertEqual(response.status, 400)
                self.assertIn("error", await response.json())
        names = "&".join(f"name=g{i}" for i in range(51))
        self.assertEqual((await self.client.get("/api/games/lookup?" + names)).status, 400)
        self.assertEqual(sent, [])

    async def test_lookup_maps_each_name_to_its_twitch_game_or_null(self):
        sent = self.fake_gql({"GameLookup": lambda v: {"data": {
            "g0": self.node("valorant", "VALORANT"), "g1": None}}})
        data = await self.get_json("/api/games/lookup?name=Valorant&name=Gone%20Game&name=Valorant")
        self.assertEqual(data["games"]["Valorant"]["name"], "VALORANT")
        self.assertIsNone(data["games"]["Gone Game"])
        self.assertEqual(sent[0]["variables"], {"n0": "Valorant", "n1": "Gone Game"})  # deduplicated
        self.assertEqual(await self.get_json("/api/games/lookup"), {"games": {}})

    async def test_twitch_failures_and_missing_account_return_json_errors(self):
        async def boom(op):
            raise RuntimeError("upstream exploded with secret detail")
        with patch.object(self.twitch, "gql_request", side_effect=boom):
            response = await self.client.get("/api/games/catalog?q=x")
            self.assertEqual(response.status, 502)
            self.assertNotIn("secret", (await response.json())["error"])

        await self.twitch.disconnect()
        for path in ("/api/games/catalog", "/api/games/lookup?name=x"):
            with self.subTest(path=path):
                response = await self.client.get(path)
                self.assertEqual(response.status, 409)


class ControlEndpointsTests(ApiContractTestCase):
    async def test_control_pause_resume_reload_switch(self):
        # 1. Pause
        res = await self.client.post("/api/control/pause")
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"status": "success", "state": "IDLE"})
        self.assertEqual(self.twitch._state, State.IDLE)
        self.assertEqual(self.hub.status_text, "Paused by user")
        self.assertEqual(self.hub.tray_icon, "idle")

        # 2. Resume
        res = await self.client.post("/api/control/resume")
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"status": "success", "state": "INVENTORY_FETCH"})
        self.assertEqual(self.twitch._state, State.INVENTORY_FETCH)
        self.assertEqual(self.hub.status_text, "Resuming mining...")

        # 3. Reload
        res = await self.client.post("/api/control/reload")
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"status": "success", "state": "INVENTORY_FETCH"})
        self.assertEqual(self.twitch._state, State.INVENTORY_FETCH)
        self.assertEqual(self.hub.status_text, "Reloading campaigns...")

        # 4. Switch
        res = await self.client.post("/api/control/switch")
        self.assertEqual(res.status, 200)
        self.assertEqual(await res.json(), {"status": "success", "state": "CHANNEL_SWITCH"})
        self.assertEqual(self.twitch._state, State.CHANNEL_SWITCH)
        self.assertEqual(self.hub.status_text, "Switching channel...")

    async def test_control_watch_success_and_errors(self):
        campaign = self.add_campaign("active")
        channel = self.add_channel(campaign, 42, online=True)

        # Non-integer channel_id -> 400
        res = await self.client.post("/api/control/watch", json={"channel_id": "not-an-int"})
        self.assertEqual(res.status, 400)

        # Non-dict body -> 400
        res = await self.client.post("/api/control/watch", data="42", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status, 400)

        # Unknown channel -> 404
        res = await self.client.post("/api/control/watch", json={"channel_id": 9999})
        self.assertEqual(res.status, 404)

        # Ineligible channel (offline) -> 409
        channel.online = False
        res = await self.client.post("/api/control/watch", json={"channel_id": 42})
        self.assertEqual(res.status, 409)

        # Eligible channel -> 200, state switches
        channel.online = True
        res = await self.client.post("/api/control/watch", json={"channel_id": 42})
        self.assertEqual(res.status, 200)
        data = await res.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["channel_id"], 42)
        self.assertEqual(self.twitch._state, State.CHANNEL_SWITCH)

    async def test_control_claim_generates_claim_for_completed_drops_and_handles_refresh_error(self):
        campaign = self.add_campaign("active-completed")
        drop = list(campaign.drops)[0]
        drop.update_minutes(drop.required_minutes)
        drop.claim_id = None
        drop.is_claimed = False

        # Mock generate_claim and claim
        generate_called = False
        async def fake_generate_claim():
            nonlocal generate_called
            generate_called = True
            drop.claim_id = "generated-claim-id"
        drop.generate_claim = fake_generate_claim
        drop.claim = AsyncMock(return_value=True)

        # Also simulate fetch_inventory raising an exception during claim
        with patch.object(self.twitch, "fetch_inventory", side_effect=RuntimeError("Transient error")):
            res = await self.client.post("/api/control/claim")
            self.assertEqual(res.status, 200)
            self.assertTrue(generate_called)
            data = await res.json()
            self.assertEqual(data["claimed"], 1)


class WebSocketActionHandlingTests(ApiContractTestCase):
    async def test_websocket_actions_and_error_handling(self):
        ws = await self.client.ws_connect("/ws")
        try:
            # Drain init
            await ws.receive_json(timeout=2)

            # 1. Action: pause
            await ws.send_json({"action": "pause"})
            await asyncio.sleep(0.05)
            self.assertEqual(self.twitch._state, State.IDLE)

            # 2. Action: resume
            await ws.send_json({"action": "resume"})
            await asyncio.sleep(0.05)
            self.assertEqual(self.twitch._state, State.INVENTORY_FETCH)

            # 3. Action: reload
            await ws.send_json({"action": "reload"})
            await asyncio.sleep(0.05)
            self.assertEqual(self.twitch._state, State.INVENTORY_FETCH)

            # 4. Action: switch
            await ws.send_json({"action": "switch"})
            await asyncio.sleep(0.05)
            self.assertEqual(self.twitch._state, State.CHANNEL_SWITCH)

            # 5. Invalid JSON string or unknown action -> ignored safely
            await ws.send_str("{invalid json")
            await ws.send_json({"action": "unknown"})
            await asyncio.sleep(0.05)
            # Connection is still alive
            self.assertFalse(ws.closed)
        finally:
            await ws.close()


class InventoryRefreshAndAuthEndpointTests(ApiContractTestCase):
    async def test_inventory_refresh_success_and_failure(self):
        # Success
        with patch.object(self.twitch, "fetch_inventory", new_callable=AsyncMock):
            res = await self.client.post("/api/inventory/refresh")
            self.assertEqual(res.status, 200)
            data = await res.json()
            self.assertEqual(data["status"], "success")
            self.assertIn("campaigns", data)

        # Failure when fetch_inventory raises Exception
        with patch.object(self.twitch, "fetch_inventory", side_effect=RuntimeError("GQL failure")):
            res = await self.client.post("/api/inventory/refresh")
            self.assertEqual(res.status, 500)
            data = await res.json()
            self.assertEqual(data["status"], "error")
            self.assertIn("GQL failure", data["message"])

    async def test_auth_token_validation_and_errors(self):
        # Non-JSON -> 400
        res = await self.client.post("/api/auth/token", data="not json", headers={"Content-Type": "application/json"})
        self.assertEqual(res.status, 400)

        # Missing token or empty -> 400
        res = await self.client.post("/api/auth/token", json={})
        self.assertEqual(res.status, 400)
        res = await self.client.post("/api/auth/token", json={"token": "   "})
        self.assertEqual(res.status, 400)

        # When twitch.connect raises -> 500
        with patch.object(self.twitch, "connect", side_effect=RuntimeError("Auth failure")):
            res = await self.client.post("/api/auth/token", json={"token": "valid_format_token"})
            self.assertEqual(res.status, 500)

    async def test_auth_connect_and_disconnect_endpoints(self):
        # connect failure -> 500
        with patch.object(self.twitch, "connect", side_effect=RuntimeError("Connect fail")):
            res = await self.client.post("/api/auth/connect")
            self.assertEqual(res.status, 500)

        # connect success -> 200
        with patch.object(self.twitch, "connect", new_callable=AsyncMock):
            res = await self.client.post("/api/auth/connect")
            self.assertEqual(res.status, 200)
            self.assertEqual((await res.json())["message"], "Twitch login started")

        # disconnect failure -> 500
        with patch.object(self.twitch, "disconnect", side_effect=RuntimeError("Disconnect fail")):
            res = await self.client.post("/api/auth/disconnect")
            self.assertEqual(res.status, 500)

        # disconnect success -> 200
        with patch.object(self.twitch, "disconnect", new_callable=AsyncMock):
            res = await self.client.post("/api/auth/disconnect")
            self.assertEqual(res.status, 200)
            self.assertEqual((await res.json())["message"], "Disconnected from Twitch")


class ServerLifecycleAndStaticTests(ApiContractTestCase):
    async def test_account_middleware_cancelled_when_account_unavailable(self):
        # Test line 83 in _account_middleware: CancelledError when account becomes unavailable
        @asynccontextmanager
        async def cancel_op():
            self.twitch.settings.auth_disconnected = True
            raise asyncio.CancelledError()
            yield

        with patch.object(self.twitch, "account_operation", side_effect=cancel_op):
            res = await self.client.post("/api/control/pause")
            self.assertEqual(res.status, 409)
            self.assertIn("cancelled", (await res.json())["error"])

    async def test_serve_index_fallback_when_file_missing(self):
        import api
        with patch.object(api.Path, "exists", return_value=False):
            res = await self.client.get("/")
            self.assertEqual(res.status, 200)
            text = await res.text()
            self.assertIn("Web Dashboard assets loading...", text)

    async def test_server_start_port_fallback_and_stop_cleanup(self):
        server = APIServer(self.twitch, self.hub, host="127.0.0.1", port=34567)
        start_count = 0

        orig_start = web.TCPSite.start
        async def mock_start(site_self):
            nonlocal start_count
            start_count += 1
            if start_count == 1:
                raise OSError("Port already in use")
            return await orig_start(site_self)

        with patch.object(web.TCPSite, "start", mock_start):
            await server.start()
            self.assertEqual(start_count, 2)
            # Test stop cleanup with simulated ws clients
            mock_ws = AsyncMock()
            server._ws_clients.add(mock_ws)
            await server.stop()
            mock_ws.close.assert_awaited()


if __name__ == "__main__":
    unittest.main()
