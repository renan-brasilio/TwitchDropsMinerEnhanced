"""Campaign discovery when Twitch hides the campaign list (risk rows 1 and 2).

Since Oct 2026 Twitch returns an empty ViewerDropsDashboard to the device-code (TV) client,
and the web client fails its integrity check. The miner then finds campaigns through live
Drops Enabled streams and each channel's AvailableDrops; Twitch is faked at gql_request.
"""
import logging
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from constants import PriorityMode
from exceptions import GQLException
from twitch import Twitch

NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def game(gid, name):
    return {"id": gid, "name": name.lower(), "displayName": name, "slug": name.lower().replace(" ", "-"),
            "boxArtURL": f"https://cdn.example/{gid}-285x380.jpg"}


RIVALS, VALORANT, EVENTS = game("1", "Marvel Rivals"), game("2", "VALORANT"), game("3", "Special Events")


def available(cid, g, *, drops=1, end=NOW + timedelta(days=3), start=NOW - timedelta(days=1)):
    """A campaign as DropsHighlightService_AvailableDrops returns it (no self/allow/status)."""
    return {
        "id": cid, "name": f"  {cid} campaign\t", "game": {"id": g["id"], "name": g["name"]},
        "detailsURL": "https://publisher.example/link", "endAt": iso(end),
        "timeBasedDrops": [{
            "id": f"{cid}-d{i}", "name": f"Reward {i}\t\t", "startAt": iso(start), "endAt": iso(end),
            "requiredMinutesWatched": 30 * (i + 1),
            "benefitEdges": [{"benefit": {"id": f"{cid}-b{i}", "name": f"Reward {i}", "imageAssetURL": ""}}],
        } for i in range(drops)],
    }


def in_progress(cid, g, minutes):
    """A campaign as the Inventory query returns it: complete, with account-specific self edges."""
    return {
        "id": cid, "name": "Real name", "game": g, "self": {"isAccountConnected": True},
        "accountLinkURL": "https://publisher.example/link", "startAt": iso(NOW - timedelta(days=1)),
        "endAt": iso(NOW + timedelta(days=3)), "status": "ACTIVE", "allow": {"isEnabled": False, "channels": None},
        "timeBasedDrops": [{
            "id": f"{cid}-d0", "name": "Reward 0", "startAt": iso(NOW - timedelta(days=1)),
            "endAt": iso(NOW + timedelta(days=3)), "requiredMinutesWatched": 30, "preconditionDrops": None,
            "benefitEdges": [{"benefit": {"id": f"{cid}-b0", "name": "Reward 0",
                                          "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": ""}}],
            "self": {"currentMinutesWatched": minutes, "dropInstanceID": None, "isClaimed": False},
        }],
    }


def stream(channel_id, g):
    return {"cursor": f"c{channel_id}", "node": {"broadcaster": {"id": channel_id, "login": f"ch{channel_id}"}, "game": g}}


class FakeTwitch:
    """Answers the GQL operations used by fetch_inventory + discovery."""

    def __init__(self):
        self.campaign_list = []          # ViewerDropsDashboard
        self.campaign_error = None       # GQLException to raise for it
        self.inventory = []              # Inventory in-progress campaigns
        self.priority_streams = {}       # game name -> [channel ids]
        self.stream_pages = [[]]         # global Drops Enabled stream pages
        self.channel_campaigns = {}      # channel id -> [available campaigns]
        self.games_by_name = {g["displayName"].casefold(): g for g in (RIVALS, VALORANT, EVENTS)}
        self.queries = []

    async def request(self, ops):
        if isinstance(ops, list):
            return [await self.request(op) for op in ops]
        name = ops.get("operationName")
        if name == "Inventory":
            return {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": self.inventory, "gameEventDrops": []}}}}
        if name == "ViewerDropsDashboard":
            if self.campaign_error:
                raise self.campaign_error
            return {"data": {"currentUser": {"dropCampaigns": self.campaign_list}}}
        if name == "DropsHighlightService_AvailableDrops":
            cid = ops["variables"]["channelID"]
            return {"data": {"channel": {"id": cid, "viewerDropCampaigns": self.channel_campaigns.get(cid)}}}
        query = ops["query"]
        self.queries.append(query)
        if "DropStreams" in query:
            after = ops["variables"]["after"]
            index = 0 if after is None else 1 + next(
                i for i, page in enumerate(self.stream_pages) if page and page[-1]["cursor"] == after)
            page = self.stream_pages[index]
            return {"data": {"streams": {"edges": page, "pageInfo": {"hasNextPage": index + 1 < len(self.stream_pages)}}}}
        if "query Games(" in query:
            out = {}
            for key, value in ops["variables"].items():
                g = self.games_by_name.get(value.casefold())
                if g is not None:
                    g = dict(g)
                    if "streams(" in query:
                        g["streams"] = {"edges": [stream(c, None) for c in self.priority_streams.get(g["displayName"], [])]}
                out["g" + key[1:]] = g
            return {"data": out}
        raise AssertionError(f"unexpected GQL: {ops}")


class CampaignDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.patches = [patch("inventory.datetime", Clock), patch("twitch.datetime", Clock)]
        for p in self.patches:
            p.start()
        self.settings = SimpleNamespace(
            priority=["marvel rivals"], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY,
            auth_token="", auth_disconnected=False, auto_claim=False, enable_badges_emotes=False,
            dump=False, available_drops_check=False)
        self.twitch = Twitch(self.settings)
        self.fake = FakeTwitch()
        gql = patch.object(self.twitch, "gql_request", side_effect=self.fake.request)
        gql.start()
        self.patches.append(gql)

    async def asyncTearDown(self):
        if self.twitch._mnt_task is not None:
            self.twitch._mnt_task.cancel()
        self.twitch.gui.progress.stop_timer()
        logging.getLogger("TwitchDrops").removeHandler(self.twitch.gui._log_handler)
        for p in reversed(self.patches):
            p.stop()

    def campaigns(self):
        return {c.id: c for c in self.twitch.inventory}

    async def test_empty_campaign_list_falls_back_to_live_drops_enabled_streams(self):
        # Priority game streams are searched by name, plus pages of streams across Twitch.
        self.fake.priority_streams = {"Marvel Rivals": ["10"]}
        self.fake.stream_pages = [[stream("20", VALORANT)], [stream("21", VALORANT)]]
        self.fake.channel_campaigns = {"10": [available("rivals", RIVALS, drops=2)],
                                       "20": [available("val", VALORANT)], "21": [available("val", VALORANT)]}
        await self.twitch.fetch_inventory()

        found = self.campaigns()
        self.assertEqual(set(found), {"rivals", "val"})
        rivals = found["rivals"]
        self.assertEqual(rivals.name, "rivals campaign")             # Twitch pads names with tabs
        self.assertEqual(rivals.game.name, "Marvel Rivals")
        self.assertEqual([d.required_minutes for d in rivals.drops], [30, 60])
        self.assertEqual([d.name for d in rivals.drops], ["Reward 0", "Reward 1"])
        self.assertTrue(rivals.active)
        self.assertTrue(rivals.link_unknown)
        self.assertEqual(rivals.link_url, "https://publisher.example/link")

    async def test_unknown_link_campaigns_are_eligible_only_for_priority_games(self):
        self.fake.stream_pages = [[stream("10", RIVALS), stream("20", VALORANT)]]
        self.fake.channel_campaigns = {"10": [available("rivals", RIVALS)], "20": [available("val", VALORANT)]}
        await self.twitch.fetch_inventory()
        found = self.campaigns()

        self.assertTrue(found["rivals"].eligible)   # "marvel rivals" is chosen, case-insensitively
        self.assertTrue(found["rivals"].can_earn_within(NOW + timedelta(hours=1)))
        self.assertFalse(found["val"].eligible)      # listed, but never mined
        self.assertFalse(found["val"].can_earn_within(NOW + timedelta(hours=1)))

        self.settings.priority = ["Marvel Rivals", "VALORANT"]  # choosing it on the Games page
        self.assertTrue(found["val"].eligible)
        self.settings.priority = []
        self.assertFalse(found["rivals"].eligible)

    async def test_in_progress_inventory_data_wins_over_discovered_data(self):
        self.fake.inventory = [in_progress("rivals", RIVALS, minutes=12)]
        self.fake.stream_pages = [[stream("10", RIVALS)]]
        self.fake.channel_campaigns = {"10": [available("rivals", RIVALS, drops=3)]}
        await self.twitch.fetch_inventory()

        rivals = self.campaigns()["rivals"]
        self.assertFalse(rivals.link_unknown)
        self.assertTrue(rivals.linked)
        self.assertEqual(rivals.name, "Real name")
        self.assertEqual([d.current_minutes for d in rivals.drops], [12])

    async def test_campaign_for_another_game_is_resolved_and_unminable_ones_are_skipped(self):
        # An event campaign offered on a VALORANT stream belongs to "Special Events".
        self.fake.stream_pages = [[stream("20", VALORANT)]]
        self.fake.channel_campaigns = {"20": [
            available("event", EVENTS),
            available("ended", VALORANT, end=NOW - timedelta(minutes=1)),
            available("event-based-only", VALORANT, drops=0),
            available("soon", VALORANT, start=NOW + timedelta(hours=5)),
        ]}
        await self.twitch.fetch_inventory()
        found = self.campaigns()

        self.assertEqual(set(found), {"event", "soon"})
        self.assertEqual(found["event"].game.name, "Special Events")
        self.assertTrue(found["soon"].upcoming)
        self.assertTrue(any("query Games(" in q and "streams(" not in q for q in self.fake.queries))

    async def test_streams_are_capped_per_game_and_pages_stop_when_twitch_has_no_more(self):
        self.settings.priority = []
        self.fake.stream_pages = [[stream(str(100 + i), VALORANT) for i in range(5)]]
        await self.twitch.fetch_inventory()
        asked = [q for q in self.fake.queries if "DropStreams" in q]
        self.assertEqual(len(asked), 1)  # hasNextPage false after the first page

        channels_checked = []
        original = self.fake.request

        async def spy(ops):
            if isinstance(ops, list):
                channels_checked.extend(op["variables"]["channelID"] for op in ops)
            return await original(ops)
        with patch.object(self.twitch, "gql_request", side_effect=spy):
            await self.twitch.fetch_inventory()
        self.assertEqual(len(channels_checked), 3)  # DISCOVERY_CHANNELS_PER_GAME

    async def test_integrity_check_failure_falls_back_but_other_errors_still_raise(self):
        self.fake.campaign_error = GQLException([{"message": "failed integrity check",
                                                   "extensions": {"code": "IntegrityCheckFailed"}}])
        self.fake.stream_pages = [[stream("10", RIVALS)]]
        self.fake.channel_campaigns = {"10": [available("rivals", RIVALS)]}
        await self.twitch.fetch_inventory()
        self.assertEqual(set(self.campaigns()), {"rivals"})

        self.fake.campaign_error = GQLException("service unavailable for good")
        with self.assertRaises(GQLException):
            await self.twitch.fetch_inventory()

    async def test_a_returned_campaign_list_is_used_without_stream_discovery(self):
        detail = in_progress("listed", VALORANT, minutes=0)
        self.fake.campaign_list = [{key: detail[key] for key in (
            "id", "name", "game", "self", "startAt", "endAt", "status", "accountLinkURL")}]

        async def details(chunk):
            return {cid: detail for cid, _ in chunk}
        with patch.object(self.twitch, "fetch_campaigns", side_effect=details):
            await self.twitch.fetch_inventory()
        self.assertEqual(set(self.campaigns()), {"listed"})
        self.assertFalse(self.campaigns()["listed"].link_unknown)
        self.assertEqual([q for q in self.fake.queries if "DropStreams" in q], [])


if __name__ == "__main__":
    unittest.main()
