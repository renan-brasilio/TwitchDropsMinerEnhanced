"""Regression coverage for the Twitch PubSub websocket pool (risk row 7).

Twitch PubSub is virtualized by a local aiohttp websocket server; the miner's real
session factory, websocket pool and connection loop run unchanged, only the
`wss://pubsub-edge.twitch.tv/v1` URL is redirected at the aiohttp session boundary.
"""
import asyncio
import json
import logging
import tempfile
import unittest
import warnings
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from yarl import URL

from constants import MAX_WEBSOCKETS, WS_TOPICS_LIMIT, PriorityMode, WebsocketTopic
from exceptions import MinerException
from twitch import Twitch


PUBSUB_URL = "wss://pubsub-edge.twitch.tv/v1"
AUTH_TOKEN = "synthetic-pubsub-token"
WAIT = 5


class FakeConnection:
    def __init__(self, ws):
        self.ws = ws
        self.messages = []
        self.listening = set()
        self.closed = False

    def of_type(self, msg_type):
        return [message for message in self.messages if message["type"] == msg_type]

    async def send(self, payload):
        await self.ws.send_json(payload)


class FakePubSub:
    """Minimal Twitch PubSub: tracks LISTEN/UNLISTEN per connection and answers PINGs."""

    def __init__(self):
        self.connections = []
        self.answer_pings = True
        self.changed = asyncio.Condition()
        app = web.Application()
        app.router.add_get("/v1", self.handle)
        self.server = TestServer(app)

    async def notify(self):
        async with self.changed:
            self.changed.notify_all()

    async def until(self, predicate):
        async with self.changed:
            await asyncio.wait_for(self.changed.wait_for(predicate), WAIT)

    async def handle(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        conn = FakeConnection(ws)
        self.connections.append(conn)
        await self.notify()
        async for raw in ws:
            if raw.type is not aiohttp.WSMsgType.TEXT:
                continue
            message = json.loads(raw.data)
            conn.messages.append(message)
            if message["type"] == "PING" and self.answer_pings:
                await ws.send_json({"type": "PONG"})
            elif message["type"] == "LISTEN":
                conn.listening.update(message["data"]["topics"])
                await ws.send_json({"type": "RESPONSE", "nonce": message["nonce"], "error": ""})
            elif message["type"] == "UNLISTEN":
                conn.listening.difference_update(message["data"]["topics"])
                await ws.send_json({"type": "RESPONSE", "nonce": message["nonce"], "error": ""})
            await self.notify()
        conn.closed = True
        await self.notify()
        return ws

    def open_connections(self):
        return [conn for conn in self.connections if not conn.closed]


class PubSubPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.pubsub = FakePubSub()
        await self.pubsub.server.start_server()
        self.requested_urls = []
        redirect = self.pubsub.server.make_url("/v1")
        requested_urls = self.requested_urls

        with warnings.catch_warnings():
            # aiohttp discourages subclassing; this double only redirects the PubSub URL
            warnings.simplefilter("ignore", DeprecationWarning)

            class RedirectingSession(aiohttp.ClientSession):
                def ws_connect(self, url, **kwargs):
                    requested_urls.append(str(url))
                    return super().ws_connect(redirect if str(url) == PUBSUB_URL else url, **kwargs)

        self.patchers = [
            patch.dict("os.environ", {"API_KEY": ""}),
            patch("twitch.COOKIES_PATH", Path(self.temp.name, "cookies.jar")),
            patch("twitch.aiohttp.ClientSession", RedirectingSession),
        ]
        for patcher in self.patchers:
            patcher.start()
        settings = SimpleNamespace(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, dump=False, proxy=URL(), connection_quality=1,
        )
        self.twitch = Twitch(settings)
        # an already validated login: the pool must authenticate LISTENs with this token
        auth = self.twitch._auth_state
        auth.device_id, auth.user_id, auth.access_token = "synthetic-device", 123, AUTH_TOKEN
        auth._logged_in.set()
        self.received = asyncio.Queue()

    async def asyncTearDown(self):
        await self.twitch.shutdown()
        logging.getLogger("TwitchDrops").removeHandler(self.twitch.gui._log_handler)
        await self.pubsub.server.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    @property
    def pool(self):
        return self.twitch.websocket

    def topics(self, count, *, start=1):
        async def process(target_id, message):
            await self.received.put((target_id, message))
        return [WebsocketTopic("Channel", "StreamState", channel_id, process)
                for channel_id in range(start, start + count)]

    async def start_pool(self, topics):
        await self.pool.start()
        self.pool.add_topics(topics)
        expected = {str(topic) for topic in topics}
        await self.pubsub.until(lambda: set().union(
            *(conn.listening for conn in self.pubsub.open_connections())) == expected)

    async def test_topics_are_sharded_across_sockets_without_exceeding_the_per_socket_limit(self):
        topics = self.topics(2 * WS_TOPICS_LIMIT + 1)

        await self.start_pool(topics)

        connections = self.pubsub.open_connections()
        self.assertEqual(len(connections), 3)
        self.assertEqual({len(conn.listening) for conn in connections}, {WS_TOPICS_LIMIT, 1})
        listened = [topic for conn in connections for message in conn.of_type("LISTEN")
                    for topic in message["data"]["topics"]]
        self.assertCountEqual(listened, [str(topic) for topic in topics])
        self.assertEqual({message["data"]["auth_token"] for conn in connections
                          for message in conn.of_type("LISTEN")}, {AUTH_TOKEN})
        self.assertEqual(set(self.requested_urls), {PUBSUB_URL})

    async def test_adding_already_subscribed_topics_does_not_subscribe_them_again(self):
        first = self.topics(WS_TOPICS_LIMIT)
        self.pool.add_topics(first)

        self.pool.add_topics(first + self.topics(1, start=1000))

        self.assertEqual([len(ws.topics) for ws in self.pool.websockets], [WS_TOPICS_LIMIT, 1])
        self.assertEqual(list(self.pool.websockets[1].topics), [str(self.topics(1, start=1000)[0])])

    async def test_exceeding_the_pool_capacity_is_rejected(self):
        with self.assertRaisesRegex(MinerException, "Maximum topics limit"):
            self.pool.add_topics(self.topics(MAX_WEBSOCKETS * WS_TOPICS_LIMIT + 1))

    async def test_message_is_dispatched_to_its_topic_callback_with_parsed_payload(self):
        await self.start_pool(self.topics(2))
        conn = self.pubsub.open_connections()[0]
        payload = {"type": "viewcount", "viewers": 42}

        await conn.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.2", "message": json.dumps(payload)}})

        self.assertEqual(await asyncio.wait_for(self.received.get(), WAIT), (2, payload))

    async def test_message_for_an_unsubscribed_topic_is_ignored(self):
        await self.start_pool(self.topics(1))
        conn = self.pubsub.open_connections()[0]

        await conn.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.999", "message": json.dumps({"stray": True})}})
        await conn.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.1", "message": json.dumps({"seq": 2})}})

        self.assertEqual(await asyncio.wait_for(self.received.get(), WAIT), (1, {"seq": 2}))
        self.assertTrue(self.received.empty())

    async def test_removing_topics_unlistens_them_and_stops_their_dispatch(self):
        await self.start_pool(self.topics(3))
        conn = self.pubsub.open_connections()[0]

        self.pool.remove_topics(["video-playback-by-id.2"])
        await self.pubsub.until(lambda: conn.of_type("UNLISTEN"))

        self.assertEqual([m["data"]["topics"] for m in conn.of_type("UNLISTEN")],
                         [["video-playback-by-id.2"]])
        self.assertEqual(conn.listening, {"video-playback-by-id.1", "video-playback-by-id.3"})
        await conn.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.2", "message": json.dumps({"stale": True})}})
        await conn.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.3", "message": json.dumps({"live": True})}})
        self.assertEqual(await asyncio.wait_for(self.received.get(), WAIT), (3, {"live": True}))
        self.assertTrue(self.received.empty())

    async def test_removing_topics_recycles_a_surplus_socket_into_the_remaining_one(self):
        topics = self.topics(WS_TOPICS_LIMIT + 1)
        await self.start_pool(topics)
        first, second = sorted(self.pubsub.open_connections(), key=lambda conn: -len(conn.listening))
        moved = next(iter(second.listening))
        dropped = next(iter(first.listening))

        self.pool.remove_topics([dropped])
        await self.pubsub.until(lambda: second.closed and moved in first.listening)

        self.assertEqual(len(self.pool.websockets), 1)
        self.assertEqual(first.listening, {str(topic) for topic in topics} - {dropped})
        self.assertEqual(self.pubsub.open_connections(), [first])

    async def test_reconnect_request_reestablishes_the_socket_and_resubscribes_topics(self):
        topics = self.topics(3)
        await self.start_pool(topics)
        old = self.pubsub.open_connections()[0]

        await old.send({"type": "RECONNECT"})
        await self.pubsub.until(lambda: old.closed and len(self.pubsub.connections) == 2
                                and len(self.pubsub.connections[1].listening) == 3)

        new = self.pubsub.connections[1]
        self.assertEqual(new.listening, {str(topic) for topic in topics})
        await new.send({"type": "MESSAGE", "data": {
            "topic": "video-playback-by-id.1", "message": json.dumps({"after": "reconnect"})}})
        self.assertEqual(await asyncio.wait_for(self.received.get(), WAIT), (1, {"after": "reconnect"}))

    async def test_server_dropping_the_connection_reconnects_and_resubscribes_topics(self):
        topics = self.topics(3)
        await self.start_pool(topics)
        old = self.pubsub.open_connections()[0]

        await old.ws.close()
        await self.pubsub.until(lambda: len(self.pubsub.connections) == 2
                                and len(self.pubsub.connections[1].listening) == 3)

        self.assertEqual(self.pubsub.connections[1].listening, {str(topic) for topic in topics})
        self.assertTrue(self.pool.websockets[0].connected)

    async def test_answered_pings_keep_the_socket_open(self):
        with patch("websocket.PING_INTERVAL", timedelta(seconds=1)), \
                patch("websocket.PING_TIMEOUT", timedelta(seconds=0.2)):
            await self.start_pool(self.topics(1))
            conn = self.pubsub.connections[0]

            await self.pubsub.until(lambda: len(conn.of_type("PING")) >= 2)

        self.assertFalse(conn.closed)
        self.assertEqual(len(self.pubsub.connections), 1)

    async def test_missing_pong_reconnects_and_resubscribes_topics(self):
        self.pubsub.answer_pings = False
        with patch("websocket.PING_INTERVAL", timedelta(seconds=60)), \
                patch("websocket.PING_TIMEOUT", timedelta(seconds=0.2)):
            await self.start_pool(self.topics(1))
            first = self.pubsub.connections[0]

            await self.pubsub.until(lambda: first.closed and len(self.pubsub.connections) >= 2
                                    and self.pubsub.connections[1].listening)

        self.assertEqual(self.pubsub.connections[1].listening, {"video-playback-by-id.1"})


if __name__ == "__main__":
    unittest.main()
