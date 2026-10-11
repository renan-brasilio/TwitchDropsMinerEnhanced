"""Risk row 2: runtime helpers the miner's request, watch and task machinery rely on (utils.py)."""
import asyncio
import json
import logging
from itertools import islice
import random
import string
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yarl import URL

from channel import Channel
from constants import PriorityMode, State
from exceptions import ExitRequest, ReloadRequest
from twitch import Twitch
from utils import (
    CHARS_HEX_LOWER, AwaitableValue, ExponentialBackoff, Game, RateLimiter, create_nonce,
    format_traceback, json_load, json_save, lock_file, task_wrapper, timestamp,
)


REPO = Path(__file__).resolve().parent.parent


async def ticks(count=10):
    """Let every ready task run; used only to show that a waiter stays blocked."""
    for _ in range(count):
        await asyncio.sleep(0)


class TimestampTests(unittest.TestCase):
    def test_twitch_timestamps_parse_as_utc_with_or_without_fractional_seconds(self):
        self.assertEqual(timestamp("2026-10-11T12:30:05.250Z"),
                         datetime(2026, 10, 11, 12, 30, 5, 250000, tzinfo=timezone.utc))
        self.assertEqual(timestamp("2026-10-11T12:30:05Z"),
                         datetime(2026, 10, 11, 12, 30, 5, tzinfo=timezone.utc))

    def test_non_twitch_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            timestamp("2026-10-11 12:30:05")


class ExponentialBackoffTests(unittest.TestCase):
    def take(self, backoff, count):
        return list(islice(backoff, count))  # the miner consumes it as `for delay in backoff`

    def test_delays_double_from_one_until_capped_at_the_maximum(self):
        backoff = ExponentialBackoff(variance=(1, 1), maximum=10)
        self.assertEqual(self.take(backoff, 7), [1, 2, 4, 8, 10, 10, 10])

    def test_shift_is_added_to_every_delay_and_counts_toward_the_cap(self):
        backoff = ExponentialBackoff(base=3, variance=(1, 1), shift=5, maximum=20)
        self.assertEqual(self.take(backoff, 4), [6, 8, 14, 20])

    def test_reset_restarts_the_sequence_even_after_long_capping(self):
        backoff = ExponentialBackoff(variance=(1, 1), maximum=10)
        self.take(backoff, 500)  # the exponent must not keep growing while capped
        backoff.reset()
        self.assertEqual(self.take(backoff, 3), [1, 2, 4])

    def test_jitter_stays_within_the_variance_band(self):
        with patch("utils.random", random.Random(20261011)):
            for _ in range(50):
                values = self.take(ExponentialBackoff(maximum=10_000), 8)
                for step, value in enumerate(values):
                    self.assertGreaterEqual(value, 0.9 * 2 ** step)
                    self.assertLessEqual(value, 1.1 * 2 ** step)
            jittered = self.take(ExponentialBackoff(maximum=10_000), 8)
        self.assertNotEqual(jittered, [2 ** step for step in range(8)])

    def test_base_must_grow(self):
        for base in (1, 0.5):
            with self.assertRaises(ValueError):
                ExponentialBackoff(base=base)


class ManualWindow:
    """Stands in for `asyncio` inside utils: a rate-limit window elapses only on `elapse()`."""

    def __init__(self):
        self.durations = []
        self._timers = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay):
        timer = asyncio.get_running_loop().create_future()
        self.durations.append(delay)
        self._timers.append(timer)
        await timer

    async def elapse(self):
        self._timers.pop(0).set_result(None)
        await ticks()


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.clock = ManualWindow()
        patcher = patch("utils.asyncio", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.limiter = RateLimiter(capacity=2, window=1)
        self.release = asyncio.Event()
        self.tasks = []

    async def asyncTearDown(self):
        self.release.set()
        for task in self.tasks:
            task.cancel()
        if self.limiter._reset_task is not None:
            self.limiter._reset_task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def enter(self, *, hold=False):
        entered = asyncio.Event()

        async def use():
            async with self.limiter:
                entered.set()
                if hold:
                    await self.release.wait()
        self.tasks.append(asyncio.create_task(use()))
        return entered

    async def test_entries_beyond_capacity_wait_for_the_window_to_elapse(self):
        first, second = self.enter(), self.enter()
        await ticks()
        self.assertTrue(first.is_set() and second.is_set())
        third = self.enter()
        await ticks()
        self.assertFalse(third.is_set())
        self.assertEqual(self.clock.durations, [1])
        await self.clock.elapse()
        self.assertTrue(third.is_set())

    async def test_each_elapsed_window_admits_a_new_batch(self):
        self.enter(), self.enter()
        await ticks()
        await self.clock.elapse()
        batch = [self.enter() for _ in range(3)]
        await ticks()
        self.assertEqual([entered.is_set() for entered in batch], [True, True, False])
        self.assertEqual(self.clock.durations, [1, 1])
        await self.clock.elapse()
        self.assertTrue(batch[2].is_set())

    async def test_concurrent_holders_block_entry_even_after_the_window_resets(self):
        holders = [self.enter(hold=True), self.enter(hold=True)]
        await ticks()
        self.assertTrue(all(entered.is_set() for entered in holders))
        queued = self.enter()
        await self.clock.elapse()
        late = self.enter()  # arrives after the window reset, while both slots are still held
        await ticks()
        self.assertFalse(queued.is_set() or late.is_set())
        self.release.set()
        await ticks()
        self.assertTrue(queued.is_set() and late.is_set())


class AwaitableValueTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_waits_until_a_value_is_set(self):
        value = AwaitableValue()
        getter = asyncio.create_task(value.get())
        await ticks()
        self.assertFalse(getter.done())
        self.assertFalse(value.has_value())
        self.assertEqual(value.get_with_default("none"), "none")
        value.set("channel")
        self.assertEqual(await getter, "channel")
        self.assertTrue(value.has_value())
        self.assertEqual(value.get_with_default("none"), "channel")
        self.assertTrue(await value.wait())

    async def test_cleared_value_reads_as_unset_until_replaced(self):
        value = AwaitableValue()
        value.set("first")
        value.clear()
        self.assertFalse(value.has_value())
        self.assertIsNone(value.get_with_default(None))
        getter = asyncio.create_task(value.get())
        await ticks()
        self.assertFalse(getter.done())
        value.set("second")
        self.assertEqual(await getter, "second")


class TaskWrapperTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.twitch = Twitch(SimpleNamespace(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, available_drops_check=False, dump=False,
            save=lambda force=False: None,
        ))

    async def asyncTearDown(self):
        logging.getLogger("TwitchDrops").removeHandler(self.twitch.gui._log_handler)

    def assert_running(self):
        self.assertFalse(self.twitch.gui.close_requested)
        self.assertIsNot(self.twitch._state, State.EXIT)

    def assert_closed(self):
        self.assertTrue(self.twitch.gui.close_requested)
        self.assertIs(self.twitch._state, State.EXIT)

    async def test_exit_and_reload_requests_end_the_task_quietly(self):
        for request in (ExitRequest, ReloadRequest):
            @task_wrapper(critical=True)
            async def task(twitch):
                raise request()
            self.assertIsNone(await task(self.twitch))
        self.assert_running()

    async def test_failing_task_is_logged_and_reraised_without_closing(self):
        @task_wrapper
        async def process_drops(twitch):
            raise KeyError("drop")
        with self.assertLogs("TwitchDrops", "ERROR") as logs:
            with self.assertRaises(KeyError):
                await process_drops(self.twitch)
        self.assertIn("Exception in process_drops task", logs.output[0])
        self.assert_running()

    async def test_critical_twitch_task_failure_closes_the_miner(self):
        @task_wrapper(critical=True)
        async def watch_loop(twitch):
            raise RuntimeError("watch loop died")
        with self.assertLogs("TwitchDrops", "ERROR"), self.assertRaises(RuntimeError):
            await watch_loop(self.twitch)
        self.assert_closed()

    async def test_critical_task_of_a_twitch_owned_object_closes_the_miner(self):
        @task_wrapper(critical=True)
        async def handle(owner):
            raise RuntimeError("websocket handler died")
        with self.assertLogs("TwitchDrops", "ERROR"), self.assertRaises(RuntimeError):
            await handle(Channel(self.twitch, id=1, login="owner"))
        self.assert_closed()

    async def test_critical_task_without_a_miner_reference_only_reraises(self):
        @task_wrapper(critical=True)
        async def orphan(*args):
            raise RuntimeError("orphan died")
        for args in ((), (SimpleNamespace(),), (SimpleNamespace(_twitch=object()),)):
            with self.assertLogs("TwitchDrops", "ERROR"), self.assertRaises(RuntimeError):
                await orphan(*args)
        self.assert_running()


class JsonPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name, "state.json")
        self.new_path = Path(temp.name, "state.json.new")

    def test_typed_values_round_trip(self):
        aware = datetime(2026, 10, 11, 12, 30, tzinfo=timezone.utc)
        contents = {
            "exclude": {"Rust"}, "mode": PriorityMode.ENDING_SOONEST,
            "proxy": URL("http://proxy.test:8080"), "seen": aware,
            "naive": datetime(2026, 10, 11, 12, 30), "plain": [1, "two"],
        }
        json_save(self.path, contents)
        self.assertFalse(self.new_path.exists())
        loaded = json_load(self.path, {}, merge=False)
        self.assertEqual(loaded, {**contents, "naive": aware})  # naive values are taken as UTC
        self.assertIsInstance(loaded["mode"], PriorityMode)

    def test_failed_save_keeps_the_previous_file_loadable(self):
        json_save(self.path, {"priority": ["Alpha"]})
        with self.assertRaises(TypeError):
            json_save(self.path, {"priority": ["Beta"], "bad": object()})
        self.assertEqual(json_load(self.path, {"priority": []}), {"priority": ["Alpha"]})
        self.assertFalse(self.new_path.exists())

    def test_sorted_save_orders_keys(self):
        json_save(self.path, {"b": 1, "a": 2}, sort=True)
        self.assertEqual(list(json.loads(self.path.read_text())), ["a", "b"])

    def test_values_of_unknown_types_fall_back_to_defaults(self):
        self.path.write_text(json.dumps({
            "when": {"__type": "Mystery", "data": 1},
            "nested": {"only": {"__type": "Mystery", "data": 2}},
            "kept": 5,
        }))
        defaults = {"when": "default", "nested": {"only": "default"}, "kept": 0}
        self.assertEqual(json_load(self.path, defaults), defaults | {"kept": 5})
        self.assertEqual(json_load(self.path, {}, merge=False), {"kept": 5})

    def test_merge_reconciles_nested_values_with_defaults(self):
        self.path.write_text(json.dumps({
            "nested": {"keep": 3, "wrong": "type", "stale": True}, "top": "x", "gone": 1,
        }))
        defaults = {"nested": {"keep": 0, "wrong": 0, "added": []}, "top": "", "new": False}
        self.assertEqual(json_load(self.path, defaults), {
            "nested": {"keep": 3, "wrong": 0, "added": []}, "top": "x", "new": False,
        })

    def test_unmerged_load_returns_the_file_as_saved(self):
        self.path.write_text(json.dumps({"gone": 1}))
        self.assertEqual(json_load(self.path, {"new": False}, merge=False), {"gone": 1})

    def test_missing_file_yields_a_copy_of_the_defaults(self):
        defaults = {"auto_claim": True}
        loaded = json_load(self.path, defaults)
        loaded["auto_claim"] = False
        self.assertEqual(defaults, {"auto_claim": True})


class GameTests(unittest.TestCase):
    def test_display_name_is_preferred_over_name(self):
        self.assertEqual(str(Game({"id": "1", "name": "rust", "displayName": "Rust"})), "Rust")
        self.assertEqual(str(Game({"id": "1", "name": "Rust", "displayName": None})), "Rust")

    def test_slug_is_derived_from_the_name_unless_twitch_supplies_one(self):
        derived = Game({"id": "1", "name": "Tom Clancy's Rainbow Six: Siege -- (2015)"})
        self.assertEqual(derived.slug, "tom-clancys-rainbow-six-siege-2015")
        supplied = Game({"id": "2", "name": "Counter-Strike", "slug": "counter-strike-2"})
        self.assertEqual(supplied.slug, "counter-strike-2")

    def test_games_are_identified_by_id(self):
        game = Game({"id": "33214", "name": "Fortnite"})
        renamed = Game({"id": "33214", "name": "Fortnite Battle Royale"})
        self.assertEqual(game, renamed)
        self.assertEqual(len({game, renamed}), 1)
        self.assertNotEqual(game, Game({"id": "1", "name": "Fortnite"}))
        self.assertNotEqual(game, "Fortnite")
        self.assertEqual(repr(game), "Game(33214, Fortnite)")

    def test_only_special_event_games_are_special(self):
        self.assertTrue(Game({"id": "509663", "name": "Special Events"}).is_special())
        self.assertTrue(Game({"id": "509672", "name": "Travel & Outdoors"}).is_special())
        self.assertFalse(Game({"id": "33214", "name": "Fortnite"}).is_special())


class SmallHelperTests(unittest.TestCase):
    def test_nonce_has_requested_length_and_alphabet(self):
        nonce = create_nonce(CHARS_HEX_LOWER, 16)
        self.assertEqual(len(nonce), 16)
        self.assertLessEqual(set(nonce), set(string.digits + "abcdef"))

    def test_format_traceback_renders_the_given_exception(self):
        try:
            raise ConnectionResetError("socket closed")
        except ConnectionResetError as exc:
            error = exc
        try:
            raise ValueError("unrelated, currently handled")
        except ValueError:
            text = format_traceback(error)
        self.assertIn("ConnectionResetError: socket closed", text)
        self.assertNotIn("unrelated", text)


@unittest.skipUnless(sys.platform in ("linux", "darwin"), "POSIX record locks")
class LockFileTests(unittest.TestCase):
    """main.py refuses to start a second miner when the lock is held by another process."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name, "lock.file")

    def lock(self):
        acquired, file = lock_file(self.path)
        self.addCleanup(file.close)
        return acquired

    def test_lock_held_by_another_process_is_refused_until_released(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; from pathlib import Path; from utils import lock_file;"
             "acquired, f = lock_file(Path(sys.argv[1])); print(acquired, flush=True);"
             "sys.stdin.read()", str(self.path)],
            cwd=REPO, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "True")
            self.assertFalse(self.lock())
        finally:
            holder.stdin.close()
            holder.wait(30)
            holder.stdout.close()
        self.assertTrue(self.lock())


if __name__ == "__main__":
    unittest.main()
