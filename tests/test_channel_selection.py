"""Risk row 2: wanted-game ordering, channel eligibility and switching rules in twitch.py."""
import asyncio
import logging
import unittest
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from channel import Channel
from constants import MAX_INT, PriorityMode, State
from inventory import DropsCampaign
from twitch import Twitch


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ALPHA = {"id": "101", "name": "Alpha", "boxArtURL": ""}
BETA = {"id": "202", "name": "Beta", "boxArtURL": ""}
GAMMA = {"id": "303", "name": "Gamma", "boxArtURL": ""}
SPECIAL = {"id": "509663", "name": "Special Events", "boxArtURL": ""}


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class ObservedStateChange(asyncio.Event):
    """The miner's state-change flag; `blocked` is set once the state loop waits for input."""

    def __init__(self):
        super().__init__()
        self.blocked = asyncio.Event()

    async def wait(self):
        if not self.is_set():
            self.blocked.set()
        return await super().wait()


def ts(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def campaign_data(game, *, end=NOW + timedelta(days=1), start=NOW - timedelta(days=1),
                  required=60, watched=0, linked=True, acl=()):
    return {
        "id": f"campaign-{game['id']}-{len(acl)}", "name": f"{game['name']} drops", "game": dict(game),
        "self": {"isAccountConnected": linked}, "accountLinkURL": "",
        "startAt": ts(start), "endAt": ts(end), "status": "ACTIVE",
        "allow": {"channels": [{"id": str(cid), "name": f"acl{cid}", "displayName": f"ACL{cid}"}
                               for cid in acl] or None, "isEnabled": True},
        "timeBasedDrops": [{
            "id": f"drop-{game['id']}-{len(acl)}", "name": "Reward",
            "startAt": ts(start), "endAt": ts(end),
            "benefitEdges": [{"benefit": {"id": f"benefit-{game['id']}", "name": "Reward",
                                          "distributionType": "DIRECT_ENTITLEMENT",
                                          "imageAssetURL": ""}}],
            "preconditionDrops": None, "requiredMinutesWatched": required,
            "self": {"dropInstanceID": None, "isClaimed": False, "currentMinutesWatched": watched},
        }],
    }


def directory_node(channel_id, game, viewers):
    """One `DirectoryPage_Game` stream edge node, as Twitch returns it."""
    return {
        "id": str(80000 + channel_id), "game": dict(game), "viewersCount": viewers, "title": "live",
        "broadcaster": {"id": str(channel_id), "login": f"streamer{channel_id}",
                        "displayName": f"Streamer{channel_id}"},
    }


def stream_info(channel_id, game=None, viewers=10):
    """A `VideoPlayerStreamInfoOverlayChannel` user payload; offline when `game` is None."""
    return {
        "id": str(channel_id), "displayName": f"ACL{channel_id}",
        "stream": None if game is None else {"id": str(70000 + channel_id), "viewersCount": viewers},
        "broadcastSettings": {"game": dict(game) if game else None, "title": "live"},
    }


class SelectionTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.patchers = [patch("inventory.datetime", Clock), patch("twitch.datetime", Clock)]
        for patcher in self.patchers:
            patcher.start()
        self.settings = SimpleNamespace(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, available_drops_check=False, dump=False,
            save=lambda force=False: None,
        )
        self.twitch = Twitch(self.settings)
        self.twitch.gui.send_notification = Mock()

    async def asyncTearDown(self):
        self.twitch.gui.progress.stop_timer()
        logging.getLogger("TwitchDrops").removeHandler(self.twitch.gui._log_handler)
        for patcher in reversed(self.patchers):
            patcher.stop()

    def add_campaign(self, game, **kwargs):
        campaign = DropsCampaign(self.twitch, campaign_data(game, **kwargs), {})
        self.twitch.inventory.append(campaign)
        self.twitch._campaigns[campaign.id] = campaign
        self.twitch._drops.update(campaign.timed_drops)
        return campaign

    def directory_channel(self, channel_id, game, *, viewers=10, drops_enabled=True):
        return Channel.from_directory(
            self.twitch, directory_node(channel_id, game, viewers), drops_enabled=drops_enabled)

    def acl_channel(self, campaign, channel_id, game=None, viewers=10):
        channel = next(c for c in campaign.allowed_channels if c.id == channel_id)
        channel.external_update(stream_info(channel_id, game, viewers), [])
        return channel

    def want(self, *games):
        self.twitch.wanted_games[:] = [DropsCampaign(self.twitch, campaign_data(g), {}).game
                                       for g in games]


class WatchEligibilityTests(SelectionTestCase):
    async def test_live_drops_enabled_channel_of_wanted_game_is_watchable(self):
        self.add_campaign(ALPHA)
        self.want(ALPHA)
        self.assertTrue(self.twitch.can_watch(self.directory_channel(1, ALPHA)))

    async def test_offline_channel_is_never_watchable(self):
        campaign = self.add_campaign(ALPHA, acl=[1])
        self.add_campaign(SPECIAL)  # earnable on any channel, but still only while it is live
        self.want(ALPHA)
        self.assertFalse(self.twitch.can_watch(self.acl_channel(campaign, 1, game=None)))
        self.assertFalse(self.twitch.can_watch(Channel(self.twitch, id=9, login="offline")))

    async def test_channel_without_drops_enabled_is_not_watchable(self):
        self.add_campaign(ALPHA)
        self.want(ALPHA)
        self.assertFalse(self.twitch.can_watch(self.directory_channel(1, ALPHA, drops_enabled=False)))

    async def test_channel_of_unwanted_game_is_not_watchable_even_with_earnable_campaign(self):
        self.add_campaign(ALPHA)
        self.add_campaign(BETA)
        self.want(BETA)
        self.assertFalse(self.twitch.can_watch(self.directory_channel(1, ALPHA)))

    async def test_channel_streaming_game_without_earnable_campaign_is_not_watchable(self):
        self.add_campaign(ALPHA, linked=False)
        self.want(ALPHA)
        self.assertFalse(self.twitch.can_watch(self.directory_channel(1, ALPHA)))

    async def test_acl_campaign_is_watchable_only_on_listed_channels(self):
        campaign = self.add_campaign(ALPHA, acl=[7])
        self.want(ALPHA)
        self.assertTrue(self.twitch.can_watch(self.acl_channel(campaign, 7, ALPHA)))
        self.assertFalse(self.twitch.can_watch(self.directory_channel(1, ALPHA)))

    async def test_special_event_campaign_is_watchable_on_any_live_channel(self):
        self.add_campaign(SPECIAL)
        self.assertTrue(self.twitch.can_watch(self.directory_channel(1, GAMMA, drops_enabled=False)))


class SwitchingRuleTests(SelectionTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        for game in (ALPHA, BETA, GAMMA):
            self.add_campaign(game)
        self.want(ALPHA, BETA, GAMMA)

    async def test_priority_follows_wanted_game_order_and_ranks_unwanted_or_offline_last(self):
        offline = Channel(self.twitch, id=9, login="offline")
        self.assertEqual(
            [self.twitch.get_priority(c) for c in (
                self.directory_channel(1, ALPHA), self.directory_channel(2, GAMMA),
                self.directory_channel(3, SPECIAL), offline)],
            [0, 2, MAX_INT, MAX_INT],
        )

    async def test_switches_only_to_a_strictly_higher_priority_game(self):
        self.twitch.watch(self.directory_channel(2, BETA))
        cases = {"higher": (ALPHA, True), "same": (BETA, False), "lower": (GAMMA, False)}
        for label, (game, expected) in cases.items():
            with self.subTest(label):
                self.assertIs(self.twitch.should_switch(self.directory_channel(5, game, viewers=999)),
                              expected)

    async def test_equal_priority_switch_only_moves_from_directory_to_acl_channel(self):
        acl_campaign = self.add_campaign(ALPHA, acl=[7])
        acl = self.acl_channel(acl_campaign, 7, ALPHA)
        directory = self.directory_channel(1, ALPHA, viewers=999)

        self.twitch.watch(directory)
        self.assertTrue(self.twitch.should_switch(acl))
        self.twitch.watch(acl)
        self.assertFalse(self.twitch.should_switch(directory))

    async def test_any_watchable_channel_replaces_watched_channel_that_became_ineligible(self):
        alpha_campaign = self.twitch.inventory[0]
        self.twitch.watch(self.directory_channel(1, ALPHA))
        # Alpha's last drop gets claimed; Alpha stays the top wanted game until the next refresh.
        next(iter(alpha_campaign.drops)).is_claimed = True
        self.assertTrue(self.twitch.should_switch(self.directory_channel(3, GAMMA)))

    async def test_unwatchable_channel_is_never_a_switch_candidate(self):
        self.assertFalse(self.twitch.should_switch(Channel(self.twitch, id=9, login="offline")))
        self.assertFalse(self.twitch.should_switch(
            self.directory_channel(4, ALPHA, drops_enabled=False)))
        self.assertTrue(self.twitch.should_switch(self.directory_channel(4, ALPHA)))


class MiningLoopSelectionTests(SelectionTestCase):
    """Drives the real state machine from inventory to channel switch with Twitch virtualized."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.directory: dict[str, list[dict]] = {}
        self.acl_streams: dict[str, dict] = {}
        twitch = self.twitch
        twitch.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=42))
        twitch.fetch_inventory = AsyncMock()  # inventory is pre-built by each test
        twitch.gql_request = self.fake_gql
        twitch._watch_loop = AsyncMock()  # no watch payloads leave the process
        twitch.websocket.start = AsyncMock()
        twitch.websocket.add_topics = Mock()
        twitch.websocket.remove_topics = Mock()
        twitch._state_change = ObservedStateChange()
        self.run_task = None

    async def asyncTearDown(self):
        if self.run_task is not None:
            self.run_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.run_task
        if self.twitch._watching_task is not None:
            self.twitch._watching_task.cancel()
        await super().asyncTearDown()

    async def fake_gql(self, ops):
        if isinstance(ops, list):
            return [await self.fake_gql(op) for op in ops]
        name, variables = ops["operationName"], ops["variables"]
        if name == "DirectoryPage_Game":
            edges = [{"node": node} for node in self.directory.get(variables["slug"], [])]
            return {"data": {"game": {"streams": {"edges": edges}}}}
        if name == "VideoPlayerStreamInfoOverlayChannel":
            return {"data": {"user": self.acl_streams.get(variables["channel"])}}
        raise AssertionError(f"unexpected GQL operation {name}")

    def live(self, game, *channels):
        """Twitch directory for `game`: (channel_id, viewers) pairs."""
        self.directory[game["name"].lower()] = [directory_node(cid, game, v) for cid, v in channels]

    async def select(self):
        self.run_task = asyncio.create_task(self.twitch._run())
        await asyncio.wait_for(self.twitch._state_change.blocked.wait(), 5)
        if self.run_task.done():
            self.run_task.result()  # surface crashes of the state loop
        watching = self.twitch.watching_channel.get_with_default(None)
        return [g.name for g in self.twitch.wanted_games], watching and watching.id

    async def test_priority_list_order_beats_viewer_count_and_skips_unlisted_games(self):
        for game in (ALPHA, BETA, GAMMA):
            self.add_campaign(game)
        self.live(ALPHA, (1, 1000))
        self.live(BETA, (2, 5))
        self.live(GAMMA, (3, 9999))
        self.settings.priority = ["Beta", "Alpha"]

        self.assertEqual(await self.select(), (["Beta", "Alpha"], 2))

    async def test_priority_only_with_empty_list_mines_every_earnable_game(self):
        for game in (ALPHA, BETA):
            self.add_campaign(game)
        self.live(BETA, (2, 5))

        wanted, watching = await self.select()
        self.assertEqual(sorted(wanted), ["Alpha", "Beta"])
        self.assertEqual(watching, 2)

    async def test_excluded_game_is_never_watched_even_if_prioritized_and_only_one_live(self):
        self.add_campaign(ALPHA)
        self.add_campaign(BETA)
        self.live(ALPHA, (1, 1000))
        self.settings.priority = ["Alpha", "Beta"]
        self.settings.exclude = {"Alpha"}

        self.assertEqual(await self.select(), (["Beta"], None))
        self.assertIs(self.twitch._state, State.IDLE)

    async def test_priority_and_exclude_match_twitch_names_case_insensitively(self):
        # Saved names like "Valorant" must match Twitch's display name "VALORANT".
        for game in (ALPHA, BETA, GAMMA):
            self.add_campaign(game)
        self.live(ALPHA, (1, 1000))
        self.live(BETA, (2, 5))
        self.settings.priority = ["BETA", "alpha", "gamma"]
        self.settings.exclude = {"GAMMA"}

        self.assertEqual(await self.select(), (["Beta", "Alpha"], 2))

    async def test_mine_now_goes_first_even_outside_the_list_and_unlinked(self):
        for game in (ALPHA, BETA):
            self.add_campaign(game)
        gamma = self.add_campaign(GAMMA, linked=False)
        self.live(ALPHA, (1, 1000))
        self.live(GAMMA, (3, 5))
        self.settings.priority = ["Alpha", "Beta"]  # priority-only, Gamma not listed
        self.settings.focused_campaign = gamma.id

        wanted, watching = await self.select()
        self.assertEqual((wanted, watching), (["Gamma", "Alpha", "Beta"], 3))
        self.assertIs(self.twitch.get_active_campaign(), gamma)

    async def test_ignored_campaign_is_never_mined_even_if_prioritized_and_linked(self):
        alpha = self.add_campaign(ALPHA)
        self.add_campaign(BETA)
        self.live(ALPHA, (1, 1000))
        self.live(BETA, (2, 5))
        self.settings.priority = ["Alpha", "Beta"]
        self.settings.ignored_campaigns = {alpha.id}

        self.assertEqual(await self.select(), (["Beta"], 2))

    async def test_finished_mine_now_choice_clears_and_normal_order_resumes(self):
        done = self.add_campaign(ALPHA, watched=60)
        next(iter(done.drops)).is_claimed = True
        self.add_campaign(BETA)
        self.live(BETA, (2, 5))
        self.settings.focused_campaign = done.id

        self.assertEqual(await self.select(), (["Beta"], 2))
        self.assertEqual(self.settings.focused_campaign, "")

    async def test_ending_soonest_orders_unprioritized_games_by_campaign_end(self):
        self.add_campaign(ALPHA, end=NOW + timedelta(days=3))
        self.add_campaign(BETA, end=NOW + timedelta(days=1))
        self.add_campaign(GAMMA, end=NOW + timedelta(days=2))
        for cid, game in enumerate((ALPHA, BETA, GAMMA), start=1):
            self.live(game, (cid, 100 * cid))
        self.settings.priority_mode = PriorityMode.ENDING_SOONEST

        self.assertEqual(await self.select(), (["Beta", "Gamma", "Alpha"], 2))

    async def test_ending_soonest_keeps_prioritized_games_ahead_of_earlier_endings(self):
        self.add_campaign(ALPHA, end=NOW + timedelta(days=3))
        self.add_campaign(BETA, end=NOW + timedelta(days=1))
        self.add_campaign(GAMMA, end=NOW + timedelta(days=2))
        for cid, game in enumerate((ALPHA, BETA, GAMMA), start=1):
            self.live(game, (cid, 10))
        self.settings.priority = ["Alpha"]
        self.settings.priority_mode = PriorityMode.ENDING_SOONEST

        self.assertEqual(await self.select(), (["Alpha", "Beta", "Gamma"], 1))

    async def test_low_availability_first_orders_by_time_left_per_remaining_minute(self):
        # Alpha: 600 min left / 30 needed = 20; Beta: 1200 min left / 600 needed = 2.
        self.add_campaign(ALPHA, end=NOW + timedelta(hours=10), required=60, watched=30)
        self.add_campaign(BETA, end=NOW + timedelta(hours=20), required=600)
        self.live(ALPHA, (1, 10))
        self.live(BETA, (2, 10))
        self.settings.priority_mode = PriorityMode.LOW_AVBL_FIRST

        self.assertEqual(await self.select(), (["Beta", "Alpha"], 2))

    async def test_campaigns_not_progressable_within_the_hour_are_not_mined(self):
        self.add_campaign(ALPHA, linked=False)
        self.add_campaign(BETA, start=NOW + timedelta(hours=2))
        self.add_campaign(GAMMA)
        for cid, game in enumerate((ALPHA, BETA, GAMMA), start=1):
            self.live(game, (cid, 10))

        self.assertEqual(await self.select(), (["Gamma"], 3))

    async def test_acl_campaign_watches_its_listed_channel_not_bigger_directory_streams(self):
        self.add_campaign(ALPHA, acl=[7])
        self.acl_streams["acl7"] = stream_info(7, ALPHA, viewers=3)
        self.live(ALPHA, (1, 5000))

        self.assertEqual(await self.select(), (["Alpha"], 7))

    async def test_offline_acl_channel_is_not_replaced_by_an_unlisted_live_channel(self):
        self.add_campaign(ALPHA, acl=[7])
        self.acl_streams["acl7"] = stream_info(7, None)
        self.live(ALPHA, (1, 5000))

        self.assertEqual(await self.select(), (["Alpha"], None))


if __name__ == "__main__":
    unittest.main()
