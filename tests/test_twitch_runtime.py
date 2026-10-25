"""Risk rows 1-2: the miner's runtime reactions in twitch.py, with Twitch GQL virtualized.

Covers the watch loop, the maintenance schedule, PubSub stream/drop/notification events and
the inventory / channel discovery requests that feed channel selection.
"""
import asyncio
import json
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from channel import Channel
from constants import PriorityMode, State
from exceptions import GQLException, MinerException
from inventory import DropsCampaign
from twitch import Twitch


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
ALPHA = {"id": "101", "name": "Alpha", "boxArtURL": ""}
BETA = {"id": "202", "name": "Beta", "boxArtURL": ""}
GAMMA = {"id": "303", "name": "Gamma", "boxArtURL": ""}
USER_ID = 42


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def ts(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def drop_data(drop_id, *, start, end, required=60, watched=0, claim_id=None, with_self=True,
              benefit=None):
    data = {
        "id": drop_id, "name": f"Reward {drop_id}", "startAt": ts(start), "endAt": ts(end),
        "benefitEdges": [{"benefit": {"id": benefit or f"benefit-{drop_id}", "name": f"Reward {drop_id}",
                                      "distributionType": "DIRECT_ENTITLEMENT", "imageAssetURL": ""}}],
        "preconditionDrops": None, "requiredMinutesWatched": required,
    }
    if with_self:
        data["self"] = {"dropInstanceID": claim_id, "isClaimed": False, "currentMinutesWatched": watched}
    return data


def campaign_data(campaign_id, game, *, start=NOW - timedelta(days=1), end=NOW + timedelta(days=1),
                  status="ACTIVE", acl=(), drops=None, **drop_kwargs):
    return {
        "id": campaign_id, "name": f"{campaign_id} campaign", "game": dict(game) if game else None,
        "self": {"isAccountConnected": True}, "accountLinkURL": "",
        "startAt": ts(start), "endAt": ts(end), "status": status,
        "allow": {"channels": [{"id": str(cid), "name": f"acl{cid}", "displayName": f"ACL{cid}"}
                               for cid in acl] or None, "isEnabled": True},
        "timeBasedDrops": drops if drops is not None else [
            drop_data(f"{campaign_id}-drop", start=start, end=end, **drop_kwargs)],
    }


def directory_node(channel_id, game, viewers=10):
    return {
        "id": str(80000 + channel_id), "game": dict(game), "viewersCount": viewers, "title": "live",
        "broadcaster": {"id": str(channel_id), "login": f"streamer{channel_id}",
                        "displayName": f"Streamer{channel_id}"},
    }


def stream_info(channel_id, game=None, viewers=10):
    """A `VideoPlayerStreamInfoOverlayChannel` user payload; offline when `game` is None."""
    return {
        "id": str(channel_id), "displayName": f"Streamer{channel_id}",
        "stream": None if game is None else {"id": str(70000 + channel_id), "viewersCount": viewers},
        "broadcastSettings": {"game": dict(game) if game else None, "title": "live"},
    }


class RuntimeTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.temp = tempfile.TemporaryDirectory()
        self.dump_path = Path(self.temp.name, "dump.dat")
        self.patchers = [
            patch("inventory.datetime", Clock), patch("twitch.datetime", Clock),
            patch("twitch.DUMP_PATH", self.dump_path),
            # Claims and device codes shell out to osascript/open on macOS.
            patch("state_hub.subprocess"),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.settings = SimpleNamespace(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, available_drops_check=False, dump=False,
            proxy="", connection_quality=1, save=lambda force=False: None,
        )
        self.twitch = Twitch(self.settings)
        self.hub = self.twitch.gui
        self.twitch.get_auth = AsyncMock(return_value=SimpleNamespace(user_id=USER_ID))
        self.twitch.gql_request = self.fake_gql
        self.gql_calls: list[tuple[str, dict]] = []
        self.gql_batches: list[int] = []
        self.gql_handlers = {}
        self.channels: list[Channel] = []

    async def asyncTearDown(self):
        twitch = self.twitch
        for channel in self.channels:
            channel.remove()
        tasks = {t for t in (*twitch._account_tasks, twitch._mnt_task, twitch._watching_task) if t}
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logging.getLogger("TwitchDrops").removeHandler(self.hub._log_handler)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    async def fake_gql(self, ops):
        if isinstance(ops, list):
            self.gql_batches.append(len(ops))
            return [await self.fake_gql(op) for op in ops]
        name, variables = ops["operationName"], ops["variables"]
        self.gql_calls.append((name, variables))
        result = self.gql_handlers[name](variables)
        if isinstance(result, Exception):
            raise result
        return result

    def calls(self, name):
        return [variables for op, variables in self.gql_calls if op == name]

    def add_campaign(self, data):
        campaign = DropsCampaign(self.twitch, data, {})
        self.twitch.inventory.append(campaign)
        self.twitch._campaigns[campaign.id] = campaign
        self.twitch._drops.update(campaign.timed_drops)
        self.hub.update_inventory_campaign(campaign)
        return campaign

    def want(self, *campaigns):
        self.twitch.wanted_games[:] = [campaign.game for campaign in campaigns]

    def live_channel(self, channel_id, game, *, viewers=10, drops_enabled=True, monitored=True):
        channel = Channel.from_directory(
            self.twitch, directory_node(channel_id, game, viewers), drops_enabled=drops_enabled)
        self.track(channel, monitored)
        return channel

    def offline_channel(self, channel_id, monitored=True):
        channel = Channel(self.twitch, id=channel_id, login=f"streamer{channel_id}",
                          display_name=f"Streamer{channel_id}")
        self.track(channel, monitored)
        return channel

    def track(self, channel, monitored):
        self.channels.append(channel)
        if monitored:
            self.twitch.channels[channel.id] = channel
            channel.display(add=True)

    def logged(self, text):
        return [entry["message"] for entry in self.hub.logs if text in entry["message"]]

    def new_account_tasks(self, before):
        return [task for task in self.twitch._account_tasks if task not in before]


class InstantSleep:
    """Replaces asyncio.sleep for the test's own task only, recording the requested delays."""

    def __init__(self, on_sleep=None):
        self.delays: list[float] = []
        self.on_sleep = on_sleep
        self._real = asyncio.sleep
        self._owner = None

    async def __call__(self, delay, *args, **kwargs):
        if asyncio.current_task() is not self._owner:
            return await self._real(delay, *args, **kwargs)
        self.delays.append(delay)
        if self.on_sleep is not None:
            self.on_sleep(delay)
        return await self._real(0)

    def __enter__(self):
        self._owner = asyncio.current_task()
        self._patch = patch("asyncio.sleep", self)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class WatchLoopTests(RuntimeTestCase):
    """The watch loop keeps Twitch crediting minutes for whichever channel is being watched."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.alpha = self.add_campaign(campaign_data("alpha", ALPHA, watched=10))
        self.beta = self.add_campaign(campaign_data("beta", BETA))
        self.want(self.alpha, self.beta)
        self.fake_now = 1_000.0
        self.request_seconds = 20.0  # a whole watch interval: every loop pause is zero
        self.watched: list[int] = []
        self.watch_events: dict[int, asyncio.Event] = {}
        self.block_after = 1
        self.patchers.append(patch("twitch.time", lambda: self.fake_now))
        self.patchers.append(patch.object(
            Channel, "send_watch", lambda channel: self.fake_send_watch(channel)))
        for patcher in self.patchers[-2:]:
            patcher.start()

    async def fake_send_watch(self, channel):
        self.watched.append(channel.id)
        self.fake_now += self.request_seconds
        if (event := self.watch_events.get(len(self.watched))) is not None:
            event.set()
        if len(self.watched) >= self.block_after:
            await asyncio.Event().wait()  # park the loop; the test cancels it
        return True

    async def run_until_watch(self, count):
        self.block_after = count
        event = self.watch_events.setdefault(count, asyncio.Event())
        if self.twitch._watching_task is None:
            self.twitch._watching_task = asyncio.create_task(self.twitch._watch_loop())
        await asyncio.wait_for(event.wait(), 2)

    async def test_watch_requests_move_to_the_new_channel_as_soon_as_watching_restarts(self):
        first, second = self.live_channel(1, ALPHA), self.live_channel(2, BETA)
        self.request_seconds = 15  # the loop then pauses 5 s before the next watch request
        self.twitch.watch(first)
        pausing = asyncio.Event()
        real_pause = self.twitch._watch_sleep

        async def observed_pause(delay):
            pausing.set()
            await real_pause(delay)

        self.twitch._watch_sleep = observed_pause
        self.block_after = 2
        self.watch_events[2] = switched = asyncio.Event()
        self.twitch._watching_task = asyncio.create_task(self.twitch._watch_loop())
        await asyncio.wait_for(pausing.wait(), 2)

        self.twitch.watch(second)
        self.twitch.restart_watching()
        await asyncio.wait_for(switched.wait(), 2)  # well before the 5 s pause would end
        self.assertEqual(self.watched, [first.id, second.id])

    async def test_channel_that_went_offline_is_dropped_without_a_watch_request(self):
        gone, live = self.offline_channel(1), self.live_channel(2, BETA)
        self.twitch.watching_channel.set(gone)
        self.hub.channels.set_watching(gone)
        events = self.hub.subscribe()
        self.twitch._watching_task = asyncio.create_task(self.twitch._watch_loop())
        # The loop clears the offline channel from the dashboard, then waits for a new one.
        while (event := await asyncio.wait_for(events.get(), 2))["event"] != "watching":
            pass
        self.hub.unsubscribe(events)
        self.assertIsNone(event["data"])
        self.assertFalse(self.twitch.watching_channel.has_value())

        self.twitch.watch(live)
        await self.run_until_watch(1)
        self.assertEqual(self.watched, [live.id])

    async def test_pause_during_watch_request_does_not_restore_stale_progress(self):
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)
        blocked = asyncio.Event()
        original_get = self.twitch.watching_channel.get

        async def observe_waiting():
            if not self.twitch.watching_channel.has_value():
                blocked.set()
            return await original_get()

        async def pause_during_request():
            self.twitch._paused = True
            self.twitch.stop_watching()
            self.fake_now += self.request_seconds
            return True

        self.gql_handlers["DropCurrentSessionContext"] = lambda v: {
            "data": {"currentUser": {"dropCurrentSession": None}}}
        self.twitch.watching_channel.get = observe_waiting
        with patch.object(Channel, "send_watch", side_effect=pause_during_request), \
                patch("twitch.asyncio.sleep", new_callable=AsyncMock):
            self.twitch._watching_task = asyncio.create_task(self.twitch._watch_loop())
            await asyncio.wait_for(blocked.wait(), 2)

        self.assertIsNone(self.hub.active_drop)
        self.assertEqual(self.alpha.timed_drops["alpha-drop"].current_minutes, 10)

    async def test_stalled_progress_is_resynced_from_twitchs_current_drop_session(self):
        channel = self.live_channel(1, ALPHA)
        drop = self.alpha.timed_drops["alpha-drop"]
        self.gql_handlers["DropCurrentSessionContext"] = lambda v: {"data": {"currentUser": {
            "dropCurrentSession": {"dropID": drop.id, "currentMinutesWatched": 25}}}}
        self.twitch.watch(channel)
        self.hub.progress.clear()  # no countdown running: Twitch stopped reporting progress

        await self.run_until_watch(2)
        self.assertEqual([v["channelID"] for v in self.calls("DropCurrentSessionContext")], ["1"])
        self.assertEqual(drop.current_minutes, 25)
        self.assertEqual(self.hub.active_drop["current_minutes"], 25)

    async def test_without_a_usable_drop_session_the_active_drop_is_estimated_one_minute(self):
        channel = self.live_channel(1, ALPHA)
        drop = self.alpha.timed_drops["alpha-drop"]
        beta_drop = self.beta.timed_drops["beta-drop"]
        sessions = {
            "query fails": GQLException("service unavailable"),
            "drop of another game": {"data": {"currentUser": {"dropCurrentSession": {
                "dropID": beta_drop.id, "currentMinutesWatched": 50}}}},
            "no session": {"data": {"currentUser": {"dropCurrentSession": None}}},
        }
        self.twitch.watch(channel)
        for count, (label, session) in enumerate(sessions.items(), start=1):
            with self.subTest(label):
                self.gql_handlers["DropCurrentSessionContext"] = lambda v, s=session: s
                self.hub.progress.clear()
                minutes = drop.current_minutes
                if self.twitch._watching_task is not None:
                    self.twitch._watching_task.cancel()
                    self.twitch._watching_task = None
                self.watched.clear()
                self.watch_events.clear()
                await self.run_until_watch(2)
                self.assertEqual(drop.current_minutes, minutes + 1)
                self.assertEqual(beta_drop.current_minutes, 0)
                self.assertEqual(self.hub.active_drop["current_minutes"], minutes + 1)


class MaintenanceScheduleTests(RuntimeTestCase):
    """Campaign boundaries trigger a channel cleanup; every hour the inventory is reloaded."""

    async def run_maintenance(self):
        states = []

        def advance(delay):
            states.append(self.twitch._state)
            Clock.current += timedelta(seconds=delay)

        with InstantSleep(advance) as sleep:
            await self.twitch._maintenance_task()
        return sleep.delays, states

    async def test_campaign_boundary_is_not_delayed_by_periodic_discovery(self):
        boundary, next_hour = (NOW + timedelta(minutes=m) for m in (7, 90))
        self.twitch._mnt_triggers.extend([boundary, next_hour])

        delays, _ = await self.run_maintenance()

        self.assertEqual(delays[:3], [300, 120, 180])
        self.assertEqual(sum(delays), 3600)
        self.assertIs(self.twitch._state, State.INVENTORY_FETCH)
        self.assertEqual(list(self.twitch._mnt_triggers), [next_hour])

    async def test_waiting_queue_is_rediscovered_within_five_minutes(self):
        delays, states = await self.run_maintenance()
        self.assertEqual(delays[0], 300)
        self.assertIs(states[1], State.CHANNELS_CLEANUP)
        self.assertEqual(sum(delays), 3600)
        self.assertIs(self.twitch._state, State.INVENTORY_FETCH)

    async def test_periodic_discovery_and_hourly_reload_respect_manual_pause(self):
        self.twitch._paused = True
        _, states = await self.run_maintenance()
        self.assertEqual(set(states), {State.IDLE})
        self.assertIs(self.twitch._state, State.IDLE)


class StreamEventTests(RuntimeTestCase):
    """PubSub stream-state / broadcast-settings events drive online status and switching."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.patchers.append(patch("channel.ONLINE_DELAY", timedelta(0)))
        self.patchers[-1].start()
        self.alpha = self.add_campaign(campaign_data("alpha", ALPHA))
        self.beta = self.add_campaign(campaign_data("beta", BETA))
        self.want(self.alpha, self.beta)
        self.streams: dict[str, dict] = {}
        self.gql_handlers["VideoPlayerStreamInfoOverlayChannel"] = (
            lambda v: {"data": {"user": self.streams.get(v["channel"])}})

    async def deliver(self, handler, channel_id, message):
        """Deliver one PubSub message and let the delayed online re-check it schedules finish."""
        before = set(self.twitch._account_tasks)
        await handler(channel_id, message)
        pending = [task for task in self.new_account_tasks(before)
                   if task.get_coro().__qualname__ == "Channel._online_delay"]
        await asyncio.gather(*pending)

    def watching(self):
        return self.twitch.watching_channel.get_with_default(None)

    async def test_viewer_count_updates_the_dashboard_channel_row(self):
        channel = self.live_channel(1, ALPHA, viewers=10)
        await self.deliver(self.twitch.process_stream_state, 1, {"type": "viewcount", "viewers": 321})
        self.assertEqual(channel.viewers, 321)
        self.assertEqual(self.hub.channels_map[1]["viewers"], 321)

    async def test_viewers_or_stream_up_for_an_offline_channel_check_whether_it_is_live(self):
        for message in ({"type": "viewcount", "viewers": 5}, {"type": "stream-up"}):
            with self.subTest(message["type"]):
                channel = self.offline_channel(1)
                self.streams["streamer1"] = stream_info(1, GAMMA, viewers=77)
                await self.deliver(self.twitch.process_stream_state, 1, message)
                self.assertTrue(channel.online)
                self.assertEqual(self.hub.channels_map[1]["viewers"], 77)
                self.assertIsNone(self.watching())  # Gamma is not a wanted game

    async def test_watched_channel_going_down_requests_a_channel_switch(self):
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)
        await self.deliver(self.twitch.process_stream_state, 1, {"type": "stream-down"})
        self.assertFalse(channel.online)
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)
        self.assertTrue(self.logged("Streamer1 goes OFFLINE, switching"))

    async def test_unwatched_channel_going_down_does_not_disturb_mining(self):
        watched, other = self.live_channel(1, ALPHA), self.live_channel(2, BETA)
        self.twitch.watch(watched)
        await self.deliver(self.twitch.process_stream_state, 2, {"type": "stream-down"})
        self.assertFalse(other.online)
        self.assertIs(self.watching(), watched)
        self.assertIs(self.twitch._state, State.IDLE)

    async def test_higher_priority_channel_coming_online_takes_over(self):
        watched = self.live_channel(2, BETA)
        rising = self.offline_channel(1)
        self.twitch.watch(watched)
        self.streams["streamer1"] = stream_info(1, ALPHA)
        await self.deliver(self.twitch.process_stream_state, 1, {"type": "stream-up"})
        self.assertIs(self.watching(), rising)
        self.assertEqual(self.hub.active_channel["id"], 1)
        self.assertTrue(self.logged("Streamer1 goes ONLINE, switching"))

    async def test_lower_priority_channel_coming_online_does_not_take_over(self):
        watched = self.live_channel(1, ALPHA)
        rising = self.offline_channel(2)
        self.twitch.watch(watched)
        self.streams["streamer2"] = stream_info(2, BETA)
        await self.deliver(self.twitch.process_stream_state, 2, {"type": "stream-up"})
        self.assertTrue(rising.online)
        self.assertIs(self.watching(), watched)

    async def test_watched_channel_switching_to_an_unwanted_game_requests_a_switch(self):
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)
        self.streams["streamer1"] = stream_info(1, GAMMA)
        await self.deliver(self.twitch.process_stream_update, 1, {
            "type": "broadcast_settings_update", "old_game": "Alpha", "game": "Gamma"})
        self.assertEqual(channel.game.name, "Gamma")
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)

    async def test_watched_channel_title_change_keeps_mining_it(self):
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)
        self.streams["streamer1"] = stream_info(1, ALPHA)
        await self.deliver(self.twitch.process_stream_update, 1, {
            "type": "broadcast_settings_update", "old_game": "Alpha", "game": "Alpha"})
        self.assertIs(self.watching(), channel)
        self.assertIs(self.twitch._state, State.IDLE)

    async def test_live_channel_enabling_drops_for_a_better_game_takes_over(self):
        watched = self.live_channel(2, BETA)
        better = self.live_channel(1, ALPHA, drops_enabled=False)
        self.twitch.watch(watched)
        self.streams["streamer1"] = stream_info(1, ALPHA)  # drops are now enabled
        await self.deliver(self.twitch.process_stream_update, 1, {
            "type": "broadcast_settings_update", "old_game": "Alpha", "game": "Alpha"})
        self.assertIs(self.watching(), better)

    async def test_events_for_unmonitored_channels_change_nothing(self):
        stray = self.live_channel(9, ALPHA, monitored=False)
        await self.deliver(self.twitch.process_stream_state, 9, {"type": "stream-down"})
        await self.deliver(self.twitch.process_stream_update, 9, {
            "type": "broadcast_settings_update", "old_game": "Alpha", "game": "Gamma"})
        self.assertTrue(stray.online)
        self.assertEqual(self.gql_calls, [])


class DropEventTests(RuntimeTestCase):
    """PubSub drop-progress and drop-claim events (risk row 1: live progress and claiming)."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.alpha = self.add_campaign(campaign_data("alpha", ALPHA, watched=10))
        self.beta = self.add_campaign(campaign_data("beta", BETA))
        self.want(self.alpha, self.beta)
        self.drop = self.alpha.timed_drops["alpha-drop"]
        self.claimed: list[str] = []

        def claim(variables):
            self.claimed.append(variables["input"]["dropInstanceID"])
            return {"data": {"claimDropRewards": {"status": "ELIGIBLE_FOR_ALL"}}}

        self.gql_handlers["DropsPage_ClaimDropRewards"] = claim

    def hub_drop(self, campaign_id, drop_id):
        """The drop as the dashboard's /api/inventory projection reports it."""
        campaign = next(c for c in self.hub.get_inventory() if c["id"] == campaign_id)
        return next(d for d in campaign["drops"] if d["id"] == drop_id)

    async def test_progress_for_the_watched_drop_updates_minutes_everywhere(self):
        self.twitch.watch(self.live_channel(1, ALPHA))
        await self.twitch.process_drops(USER_ID, {"type": "drop-progress", "data": {
            "drop_id": self.drop.id, "current_progress_min": 30, "required_progress_min": 60}})
        self.assertEqual(self.drop.current_minutes, 30)
        self.assertEqual(self.hub.active_drop["current_minutes"], 30)
        self.assertEqual(self.hub_drop("alpha", self.drop.id)["current_minutes"], 30)

    async def test_progress_that_cannot_come_from_the_watched_channel_is_ignored(self):
        self.twitch.watch(self.live_channel(2, BETA))
        for drop_id in (self.drop.id, "unknown-drop"):
            with self.subTest(drop_id):
                await self.twitch.process_drops(USER_ID, {"type": "drop-progress", "data": {
                    "drop_id": drop_id, "current_progress_min": 30, "required_progress_min": 60}})
                self.assertEqual(self.drop.current_minutes, 10)

    async def test_ready_drop_with_auto_claim_disabled_is_left_claimable(self):
        await self.twitch.process_drops(USER_ID, {"type": "drop-claim", "data": {
            "drop_id": self.drop.id, "drop_instance_id": "instance-1"}})
        self.assertTrue(self.drop.can_claim)
        self.assertFalse(self.drop.is_claimed)
        self.assertEqual(self.claimed, [])
        self.assertTrue(self.hub_drop("alpha", self.drop.id)["can_claim"])

    async def test_auto_claim_claims_and_reloads_when_the_campaign_is_finished(self):
        self.settings.auto_claim = True
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)
        sessions = iter([self.drop.id, None])
        self.gql_handlers["DropCurrentSessionContext"] = lambda v: {"data": {"currentUser": {
            "dropCurrentSession": (drop_id := next(sessions)) and {"dropID": drop_id}}}}

        with InstantSleep() as sleep:
            await self.twitch.process_drops(USER_ID, {"type": "drop-claim", "data": {
                "drop_id": self.drop.id, "drop_instance_id": "instance-1"}})

        self.assertEqual(self.claimed, ["instance-1"])
        self.assertTrue(self.drop.is_claimed)
        self.assertTrue(self.logged("Claimed drop"))
        # Waits for Twitch to move its drop session off the claimed drop before reloading.
        self.assertEqual([v["channelID"] for v in self.calls("DropCurrentSessionContext")], ["1", "1"])
        self.assertEqual(sleep.delays, [4, 2])
        self.assertIs(self.twitch._state, State.INVENTORY_FETCH)

    async def test_auto_claim_resumes_watching_when_the_campaign_has_more_drops(self):
        self.settings.auto_claim = True
        start, end = NOW - timedelta(days=1), NOW + timedelta(days=1)
        campaign = self.add_campaign(campaign_data("chain", GAMMA, drops=[
            drop_data("first", start=start, end=end, required=30, watched=30),
            drop_data("second", start=start, end=end, required=90, watched=30),
        ]))
        self.want(campaign)
        self.twitch.watch(self.live_channel(3, GAMMA))
        self.gql_handlers["DropCurrentSessionContext"] = lambda v: {"data": {"currentUser": {
            "dropCurrentSession": {"dropID": "second"}}}}

        with InstantSleep():
            await self.twitch.process_drops(USER_ID, {"type": "drop-claim", "data": {
                "drop_id": "first", "drop_instance_id": "instance-first"}})

        self.assertEqual(self.claimed, ["instance-first"])
        self.assertTrue(self.twitch._watching_restart.is_set())
        self.assertIs(self.twitch._state, State.IDLE)

    async def test_other_drop_messages_are_ignored(self):
        await self.twitch.process_drops(USER_ID, {"type": "drop-something-else", "data": {}})
        self.assertEqual(self.drop.current_minutes, 10)
        self.assertEqual(self.gql_calls, [])


class NotificationEventTests(RuntimeTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.gql_handlers["OnsiteNotifications_DeleteNotification"] = lambda v: {"data": {}}

    def notification(self, kind):
        return {"type": "create-notification",
                "data": {"notification": {"id": "notification-1", "type": kind}}}

    async def test_reward_notification_reloads_inventory_and_is_dismissed(self):
        for kind in ("user_drop_reward_reminder_notification",
                     "quests_viewer_reward_campaign_earned_emote"):
            with self.subTest(kind):
                self.twitch._state = State.IDLE
                self.gql_calls.clear()
                await self.twitch.process_notifications(USER_ID, self.notification(kind))
                self.assertIs(self.twitch._state, State.INVENTORY_FETCH)
                self.assertEqual(self.calls("OnsiteNotifications_DeleteNotification"),
                                 [{"input": {"id": "notification-1"}}])

    async def test_unrelated_notifications_are_left_alone(self):
        await self.twitch.process_notifications(USER_ID, self.notification("live_stream_started"))
        await self.twitch.process_notifications(USER_ID, {"type": "update-summary", "data": {}})
        self.assertIs(self.twitch._state, State.IDLE)
        self.assertEqual(self.gql_calls, [])


class InventoryFetchTests(RuntimeTestCase):
    """fetch_inventory merges inventory progress, the campaign list and campaign details."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        day = timedelta(days=1)
        self.in_progress = campaign_data("progress", ALPHA, watched=30)
        self.details = {
            "progress": campaign_data("progress", ALPHA),
            "upcoming": campaign_data("upcoming", BETA, start=NOW + timedelta(minutes=30), end=NOW + day),
            "later": campaign_data("later", BETA, start=NOW + timedelta(hours=3), end=NOW + 2 * day),
            "claimed": campaign_data("claimed", GAMMA, with_self=False, benefit="benefit-claimed"),
            "gameless": campaign_data("gameless", None),
            "expired": campaign_data("expired", GAMMA, end=NOW - day, status="EXPIRED"),
        }
        self.awarded = [{"id": "benefit-claimed", "lastAwardedAt": ts(NOW - timedelta(hours=1))}]
        self.gql_handlers.update({
            "Inventory": lambda v: {"data": {"currentUser": {"inventory": {
                "dropCampaignsInProgress": [json.loads(json.dumps(self.in_progress))],
                "gameEventDrops": self.awarded}}}},
            "ViewerDropsDashboard": lambda v: {"data": {"currentUser": {"dropCampaigns": [
                {"id": cid, "name": data["name"], "status": data["status"],
                 "game": data["game"], "self": data["self"]}
                for cid, data in self.details.items()]}}},
            "DropCampaignDetails": self.campaign_details,
        })

    def campaign_details(self, variables):
        self.assertEqual(variables["channelLogin"], str(USER_ID))
        data = json.loads(json.dumps(self.details[variables["dropID"]]))
        return {"data": {"user": {"dropCampaign": data}}}

    async def test_inventory_lists_mergeable_campaigns_with_account_progress(self):
        await self.twitch.fetch_inventory()

        campaigns = {c.id: c for c in self.twitch.inventory}
        self.assertEqual(set(campaigns), {"progress", "upcoming", "later", "claimed"})
        self.assertEqual(set(self.hub.campaigns_map), set(campaigns))
        self.assertNotIn("expired", [v["dropID"] for v in self.calls("DropCampaignDetails")])
        self.assertEqual(campaigns["progress"].timed_drops["progress-drop"].current_minutes, 30)
        # Without a "self" edge, a benefit awarded inside the drop window marks the drop claimed.
        self.assertTrue(campaigns["claimed"].timed_drops["claimed-drop"].is_claimed)
        self.assertIs(self.twitch._drops["upcoming-drop"].campaign, campaigns["upcoming"])

    async def test_maintenance_is_scheduled_at_future_boundaries_of_campaigns_earnable_soon(self):
        await self.twitch.fetch_inventory()
        day = timedelta(days=1)
        self.assertEqual(list(self.twitch._mnt_triggers),
                         [NOW + timedelta(minutes=30), NOW + day])
        self.assertFalse(self.twitch._mnt_task.done())

    async def test_watched_channel_that_can_no_longer_earn_is_dropped_on_refresh(self):
        old = self.add_campaign(campaign_data("old", GAMMA))
        self.want(old)
        self.twitch.watch(self.live_channel(1, GAMMA))
        del self.details["claimed"], self.details["expired"]

        await self.twitch.fetch_inventory()

        self.assertIsNone(self.twitch.watching_channel.get_with_default(None))
        self.assertIsNone(self.hub.active_channel)
        self.assertIs(self.twitch._state, State.GAMES_UPDATE)

    async def test_watched_channel_that_still_earns_is_rebound_to_the_new_inventory(self):
        self.want(DropsCampaign(self.twitch, self.in_progress, {}))
        channel = self.live_channel(1, ALPHA)
        self.twitch.watch(channel)

        await self.twitch.fetch_inventory()

        self.assertIs(self.twitch.watching_channel.get_with_default(None), channel)
        new_drop = next(c for c in self.twitch.inventory if c.id == "progress").timed_drops["progress-drop"]
        self.assertEqual(self.hub.active_drop["id"], new_drop.id)
        self.assertEqual(self.hub.active_drop["current_minutes"], 30)

    async def test_failed_campaign_details_keep_the_previous_inventory(self):
        previous = self.add_campaign(campaign_data("previous", ALPHA))
        self.gql_handlers["DropCampaignDetails"] = lambda v: GQLException("service unavailable")
        with self.assertRaises(GQLException):
            await self.twitch.fetch_inventory()
        self.assertEqual(self.twitch.inventory, [previous])

    async def test_dump_mode_writes_inventory_without_user_identifiers(self):
        self.settings.dump = True
        self.details = {"acl": campaign_data("acl", ALPHA, acl=[1, 2], claim_id=f"{USER_ID}#acl#d")}
        self.in_progress = campaign_data("acl", ALPHA, acl=[1, 2], claim_id=f"{USER_ID}#acl#d")

        await self.twitch.fetch_inventory()

        dumped = self.dump_path.read_text(encoding="utf8")
        campaign = json.loads(dumped.split("\n\n")[0])["acl"]
        self.assertEqual(campaign["allow"]["channels"], "2 channels")
        self.assertEqual(campaign["timeBasedDrops"][0]["self"]["dropInstanceID"], "...")
        self.assertNotIn(f"{USER_ID}#", dumped)
        # The in-memory campaign keeps the real ACL and claim ID.
        acl_campaign = self.twitch.inventory[0]
        self.assertEqual([c.id for c in acl_campaign.allowed_channels], [1, 2])
        self.assertEqual(acl_campaign.timed_drops["acl-drop"].claim_id, f"{USER_ID}#acl#d")


class ChannelDiscoveryTests(RuntimeTestCase):
    """Directory and ACL lookups that produce the channel candidates for selection."""

    async def test_live_streams_come_from_the_drops_enabled_game_directory(self):
        game = DropsCampaign(self.twitch, campaign_data("alpha", ALPHA), {}).game
        nodes = [directory_node(1, ALPHA, 50), dict(directory_node(2, ALPHA), broadcaster=None),
                 directory_node(3, ALPHA, 5)]
        self.gql_handlers["DirectoryPage_Game"] = lambda v: {"data": {"game": {"streams": {
            "edges": [{"node": node} for node in nodes]}}}}

        channels = await self.twitch.get_live_streams(game, drops_enabled=True)

        self.assertEqual([(c.id, c.viewers, c.drops_enabled) for c in channels],
                         [(1, 50, True), (3, 5, True)])
        request = self.calls("DirectoryPage_Game")[0]
        self.assertEqual(request["slug"], game.slug)
        self.assertEqual(request["options"]["systemFilters"], ["DROPS_ENABLED"])

    async def test_unknown_game_has_no_live_streams_and_errors_name_the_game(self):
        game = DropsCampaign(self.twitch, campaign_data("alpha", ALPHA), {}).game
        self.gql_handlers["DirectoryPage_Game"] = lambda v: {"data": {"game": None}}
        self.assertEqual(await self.twitch.get_live_streams(game), [])

        self.gql_handlers["DirectoryPage_Game"] = lambda v: GQLException("bad slug")
        with self.assertRaisesRegex(MinerException, game.slug):
            await self.twitch.get_live_streams(game)

    async def test_bulk_online_check_batches_acl_channels_and_marks_live_ones(self):
        ids = range(1, 26)
        campaign = self.add_campaign(campaign_data("acl", ALPHA, acl=ids))
        users = {f"acl{i}": stream_info(i, ALPHA if i % 2 else None, viewers=i) for i in ids}
        users["acl25"] = None  # renamed/banned account
        self.gql_handlers["VideoPlayerStreamInfoOverlayChannel"] = (
            lambda v: {"data": {"user": users[v["channel"]]}})

        await self.twitch.bulk_check_online(campaign.allowed_channels)

        self.assertEqual(sorted(self.gql_batches), [5, 20])
        online = {c.id: c.viewers for c in campaign.allowed_channels if c.online}
        self.assertEqual(online, {i: i for i in ids if i % 2 and i != 25})

    async def test_bulk_online_check_verifies_available_drops_when_enabled(self):
        self.settings.available_drops_check = True
        campaign = self.add_campaign(campaign_data("acl", ALPHA, acl=[1, 2]))
        self.gql_handlers["VideoPlayerStreamInfoOverlayChannel"] = (
            lambda v: {"data": {"user": stream_info(int(v["channel"][3:]), ALPHA)}})
        available = {"1": [{"id": "acl"}], "2": [{"id": "some-other-campaign"}]}
        self.gql_handlers["DropsHighlightService_AvailableDrops"] = lambda v: {"data": {"channel": {
            "id": v["channelID"], "viewerDropCampaigns": available[v["channelID"]]}}}

        await self.twitch.bulk_check_online(campaign.allowed_channels)

        self.assertEqual({c.id: c.drops_enabled for c in campaign.allowed_channels},
                         {1: True, 2: False})

    async def test_bulk_online_check_failure_propagates(self):
        campaign = self.add_campaign(campaign_data("acl", ALPHA, acl=[1]))
        self.gql_handlers["VideoPlayerStreamInfoOverlayChannel"] = lambda v: GQLException("down")
        with self.assertRaises(GQLException):
            await self.twitch.bulk_check_online(campaign.allowed_channels)
        self.assertFalse(campaign.allowed_channels[0].online)


class ActiveCampaignTests(RuntimeTestCase):
    async def test_active_campaign_is_the_earnable_one_closest_to_completion(self):
        far = self.add_campaign(campaign_data("far", ALPHA, watched=5))
        close = self.add_campaign(campaign_data("close", ALPHA, watched=50))
        self.add_campaign(campaign_data("other", BETA, watched=59))
        channel = self.live_channel(1, ALPHA)

        self.assertIsNone(self.twitch.get_active_campaign(channel))  # nothing wanted yet
        self.want(far)
        self.assertIs(self.twitch.get_active_campaign(channel), close)
        self.assertIsNone(self.twitch.get_active_campaign(self.live_channel(2, GAMMA)))
        self.assertIsNone(self.twitch.get_active_campaign())  # not watching anything


if __name__ == "__main__":
    unittest.main()
