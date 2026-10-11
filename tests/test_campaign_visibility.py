import copy
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from api import APIServer
from constants import PriorityMode
from inventory import DropsCampaign
from twitch import Twitch


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class CampaignVisibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.clock = patch("inventory.datetime", Clock)
        self.clock.start()
        self.auth = patch.dict("os.environ", {"API_KEY": ""})
        self.auth.start()
        settings = SimpleNamespace(priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY,
                                   auth_token="", auto_claim=False, enable_badges_emotes=False, dump=False)
        self.twitch = Twitch(settings)
        self.hub = self.twitch.gui
        self.client = TestClient(TestServer(APIServer(self.twitch, self.hub)._app))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.hub.progress.stop_timer()
        if self.twitch._mnt_task is not None:
            self.twitch._mnt_task.cancel()
        logging.getLogger("TwitchDrops").removeHandler(self.hub._log_handler)
        self.clock.stop()
        self.auth.stop()

    def campaign(self, name, *, end=NOW - timedelta(hours=1), start=NOW - timedelta(days=1),
                 minutes=60, claim_id="ready", claimed=False, linked=True):
        data = {
            "id": name, "name": name, "game": {"id": "1", "name": "Game", "boxArtURL": ""},
            "self": {"isAccountConnected": linked}, "accountLinkURL": "",
            "startAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endAt": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "ACTIVE" if end > NOW else "EXPIRED",
            "allow": {"channels": []},
            "timeBasedDrops": [{
                "id": "reward", "name": "Reward", "startAt": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endAt": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "benefitEdges": [{"benefit": {"id": "benefit", "name": "Reward",
                    "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": ""}}],
                "preconditionDrops": [], "requiredMinutesWatched": 60,
                "self": {"dropInstanceID": claim_id, "isClaimed": claimed, "currentMinutesWatched": minutes},
            }],
        }
        self.last_campaign_data = data
        campaign = DropsCampaign(self.twitch, data, {})
        self.twitch.inventory.append(campaign)
        self.twitch.wanted_games = [campaign.game]
        self.twitch._drops.update(campaign.timed_drops)
        self.hub.update_inventory_campaign(campaign)
        return campaign

    async def inventory(self):
        response = await self.client.get("/api/inventory")
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_only_actionable_campaigns_are_listed_and_counted(self):
        self.campaign("ended-incomplete", minutes=20, claim_id=None)
        self.campaign("ended-claimed", claimed=True)
        self.campaign("ended-unlinked", linked=False)
        self.campaign("claim-window-expired", end=NOW - timedelta(hours=24))
        self.campaign("ended-ready")
        self.campaign("active", end=NOW + timedelta(days=1), minutes=20, claim_id=None)
        self.campaign("upcoming", start=NOW + timedelta(hours=1), end=NOW + timedelta(days=1), minutes=0, claim_id=None)
        self.campaign("active-completed", end=NOW + timedelta(days=1), claimed=True)
        self.assertEqual([c["id"] for c in await self.inventory()], ["ended-ready", "active", "upcoming"])
        response = await self.client.get("/api/status")
        self.assertEqual((await response.json())["campaigns_count"], 3)
        response = await self.client.get("/api/games")
        self.assertEqual((await response.json())["campaign_counts"], {"Game": 3})

    async def test_last_claim_removes_ended_campaign_from_live_inventory(self):
        campaign = self.campaign("ended-ready")
        q = self.hub.subscribe()
        drop = campaign.timed_drops["reward"]
        drop.is_claimed = True
        self.hub.update_inventory_drop(drop)
        event = await q.get()
        self.assertEqual(event["event"], "inventory")
        self.assertEqual(event["data"], [])
        self.assertEqual(await self.inventory(), [])
        self.hub.unsubscribe(q)

    async def test_claim_deadline_is_rechecked_without_an_inventory_reload(self):
        self.campaign("ended-ready", end=NOW - timedelta(hours=23))
        self.assertEqual([c["id"] for c in await self.inventory()], ["ended-ready"])
        Clock.current = NOW + timedelta(hours=1)
        self.assertEqual(await self.inventory(), [])

    async def test_ended_campaign_lists_only_rewards_that_can_still_be_claimed(self):
        campaign = self.campaign("ended-ready")
        original = campaign.timed_drops["reward"]
        claimed = copy.copy(original)
        claimed.id = "claimed"
        claimed.is_claimed = True
        incomplete = copy.copy(original)
        incomplete.id = "incomplete"
        incomplete.claim_id = None
        incomplete.real_current_minutes = 20
        campaign.timed_drops.update(claimed=claimed, incomplete=incomplete)
        self.hub.update_inventory_campaign(campaign)
        self.assertEqual([d["id"] for d in (await self.inventory())[0]["drops"]], ["reward"])

    def channel(self, campaign, channel_id=1):
        channel = SimpleNamespace(id=channel_id, name=f"streamer{channel_id}", viewers=10,
                                  online=True, game=campaign.game, drops_enabled=True)
        self.twitch.channels[channel_id] = channel
        self.hub.upsert_channel(channel)
        return channel

    async def test_refresh_rebinds_home_target_to_new_inventory_objects(self):
        campaign = self.campaign("active", end=NOW + timedelta(days=1), minutes=20, claim_id=None)
        channel = self.channel(campaign)
        self.twitch.watch(channel)
        self.hub.display_drop(campaign.timed_drops["reward"])
        previous_status = self.hub.status_text
        data = copy.deepcopy(self.last_campaign_data)
        data["id"] = "new-active"
        data["name"] = "new-active"
        data["timeBasedDrops"][0]["id"] = "new-reward"
        data["timeBasedDrops"][0]["self"]["currentMinutesWatched"] = 35
        replies = [
            {"data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [data], "gameEventDrops": []}}}},
            {"data": {"currentUser": {"dropCampaigns": []}}},
            # the empty list triggers stream discovery; nothing is live
            {"data": {"streams": {"edges": [], "pageInfo": {"hasNextPage": False}}}},
        ]
        with patch.object(self.twitch, "gql_request", AsyncMock(side_effect=replies)):
            response = await self.client.post("/api/inventory/refresh")
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/status")
        status = await response.json()
        self.assertEqual(status["active_drop"]["current_minutes"], 35)
        self.assertEqual(status["active_drop"]["id"], "new-reward")
        self.assertEqual(status["active_drop"]["campaign_name"], "new-active")
        self.assertEqual(status["status_text"], previous_status)

    async def test_watch_selects_channel_and_updates_home_target(self):
        campaign = self.campaign("active", end=NOW + timedelta(days=1), minutes=20, claim_id=None)
        first = self.channel(campaign, 1)
        self.channel(campaign, 2)
        self.twitch.watch(first)
        response = await self.client.post("/api/control/watch", json={"channel_id": 2})
        self.assertEqual(response.status, 200)
        response = await self.client.get("/api/status")
        status = await response.json()
        self.assertEqual(status["active_channel"]["id"], 2)
        self.assertEqual(status["active_drop"]["campaign_name"], "active")
        response = await self.client.get("/api/channels")
        self.assertEqual([c["id"] for c in await response.json() if c["watching"]], [2])

    async def test_watch_rejects_offline_and_unknown_channels_without_switching(self):
        campaign = self.campaign("active", end=NOW + timedelta(days=1), minutes=20, claim_id=None)
        first = self.channel(campaign, 1)
        second = self.channel(campaign, 2)
        second.online = False
        self.twitch.watch(first)
        for channel_id, expected in [(2, 409), (999, 404)]:
            with self.subTest(channel_id=channel_id):
                response = await self.client.post("/api/control/watch", json={"channel_id": channel_id})
                self.assertEqual(response.status, expected)
                self.assertEqual(self.twitch.watching_channel.get_with_default(None).id, 1)


if __name__ == "__main__":
    unittest.main()
