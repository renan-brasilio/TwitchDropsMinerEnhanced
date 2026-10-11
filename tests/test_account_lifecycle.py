"""Regression coverage for isolated Twitch account transitions."""
import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp.test_utils import TestClient, TestServer

from api import APIServer
from channel import Channel
from constants import ClientType, State
from exceptions import LoginException
from settings import Settings
from twitch import Twitch
from websocket import Websocket


class TwitchResponse:
    status = 200

    def __init__(self, session, url, headers):
        self.session = session
        self.url = str(url)
        self.headers = headers
        self.released = False

    async def read(self):
        if self.url.endswith('/validate') and self.session.block == 'body':
            self.session.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.session.finished.set()
        return b'{}'

    async def text(self, *args):
        return ''

    async def json(self, **kwargs):
        token = self.headers.get('Authorization', '').removeprefix('OAuth ')
        user_id, login = {
            'test-old': ('101', 'previous_account'),
            'test-new': ('202', 'next_account'),
            'oauth-device-token': ('303', 'oauth_account'),
            'env-token': ('404', 'environment_account'),
        }[token]
        return {'user_id': user_id, 'login': login, 'client_id': ClientType.WEB.CLIENT_ID}

    def release(self):
        self.released = True


class TwitchSession:
    def __init__(self, *, cookie_jar, timeout, **kwargs):
        self.cookie_jar = cookie_jar
        self.timeout = timeout
        self.closed = False
        self.block = None
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.responses = []
        self.cookie_jar.update_cookies({'unique_id': 'test-device'}, ClientType.WEB.CLIENT_URL)

    async def request(self, method, url, **kwargs):
        if str(url).endswith('/validate') and self.block == 'request':
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.finished.set()
        response = TwitchResponse(self, url, kwargs.get('headers', {}))
        self.responses.append(response)
        return response

    async def close(self):
        self.closed = True


class AccountLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings_path = Path(self.temp.name, 'settings.json')
        self.cookies_path = Path(self.temp.name, 'cookies.jar')
        self.patchers = [
            patch('settings.SETTINGS_PATH', self.settings_path),
            patch('twitch.COOKIES_PATH', self.cookies_path),
            patch.dict('os.environ', {'API_KEY': '', 'TWITCH_AUTH_TOKEN': 'env-token'}),
            patch('twitch.aiohttp.ClientSession', TwitchSession),
            patch('twitch.aiohttp.TCPConnector', return_value=None),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.instances = []
        self.owners = []
        self.twitch = self.new_twitch()
        self.settings = self.twitch.settings
        self.settings.priority = ['Overwatch']
        self.settings.exclude = {'Rust'}
        self.settings.auto_claim = False
        self.settings.auth_token = 'test-old'
        self.settings.save(force=True)
        self.hub = self.twitch.gui
        self.client = TestClient(TestServer(APIServer(self.twitch, self.hub)._app))
        await self.client.start_server()

    def new_twitch(self):
        twitch = Twitch(Settings(SimpleNamespace(dump=False)))
        self.instances.append(twitch)
        return twitch

    async def asyncTearDown(self):
        for twitch in self.instances:
            twitch.gui.close()
        for owner in self.owners:
            await asyncio.wait_for(owner, 5)
        await self.client.close()
        for twitch in self.instances:
            await twitch.shutdown()
            logging.getLogger('TwitchDrops').removeHandler(twitch.gui._log_handler)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def start_owner(self, twitch=None):
        owner = asyncio.create_task((twitch or self.twitch).run())
        self.owners.append(owner)
        return owner

    async def status(self):
        response = await self.client.get('/api/status')
        self.assertEqual(response.status, 200)
        return await response.json()

    async def test_disconnect_removes_disk_cookie_without_session_and_restart_stays_disconnected(self):
        self.cookies_path.write_bytes(b'synthetic-cookie')
        response = await self.client.post('/api/auth/disconnect')
        self.assertEqual(response.status, 200)
        self.assertFalse(self.cookies_path.exists())
        persisted = json.loads(self.settings_path.read_text())
        self.assertEqual(persisted['auth_token'], '')
        self.assertTrue(persisted['auth_disconnected'])
        self.assertEqual(persisted['priority'], ['Overwatch'])
        self.assertFalse(persisted['auto_claim'])

        restarted = self.new_twitch()
        owner = self.start_owner(restarted)
        with self.assertRaises(LoginException):
            await restarted.get_auth()
        await asyncio.sleep(0)
        self.assertFalse(owner.done())
        self.assertIsNone(restarted._session)
        restarted.gui.close()  # main.py closes the hub, not Twitch.close().
        await asyncio.wait_for(owner, 3)

        for route in ('inventory/refresh', 'control/pause', 'control/resume',
                      'control/reload', 'control/switch', 'control/claim', 'control/watch'):
            with self.subTest(route=route):
                response = await self.client.post('/api/' + route, json={'channel_id': 1})
                self.assertEqual(response.status, 409)
                self.assertIn('error', await response.json())
        response = await self.client.post('/api/settings', json={'priority': ['Valorant'], 'auto_claim': True})
        self.assertEqual(response.status, 200)
        self.assertTrue(self.settings.auth_disconnected)
        self.assertEqual(self.twitch._state, State.IDLE)
        self.assertIsNone(self.twitch._session)
        self.assertTrue((await self.status())['auth_disconnected'])

    async def assert_validation_is_joined(self, phase):
        session = await self.twitch.get_session()
        session.block = phase
        validating = asyncio.create_task(self.twitch.get_auth())
        await asyncio.wait_for(session.started.wait(), 2)
        response = await self.client.post('/api/auth/disconnect')
        self.assertEqual(response.status, 200)
        self.assertTrue(validating.cancelled())
        self.assertTrue(session.finished.is_set())
        self.assertTrue(session.closed)
        self.assertFalse(self.cookies_path.exists())
        self.assertFalse(self.twitch._auth_state._logged_in.is_set())
        self.assertFalse(hasattr(self.twitch._auth_state, 'access_token'))
        self.assertFalse(self.hub.is_logged_in)
        self.assertIsNone(self.hub.device_code_data)
        self.assertIsNone((await self.status())['username'])
        if phase == 'body':
            self.assertTrue(session.responses[-1].released)

    async def test_disconnect_joins_inflight_validation_request(self):
        await self.assert_validation_is_joined('request')

    async def test_disconnect_joins_inflight_validation_body(self):
        await self.assert_validation_is_joined('body')

    async def test_disconnect_cancels_inflight_refresh_and_does_not_publish_stale_inventory(self):
        started = asyncio.Event()
        finished = asyncio.Event()

        async def pending_gql(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                finished.set()

        with patch.object(self.twitch, 'gql_request', side_effect=pending_gql):
            refresh = asyncio.create_task(self.client.post('/api/inventory/refresh'))
            await asyncio.wait_for(started.wait(), 2)
            response = await self.client.post('/api/auth/disconnect')
            self.assertEqual(response.status, 200)
            refreshed = await asyncio.wait_for(refresh, 2)
            self.assertEqual(refreshed.status, 409)
        self.assertTrue(finished.is_set())
        self.assertEqual(self.hub.get_inventory(), [])
        queue = self.hub.subscribe()
        await asyncio.sleep(0)
        self.assertTrue(queue.empty())
        self.hub.unsubscribe(queue)
        self.assertTrue((await self.status())['auth_disconnected'])

    async def test_token_replacement_resets_old_identity_and_validates_new_account(self):
        await self.twitch.get_auth()
        self.assertEqual((await self.status())['username'], 'previous_account')
        old_session = self.twitch._session
        self.hub.active_drop = {'id': 'old-drop'}
        self.hub.active_channel = {'id': 1}
        self.hub.device_code_data = {'uri': 'https://example.com', 'code': 'OLD'}
        self.hub.channels_map[1] = {'id': 1}
        self.twitch._campaigns['old'] = object()
        self.twitch._drops['old'] = object()
        self.twitch.inventory.append(object())
        socket = Websocket(self.twitch.websocket, 0)
        socket.topics['old-topic'] = object()
        socket._submitted.add('old-topic')
        self.twitch.websocket.websockets.append(socket)
        response = await self.client.post('/api/auth/token', json={'token': ' test-new '})
        self.assertEqual(response.status, 200)
        self.assertTrue(old_session.closed)
        status = await self.status()
        self.assertFalse(status['auth_disconnected'])
        self.assertFalse(status['is_logged_in'])
        self.assertIsNone(status['username'])
        self.assertIsNone(status['active_drop'])
        self.assertIsNone(status['active_channel'])
        self.assertIsNone(status['device_code'])
        self.assertEqual(status['websockets'], [])
        self.assertEqual(self.twitch.inventory, [])
        self.assertEqual(self.twitch._campaigns, {})
        self.assertEqual(socket.topics, {})
        await self.twitch.get_auth()
        status = await self.status()
        self.assertEqual((status['user_id'], status['username']), (202, 'next_account'))
        self.assertTrue(status['is_logged_in'])
        self.assertNotIn('test-new', json.dumps(status))
        persisted = Settings(SimpleNamespace(dump=False))
        self.assertEqual(persisted.auth_token, 'test-new')
        self.assertEqual(persisted.priority, ['Overwatch'])
        self.assertEqual(persisted.exclude, {'Rust'})
        self.assertFalse(persisted.auto_claim)
        self.assertFalse(persisted.auth_disconnected)

    async def test_explicit_connect_uses_oauth_not_environment_and_run_survives_disconnect(self):
        await self.twitch.disconnect()
        validated = asyncio.Event()
        stopped = asyncio.Event()

        async def mine():
            await self.twitch.get_auth()
            validated.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with patch.object(self.twitch, '_run', side_effect=mine), patch.object(
            self.twitch._auth_state, '_oauth_login', new=AsyncMock(return_value='oauth-device-token')
        ) as oauth:
            owner = self.start_owner()
            response = await self.client.post('/api/auth/connect')
            self.assertEqual(response.status, 200)
            await asyncio.wait_for(validated.wait(), 2)
            oauth.assert_awaited_once()
            self.assertEqual((await self.status())['username'], 'oauth_account')
            response = await self.client.post('/api/auth/disconnect')
            self.assertEqual(response.status, 200)
            self.assertTrue(stopped.is_set())
            self.assertFalse(owner.done())
            self.hub.close()
            await asyncio.wait_for(owner, 3)

    async def test_new_token_cancels_active_login_and_owner_validates_replacement(self):
        old_session = await self.twitch.get_session()
        old_session.block = 'body'
        validated = asyncio.Event()

        async def mine():
            await self.twitch.get_auth()
            validated.set()
            await asyncio.Event().wait()

        with patch.object(self.twitch, '_run', side_effect=mine):
            owner = self.start_owner()
            await asyncio.wait_for(old_session.started.wait(), 2)
            response = await self.client.post('/api/auth/token', json={'token': 'test-new'})
            self.assertEqual(response.status, 200)
            self.assertTrue(old_session.finished.is_set())
            self.assertTrue(old_session.closed)
            await asyncio.wait_for(validated.wait(), 2)
            self.assertEqual((await self.status())['username'], 'next_account')
            self.assertFalse(owner.done())
            self.hub.close()
            await asyncio.wait_for(owner, 3)

    async def test_pending_disconnect_blocks_controls_before_it_acquires_account_lock(self):
        await self.twitch._account_lock.acquire()
        disconnect = asyncio.create_task(self.twitch.disconnect())
        try:
            await asyncio.sleep(0)  # disconnect is now waiting for the current mutation.
            status = await self.status()
            self.assertTrue(status['auth_pending'])
            response = await self.client.post('/api/control/resume')
            self.assertEqual(response.status, 409)
            self.assertEqual(self.twitch._state, State.IDLE)
            self.assertIsNone(self.twitch._session)
        finally:
            self.twitch._account_lock.release()
            await asyncio.wait_for(disconnect, 3)

    async def test_disconnect_joins_socket_callbacks_channel_delay_mining_and_maintenance(self):
        entered = [asyncio.Event() for _ in range(4)]
        finished = [asyncio.Event() for _ in range(4)]

        async def pending(index):
            entered[index].set()
            try:
                await asyncio.Event().wait()
            finally:
                finished[index].set()

        socket = Websocket(self.twitch.websocket, 0)
        self.twitch.websocket.websockets.append(socket)
        socket.topics['callback'] = lambda message: pending(0)
        socket._handle_message({'data': {'topic': 'callback', 'message': '{}'}})
        channel = Channel(self.twitch, id=1, login='synthetic_streamer')
        with patch.object(Channel, '_online_delay', lambda self: pending(1)):
            channel.check_online()
        delayed = channel._pending_stream_up
        self.twitch._watching_task = self.twitch.create_account_task(pending(2))
        self.twitch._mnt_task = self.twitch.create_account_task(pending(3))
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered)), 2)
        queue = self.hub.subscribe()
        response = await self.client.post('/api/auth/disconnect')
        self.assertEqual(response.status, 200)
        self.assertTrue(all(event.is_set() for event in finished))
        self.assertTrue(delayed.done())
        events = []
        while not queue.empty():
            events.append(queue.get_nowait())
        auth = [event['data'] for event in events if event['event'] == 'auth'][-1]
        self.assertFalse(auth['logged_in'])
        self.assertTrue(auth['auth_disconnected'])
        self.assertIsNone(auth['device_code'])
        self.assertIsNone(auth['username'])
        self.hub.unsubscribe(queue)

    async def test_invalid_token_and_unauthorized_disconnect_leave_saved_account_unchanged(self):
        for token in (None, 123, '', '  ', []):
            with self.subTest(token=token):
                response = await self.client.post('/api/auth/token', json={'token': token})
                self.assertEqual(response.status, 400)
                self.assertEqual(self.settings.auth_token, 'test-old')
        with patch.dict('os.environ', {'API_KEY': 'local-api-key'}):
            response = await self.client.post('/api/auth/disconnect')
            self.assertEqual(response.status, 401)
        self.assertFalse(self.settings.auth_disconnected)
        self.assertEqual(Settings(SimpleNamespace(dump=False)).auth_token, 'test-old')
