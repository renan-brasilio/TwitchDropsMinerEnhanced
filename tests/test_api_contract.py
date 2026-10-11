"""REST/WebSocket contract and remote-security regression tests for api.py."""
import copy
import json
import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from api import APIServer
from constants import PriorityMode
from inventory import DropsCampaign
from settings import Settings
from twitch import Twitch
from utils import Game


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
        self.client = TestClient(TestServer(APIServer(self.twitch, self.hub)._app))
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

    def add_channel(self, campaign, channel_id):
        channel = SimpleNamespace(id=channel_id, name=f"streamer{channel_id}", viewers=10,
                                  online=True, game=campaign.game, drops_enabled=True)
        self.twitch.channels[channel_id] = channel
        self.hub.upsert_channel(channel)
        return channel

    def persisted(self):
        s = Settings(SimpleNamespace(dump=False))
        return {
            "priority": s.priority, "exclude": set(s.exclude), "priority_mode": s.priority_mode,
            "auto_claim": s.auto_claim, "tray_notifications": s.tray_notifications,
            "auth_token": s.auth_token, "auth_disconnected": s.auth_disconnected, "api_port": s.api_port,
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
                        "device_code", "channels_count", "campaigns_count", "websockets"):
                with self.subTest(key=key):
                    self.assertEqual(init[key], status[key])
            self.assertEqual(init["campaigns_count"], 2)
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


if __name__ == "__main__":
    unittest.main()
