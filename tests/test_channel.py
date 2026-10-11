"""Risk row 2: channel status, stream parsing and the watch requests in channel.py.

Twitch is virtualized at the aiohttp session boundary: every request the miner makes is
redirected to an in-process server that answers as GQL, usher, the playlist CDN and the
segment edge would, keyed by the original host.
"""
import asyncio
import tempfile
import unittest
import warnings
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from yarl import URL

from channel import Channel
from constants import State
from exceptions import MinerException
from test_channel_selection import ALPHA, BETA, SelectionTestCase, directory_node, stream_info


LOGIN = "streamer1"
MASTER_PLAYLIST = (
    "#EXTM3U\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=6000000,VIDEO="chunked"\n'
    "https://video-weaver.test/v1/playlist/source.m3u8\n"
    '#EXT-X-STREAM-INF:BANDWIDTH=200000,VIDEO="160p30"\n'
    "https://video-weaver.test/v1/playlist/160p.m3u8\n"
)
CHUNK_PLAYLIST = (
    "#EXTM3U\n#EXT-X-TARGETDURATION:2\n"
    "#EXTINF:2.000,live\nhttps://video-edge.test/v1/segment/1.ts\n"
    "#EXTINF:2.000,live\nhttps://video-edge.test/v1/segment/2.ts\n"
)
USHER_PATH = f"usher.ttvnw.net/api/channel/hls/{LOGIN}.m3u8"
PLAYLIST_PATH = "video-weaver.test/v1/playlist/160p.m3u8"
SEGMENTS = ("video-edge.test/v1/segment/1.ts", "video-edge.test/v1/segment/2.ts")


class FakeTwitchUpstream:
    """GQL operations are answered by `gql[operationName](variables)`; other hosts by `routes`."""

    def __init__(self):
        self.requests = []
        self.gql = {}
        self.routes = {}
        app = web.Application()
        app.router.add_route("*", "/{host}/{path:.*}", self.handle)
        self.server = TestServer(app)

    async def handle(self, request):
        host, path = request.match_info["host"], request.match_info["path"]
        target = f"{host}/{path}"
        body = await request.json() if host == "gql.twitch.tv" else None
        self.requests.append(SimpleNamespace(
            method=request.method, target=target, query=dict(request.query),
            headers=dict(request.headers), body=body,
        ))
        if body is not None:
            return web.json_response(self.gql[body["operationName"]](body.get("variables", {})))
        status, text = self.routes.get((request.method, target), (404, "Not Found"))
        return web.Response(status=status, text=text)

    def made(self, method, target):
        return [r for r in self.requests if r.method == method and r.target == target]

    def operations(self, name):
        return [r.body["variables"] for r in self.requests if r.body and r.body["operationName"] == name]


class ChannelTestCase(SelectionTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.temp = tempfile.TemporaryDirectory()
        self.upstream = FakeTwitchUpstream()
        await self.upstream.server.start_server()
        base = str(self.upstream.server.make_url("/")).rstrip("/")

        with warnings.catch_warnings():
            # aiohttp discourages subclassing; this double only redirects to the virtual Twitch
            warnings.simplefilter("ignore", DeprecationWarning)

            class RedirectingSession(aiohttp.ClientSession):
                def request(self, method, url, **kwargs):
                    url = URL(url)
                    return super().request(
                        method, URL(f"{base}/{url.host}{url.raw_path_qs}", encoded=True), **kwargs)

        self.session_patchers = [
            patch("twitch.COOKIES_PATH", Path(self.temp.name, "cookies.jar")),
            patch("twitch.aiohttp.ClientSession", RedirectingSession),
            patch("channel.ONLINE_DELAY", timedelta(0)),
        ]
        for patcher in self.session_patchers:
            patcher.start()
        self.settings.proxy = URL()
        self.settings.connection_quality = 1
        auth = self.twitch._auth_state
        auth.device_id, auth.user_id, auth.access_token = "synthetic-device", 123, "synthetic-token"
        auth._logged_in.set()
        self.users = {}
        self.available = {}
        self.upstream.gql["VideoPlayerStreamInfoOverlayChannel"] = (
            lambda variables: {"data": {"user": self.users.get(variables["channel"])}})
        self.upstream.gql["DropsHighlightService_AvailableDrops"] = (
            lambda variables: {"data": {"channel": {
                "viewerDropCampaigns": self.available.get(variables["channelID"])}}})
        self.upstream.gql["PlaybackAccessToken"] = (
            lambda variables: {"data": {"streamPlaybackAccessToken": {
                "value": f"token-for-{variables['login']}", "signature": "synthetic-sig"}}})

    async def asyncTearDown(self):
        tasks = set(self.twitch._account_tasks)
        if self.twitch._qgl_limiter._reset_task is not None:
            tasks.add(self.twitch._qgl_limiter._reset_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.twitch._session is not None:
            await self.twitch._session.close()
        await self.upstream.server.close()
        for patcher in reversed(self.session_patchers):
            patcher.stop()
        self.temp.cleanup()
        await super().asyncTearDown()

    def channel(self, channel_id=1, **kwargs):
        channel = Channel(self.twitch, id=channel_id, login=f"streamer{channel_id}", **kwargs)
        self.twitch.channels[channel.id] = channel
        return channel

    def go_live(self, channel_id=1, game=ALPHA, *, viewers=10, title="live"):
        payload = stream_info(channel_id, game, viewers)
        payload["broadcastSettings"]["title"] = title
        self.users[f"streamer{channel_id}"] = payload

    def go_offline(self, channel_id=1):
        self.users[f"streamer{channel_id}"] = stream_info(channel_id, None)

    def gql_fails(self, operation):
        self.upstream.gql[operation] = lambda variables: {"errors": [{"message": "internal"}]}

    def logged(self, text):
        return [entry for entry in self.twitch.gui.logs if text in entry["message"]]


class ChannelConstructionTests(ChannelTestCase):
    async def test_directory_channel_is_online_with_the_listed_stream(self):
        channel = Channel.from_directory(
            self.twitch, directory_node(7, BETA, 321), drops_enabled=True)
        self.assertEqual((channel.id, channel.name, channel.acl_based), (7, "Streamer7", False))
        self.assertTrue(channel.online)
        self.assertEqual((channel.game.name, channel.viewers, channel.drops_enabled), ("Beta", 321, True))

    async def test_directory_channel_drops_flag_defaults_to_disabled(self):
        channel = Channel.from_directory(self.twitch, directory_node(7, BETA, 1))
        self.assertFalse(channel.drops_enabled)

    async def test_acl_channel_starts_offline_and_falls_back_to_login_for_its_name(self):
        channel = Channel.from_acl(self.twitch, {"id": "55", "name": "acl_login"})
        self.assertTrue(channel.acl_based)
        self.assertTrue(channel.offline)
        self.assertFalse(channel.online or channel.pending_online)
        self.assertEqual(channel.name, "acl_login")
        self.assertIsNone(channel.game)
        self.assertIsNone(channel.viewers)
        self.assertFalse(channel.drops_enabled)
        self.assertEqual(repr(channel), "Channel(acl_login, 55)")

    async def test_channels_are_identified_by_id_alone(self):
        directory = Channel.from_directory(self.twitch, directory_node(9, ALPHA, 1))
        acl = Channel.from_acl(self.twitch, {"id": "9", "name": "renamed", "displayName": "Renamed"})
        self.assertEqual(directory, acl)
        self.assertEqual(len({directory, acl}), 1)
        self.assertNotEqual(directory, Channel(self.twitch, id=10, login="streamer9"))
        self.assertNotEqual(directory, 9)
        self.assertEqual(repr(acl), "Channel(Renamed(renamed), 9)")

    async def test_viewer_count_is_only_tracked_while_online(self):
        offline = self.channel(2)
        offline.viewers = 50
        self.assertIsNone(offline.viewers)
        live = Channel.from_directory(self.twitch, directory_node(3, ALPHA, 1))
        live.viewers = 50
        self.assertEqual(live.viewers, 50)


class StreamStatusTests(ChannelTestCase):
    async def test_online_stream_is_parsed_from_stream_info(self):
        channel = self.channel()
        self.go_live(game=BETA, viewers=1234, title="Road to drops")
        self.assertTrue(await channel.update_stream())
        self.assertTrue(channel.online)
        self.assertEqual((channel.game.id, channel.game.name), (202, "Beta"))
        self.assertEqual((channel.viewers, channel._stream.title), (1234, "Road to drops"))
        self.assertEqual(channel._stream.broadcast_id, 70001)
        self.assertEqual(self.upstream.operations("VideoPlayerStreamInfoOverlayChannel"),
                         [{"channel": LOGIN}])

    async def test_stream_info_fills_a_missing_display_name_even_when_offline(self):
        channel = self.channel()
        self.go_offline()
        self.assertFalse(await channel.update_stream())
        self.assertTrue(channel.offline)
        self.assertEqual(channel.name, "ACL1")

    async def test_known_display_name_is_kept(self):
        channel = self.channel(display_name="Original")
        self.go_live()
        await channel.update_stream()
        self.assertEqual(channel.name, "Original")

    async def test_stream_without_game_is_online_with_no_game(self):
        channel = self.channel()
        self.go_live()
        self.users[LOGIN]["broadcastSettings"]["game"] = None
        self.assertTrue(await channel.update_stream())
        self.assertIsNone(channel.game)

    async def test_unknown_user_is_offline(self):
        channel = Channel.from_directory(self.twitch, directory_node(1, ALPHA, 5))
        self.assertFalse(await channel.update_stream())
        self.assertTrue(channel.offline)

    async def test_failed_stream_info_request_names_the_channel(self):
        channel = self.channel()
        self.gql_fails("VideoPlayerStreamInfoOverlayChannel")
        with self.assertRaisesRegex(MinerException, f"Channel: {LOGIN}"):
            await channel.get_stream()

    async def test_drops_are_assumed_enabled_without_the_available_drops_check(self):
        channel = self.channel()
        self.go_live()
        await channel.update_stream()
        self.assertTrue(channel.drops_enabled)
        self.assertEqual(self.upstream.operations("DropsHighlightService_AvailableDrops"), [])


class AvailableDropsCheckTests(ChannelTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.settings.available_drops_check = True
        self.campaign = self.add_campaign(ALPHA)
        self.go_live()

    async def test_drops_enabled_when_channel_offers_an_earnable_campaign(self):
        channel = self.channel()
        self.available["1"] = [{"id": self.campaign.id}]
        await channel.update_stream()
        self.assertTrue(channel.drops_enabled)
        self.assertEqual(self.upstream.operations("DropsHighlightService_AvailableDrops"),
                         [{"channelID": "1"}])

    async def test_drops_disabled_when_channel_offers_no_campaigns(self):
        channel = self.channel()
        self.available["1"] = None
        await channel.update_stream()
        self.assertTrue(channel.online)
        self.assertFalse(channel.drops_enabled)

    async def test_drops_disabled_for_unknown_or_unearnable_campaigns(self):
        restricted = self.add_campaign(BETA, acl=[99])  # this channel isn't on its ACL
        channel = self.channel()
        self.available["1"] = [{"id": "campaign-unknown"}, {"id": restricted.id}]
        await channel.update_stream()
        self.assertFalse(channel.drops_enabled)

    async def test_failed_available_drops_check_keeps_the_stream_with_drops_disabled(self):
        channel = self.channel()
        self.gql_fails("DropsHighlightService_AvailableDrops")
        self.assertTrue(await channel.update_stream())
        self.assertTrue(channel.online)
        self.assertFalse(channel.drops_enabled)

    async def test_bulk_update_applies_the_supplied_available_drops(self):
        channel = self.channel()
        channel.external_update(stream_info(1, ALPHA), [{"id": self.campaign.id}])
        self.assertTrue(channel.online and channel.drops_enabled)
        channel.external_update(stream_info(1, ALPHA), [])
        self.assertTrue(channel.online)
        self.assertFalse(channel.drops_enabled)
        channel.external_update(stream_info(1, None), [{"id": self.campaign.id}])
        self.assertTrue(channel.offline)
        self.assertEqual(self.upstream.requests, [])


class StatusTransitionTests(ChannelTestCase):
    """stream-up/down events reach the miner through Twitch.process_stream_state."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.add_campaign(ALPHA)
        self.want(ALPHA)

    async def event(self, channel, msg_type, **data):
        await self.twitch.process_stream_state(channel.id, {"type": msg_type, **data})

    async def settle(self, channel):
        await channel._pending_stream_up

    async def test_stream_up_is_pending_until_the_delayed_check_confirms_it(self):
        channel = self.channel()
        self.go_live()
        await self.event(channel, "stream-up")
        self.assertTrue(channel.pending_online)
        self.assertFalse(channel.online or channel.offline)
        self.assertEqual(self.upstream.requests, [])
        self.assertIn(channel.id, self.twitch.gui.channels_map)
        await self.settle(channel)
        self.assertTrue(channel.online)
        self.assertFalse(channel.pending_online)

    async def test_repeated_stream_up_schedules_a_single_check(self):
        channel = self.channel()
        self.go_live()
        await self.event(channel, "stream-up")
        pending = channel._pending_stream_up
        await self.event(channel, "stream-up")
        self.assertIs(channel._pending_stream_up, pending)
        await self.settle(channel)
        self.assertEqual(len(self.upstream.operations("VideoPlayerStreamInfoOverlayChannel")), 1)

    async def test_channel_going_online_with_a_wanted_game_is_watched(self):
        channel = self.channel()
        self.go_live(viewers=77)
        await self.event(channel, "stream-up")
        await self.settle(channel)
        self.assertIs(self.twitch.watching_channel.get_with_default(None), channel)
        shown = self.twitch.gui.channels_map[channel.id]
        self.assertEqual((shown["online"], shown["viewers"], shown["watching"]), (True, 77, True))

    async def test_stream_up_that_turns_out_offline_leaves_the_channel_offline(self):
        channel = self.channel()
        self.go_offline()
        await self.event(channel, "stream-up")
        await self.settle(channel)
        self.assertTrue(channel.offline)
        self.assertIsNone(self.twitch.watching_channel.get_with_default(None))
        self.assertFalse(self.twitch.gui.channels_map[channel.id]["online"])

    async def test_stream_down_before_the_delayed_check_cancels_it(self):
        channel = self.channel()
        self.go_live()
        await self.event(channel, "stream-up")
        pending = channel._pending_stream_up
        await self.event(channel, "stream-down")
        with suppress(asyncio.CancelledError):
            await pending
        self.assertTrue(pending.cancelled())
        self.assertTrue(channel.offline)
        self.assertEqual(self.upstream.requests, [])
        self.assertFalse(self.twitch.gui.channels_map[channel.id]["online"])

    async def test_watched_channel_going_offline_requests_a_channel_switch(self):
        channel = self.channel()
        self.go_live()
        await channel.update_stream()
        self.assertIs(self.twitch.watching_channel.get_with_default(None), channel)
        self.twitch._state = State.IDLE
        await self.event(channel, "stream-down")
        self.assertTrue(channel.offline)
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)
        self.assertFalse(self.twitch.gui.channels_map[channel.id]["online"])

    async def test_stream_down_for_an_offline_channel_changes_nothing(self):
        channel = self.channel()
        self.twitch._state = State.IDLE
        await self.event(channel, "stream-down")
        self.assertTrue(channel.offline)
        self.assertIs(self.twitch._state, State.IDLE)
        self.assertNotIn(channel.id, self.twitch.gui.channels_map)

    async def test_viewcount_updates_online_channel_and_rechecks_offline_one(self):
        live = Channel.from_directory(self.twitch, directory_node(2, ALPHA, 5), drops_enabled=True)
        self.twitch.channels[live.id] = live
        await self.event(live, "viewcount", viewers=999)
        self.assertEqual(self.twitch.gui.channels_map[live.id]["viewers"], 999)
        offline = self.channel()
        self.go_live()
        await self.event(offline, "viewcount", viewers=5)
        self.assertTrue(offline.pending_online)
        await self.settle(offline)
        self.assertTrue(offline.online)

    async def test_stream_update_for_unwatchable_watched_channel_requests_switch(self):
        channel = self.channel()
        self.go_live()
        await channel.update_stream()
        self.twitch._state = State.IDLE
        self.go_live(game=BETA)  # switched to a game the user doesn't want
        await channel.update_stream()
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)

    async def test_removing_a_channel_cancels_its_pending_check(self):
        channel = self.channel()
        self.go_live()
        await self.event(channel, "stream-up")
        self.twitch.gui.channels.display(channel)
        pending = channel._pending_stream_up
        channel.remove()
        with suppress(asyncio.CancelledError):
            await pending
        self.assertTrue(pending.cancelled())
        self.assertNotIn(channel.id, self.twitch.gui.channels_map)
        self.assertEqual(self.upstream.requests, [])


class SendWatchTests(ChannelTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.ch = self.channel()
        self.go_live()
        await self.ch.update_stream()
        self.upstream.requests.clear()
        self.upstream.routes.update({
            ("GET", USHER_PATH): (200, MASTER_PLAYLIST),
            ("GET", PLAYLIST_PATH): (200, CHUNK_PLAYLIST),
            ("HEAD", SEGMENTS[0]): (200, ""),
            ("HEAD", SEGMENTS[1]): (200, ""),
        })

    async def test_offline_channel_sends_nothing(self):
        self.assertFalse(await self.channel(5).send_watch())
        self.assertEqual(self.upstream.requests, [])

    async def test_watch_fetches_lowest_quality_playlist_and_heads_every_segment(self):
        self.assertTrue(await self.ch.send_watch())
        self.assertEqual(self.upstream.operations("PlaybackAccessToken")[0]["login"], LOGIN)
        usher, = self.upstream.made("GET", USHER_PATH)
        self.assertEqual(usher.query, {"sig": "synthetic-sig", "token": f"token-for-{LOGIN}"})
        playlist, = self.upstream.made("GET", PLAYLIST_PATH)
        self.assertEqual(playlist.headers.get("Connection"), "close")
        self.assertEqual([r.target for r in self.upstream.requests if r.method == "HEAD"],
                         list(SEGMENTS))

    async def test_stream_url_is_reused_across_watch_ticks(self):
        self.assertTrue(await self.ch.send_watch())
        self.assertTrue(await self.ch.send_watch())
        self.assertEqual(len(self.upstream.operations("PlaybackAccessToken")), 1)
        self.assertEqual(len(self.upstream.made("GET", USHER_PATH)), 1)
        self.assertEqual(len(self.upstream.made("GET", PLAYLIST_PATH)), 2)

    async def test_usher_error_sets_the_channel_offline_and_fails(self):
        self.add_campaign(ALPHA)
        self.want(ALPHA)
        self.twitch.watch(self.ch)
        self.twitch._state = State.IDLE
        self.upstream.routes[("GET", USHER_PATH)] = (
            403, '[{"type":"error","error":"Content is restricted","error_code":"unauthorized"}]')
        self.assertFalse(await self.ch.send_watch())
        self.assertTrue(self.ch.offline)
        self.assertIs(self.twitch._state, State.CHANNEL_SWITCH)
        self.assertTrue(self.logged('Stream URL get error: "Content is restricted"'))
        self.assertEqual(self.upstream.made("GET", PLAYLIST_PATH), [])

    async def test_usher_json_without_error_fails_and_retries_next_tick(self):
        self.upstream.routes[("GET", USHER_PATH)] = (200, '{"status": "pending"}')
        self.assertFalse(await self.ch.send_watch())
        self.assertTrue(self.ch.online)
        self.upstream.routes[("GET", USHER_PATH)] = (200, MASTER_PLAYLIST)
        self.assertTrue(await self.ch.send_watch())
        self.assertEqual(len(self.upstream.made("GET", USHER_PATH)), 2)

    async def test_malformed_usher_playlist_is_printed_and_raised(self):
        self.upstream.routes[("GET", USHER_PATH)] = (200, "#EXTM3U\nhttp://[broken")
        with self.assertRaises(ValueError):
            await self.ch.send_watch()
        self.assertTrue(self.logged("http://[broken"))

    async def test_missing_chunk_playlist_fails_without_heading_segments(self):
        self.upstream.routes[("GET", PLAYLIST_PATH)] = (404, "")
        self.assertFalse(await self.ch.send_watch())
        self.assertEqual([r for r in self.upstream.requests if r.method == "HEAD"], [])

    async def test_chunk_playlist_error_document_fails_and_is_logged(self):
        # Twitch's error body carries a "url" value with unescaped quotes; it must still parse
        self.upstream.routes[("GET", PLAYLIST_PATH)] = (
            200, '[{"url": "https://x.test/?a="b"}", "error": "Segment unavailable"}]')
        self.assertFalse(await self.ch.send_watch())
        self.assertTrue(self.logged('Send watch error: "Segment unavailable"'))
        self.assertEqual([r for r in self.upstream.requests if r.method == "HEAD"], [])

    async def test_chunk_playlist_json_without_error_fails(self):
        self.upstream.routes[("GET", PLAYLIST_PATH)] = (200, '{"status": "ok"}')
        self.assertFalse(await self.ch.send_watch())
        self.assertEqual([r for r in self.upstream.requests if r.method == "HEAD"], [])

    async def test_rejected_segment_fails_and_stops_heading(self):
        self.upstream.routes[("HEAD", SEGMENTS[0])] = (403, "")
        self.assertFalse(await self.ch.send_watch())
        self.assertEqual([r.target for r in self.upstream.requests if r.method == "HEAD"],
                         [SEGMENTS[0]])


if __name__ == "__main__":
    unittest.main()
