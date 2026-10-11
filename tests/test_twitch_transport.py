"""Risk rows 2-3: twitch.py's HTTP/GQL transport and the device-code login, with Twitch
virtualized at the aiohttp.ClientSession boundary."""
import asyncio
import json
import logging
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import aiohttp
from yarl import URL

from constants import ClientType, PriorityMode
from exceptions import ExitRequest, GQLException, LoginException, RequestInvalid
from twitch import Twitch
from utils import ExponentialBackoff


NOW = datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
TOKEN = "synthetic-token"


class Clock(datetime):
    current = NOW

    @classmethod
    def now(cls, tz=None):
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


@contextmanager
def recorded_sleeps():
    """Make asyncio.sleep instant for the calling task only, recording the requested delays."""
    real_sleep, owner, delays = asyncio.sleep, asyncio.current_task(), []

    async def sleep(delay, *args, **kwargs):
        if asyncio.current_task() is not owner:
            return await real_sleep(delay, *args, **kwargs)
        delays.append(delay)
        return await real_sleep(0)

    with patch("asyncio.sleep", sleep):
        yield delays


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status = status
        self.body = body if body is not None else {}
        self.released = False

    async def read(self):
        return json.dumps(self.body).encode()

    async def json(self, **kwargs):
        return json.loads(json.dumps(self.body))

    async def text(self, *args, **kwargs):
        return ""

    def release(self):
        self.released = True


class FakeTwitchSession:
    """aiohttp.ClientSession stand-in that hands every request to the test's `route`."""

    def __init__(self, route, *, cookie_jar, timeout, **kwargs):
        self.route = route
        self.cookie_jar = cookie_jar
        self.timeout = timeout
        self.closed = False

    async def request(self, method, url, **kwargs):
        result = await self.route(self, method, URL(str(url)), kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    async def close(self):
        self.closed = True


class TransportTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        Clock.current = NOW
        self.temp = tempfile.TemporaryDirectory()
        self.cookies_path = Path(self.temp.name, "cookies.jar")
        self.patchers = [
            patch("twitch.datetime", Clock),
            patch("twitch.COOKIES_PATH", self.cookies_path),
            patch("twitch.aiohttp.ClientSession", side_effect=self.new_session),
            patch("twitch.aiohttp.TCPConnector", return_value=None),
            # Retries back off for 0 s so no test waits on real time.
            patch("twitch.ExponentialBackoff", partial(ExponentialBackoff, variance=(0, 0))),
            # The device-code prompt opens the browser on macOS.
            patch("state_hub.subprocess"),
            patch.dict("os.environ"),
        ]
        for patcher in self.patchers:
            patcher.start()
        os.environ.pop("TWITCH_AUTH_TOKEN", None)
        self.requests: list[tuple[str, URL, dict]] = []
        self.responses: list[FakeResponse] = []
        self.handlers = {}
        self.instances: list[Twitch] = []
        self.twitch = self.new_twitch()

    def new_twitch(self, **settings):
        values = dict(
            priority=[], exclude=set(), priority_mode=PriorityMode.PRIORITY_ONLY, auth_token="",
            auto_claim=False, enable_badges_emotes=False, available_drops_check=False, dump=False,
            proxy="", connection_quality=1, save=lambda force=False: None,
        )
        values.update(settings)
        twitch = Twitch(SimpleNamespace(**values))
        self.instances.append(twitch)
        return twitch

    async def asyncTearDown(self):
        for twitch in self.instances:
            twitch.gui.progress.stop_timer()
            if (task := twitch._qgl_limiter._reset_task) is not None:
                task.cancel()
            logging.getLogger("TwitchDrops").removeHandler(twitch.gui._log_handler)
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def new_session(self, **kwargs):
        return FakeTwitchSession(self.route, **kwargs)

    async def route(self, session, method, url, kwargs):
        self.requests.append((method, url, kwargs))
        handler = self.handlers[(method, f"{url.host}{url.path}")]
        result = handler(session, kwargs) if callable(handler) else next(handler)
        if isinstance(result, FakeResponse):
            self.responses.append(result)
        return result

    def sent(self, method, host_path):
        return [kwargs for m, url, kwargs in self.requests if (m, f"{url.host}{url.path}") == (method, host_path)]

    def logged(self, twitch, text):
        return [entry["message"] for entry in twitch.gui.logs if text in entry["message"]]

    def logged_in(self, twitch=None):
        auth = (twitch or self.twitch)._auth_state
        auth.session_id, auth.device_id, auth.access_token, auth.user_id = "session", "device", TOKEN, 42


class RequestRetryTests(TransportTestCase):
    URL = "https://www.twitch.tv/some/page"

    async def fetch(self, twitch=None, **kwargs):
        async with (twitch or self.twitch).request("GET", self.URL, **kwargs) as response:
            return response.status, await response.json()

    async def test_twitch_server_errors_are_retried_until_it_answers(self):
        self.handlers[("GET", "www.twitch.tv/some/page")] = iter([
            FakeResponse(503), FakeResponse(502), FakeResponse(200, {"ok": True})])

        self.assertEqual(await self.fetch(), (200, {"ok": True}))
        self.assertEqual(len(self.requests), 3)
        self.assertTrue(all(r.released for r in self.responses[:2]))
        self.assertEqual(len(self.logged(self.twitch, "Twitch is down")), 2)

    async def test_client_errors_are_returned_without_retrying(self):
        self.handlers[("GET", "www.twitch.tv/some/page")] = iter([FakeResponse(404, {"error": "gone"})])
        self.assertEqual(await self.fetch(), (404, {"error": "gone"}))
        self.assertEqual(len(self.requests), 1)

    async def test_connection_failures_are_retried_and_only_repeats_are_reported(self):
        self.handlers[("GET", "www.twitch.tv/some/page")] = iter([
            aiohttp.ClientConnectionError(), asyncio.TimeoutError(),
            aiohttp.ClientPayloadError(), FakeResponse(200, {"ok": True})])

        self.assertEqual(await self.fetch(), (200, {"ok": True}))
        # The first quick retry stays silent; later ones tell the user what is happening.
        self.assertEqual(len(self.logged(self.twitch, "Cannot connect to Twitch")), 2)

    async def test_certificate_failures_are_not_retried(self):
        error = aiohttp.ClientConnectorCertificateError(None, Exception("untrusted certificate"))
        self.handlers[("GET", "www.twitch.tv/some/page")] = iter([error, FakeResponse(200)])
        with self.assertRaises(aiohttp.ClientConnectorCertificateError):
            await self.fetch()
        self.assertEqual(len(self.requests), 1)

    async def test_request_close_to_its_expiry_is_invalid_without_contacting_twitch(self):
        # The session timeout is 10 s: a request must be able to finish before `invalidate_after`.
        self.handlers[("GET", "www.twitch.tv/some/page")] = lambda s, k: FakeResponse(200)
        with self.assertRaises(RequestInvalid):
            await self.fetch(invalidate_after=NOW + timedelta(seconds=10))
        self.assertEqual(self.requests, [])
        self.assertEqual((await self.fetch(invalidate_after=NOW + timedelta(seconds=11)))[0], 200)

    async def test_closing_the_app_stops_retrying_an_unavailable_twitch(self):
        def outage(session, kwargs):
            self.twitch.gui.close()
            return FakeResponse(503)

        self.handlers[("GET", "www.twitch.tv/some/page")] = outage
        with self.assertRaises(ExitRequest):
            await self.fetch()
        self.assertEqual(len(self.requests), 1)

    async def test_configured_proxy_is_used_unless_the_request_names_one(self):
        twitch = self.new_twitch(proxy="http://proxy.invalid:3128")
        self.handlers[("GET", "www.twitch.tv/some/page")] = lambda s, k: FakeResponse(200)
        await self.fetch(twitch)
        await self.fetch(twitch, proxy="http://explicit.invalid:8080")
        self.assertEqual([kwargs["proxy"] for _, _, kwargs in self.requests],
                         ["http://proxy.invalid:3128", "http://explicit.invalid:8080"])

    async def test_unreadable_cookie_file_does_not_block_requests(self):
        self.cookies_path.write_bytes(b"not a pickled cookie jar")
        self.handlers[("GET", "www.twitch.tv/some/page")] = lambda s, k: FakeResponse(200)
        self.assertEqual((await self.fetch())[0], 200)
        self.assertEqual(len(self.twitch._session.cookie_jar), 0)


class GQLRequestTests(TransportTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.logged_in()
        self.replies: list = []
        self.handlers[("POST", "gql.twitch.tv/gql")] = lambda s, k: FakeResponse(200, self.replies.pop(0))
        self.op = {"operationName": "Inventory", "variables": {}}

    def error(self, message, **extra):
        return {"errors": [dict(message=message, **extra)], "data": None,
                "extensions": {"operationName": "Inventory"}}

    async def test_successful_reply_is_returned_and_sent_with_the_account_token(self):
        self.replies = [{"data": {"currentUser": {"id": "42"}}}]
        self.assertEqual(await self.twitch.gql_request(self.op), {"data": {"currentUser": {"id": "42"}}})
        (kwargs,) = self.sent("POST", "gql.twitch.tv/gql")
        self.assertEqual(kwargs["json"], self.op)
        self.assertEqual(kwargs["headers"]["Authorization"], f"OAuth {TOKEN}")
        self.assertEqual(kwargs["headers"]["Client-Id"], ClientType.WEB.CLIENT_ID)

    async def test_unrecognised_errors_raise_without_retrying(self):
        self.replies = [self.error("forbidden")]
        with self.assertRaises(GQLException):
            await self.twitch.gql_request(self.op)
        self.assertEqual(len(self.requests), 1)

    async def test_error_replies_raise_with_twitchs_message(self):
        self.replies = [{"error": "Unauthorized", "message": "The OAuth token is invalid", "status": 401}]
        with self.assertRaisesRegex(GQLException, "Unauthorized: The OAuth token is invalid"):
            await self.twitch.gql_request(self.op)

    async def test_service_errors_are_retried_once_after_at_least_five_seconds(self):
        for message in ("service error", "PersistedQueryNotFound"):
            with self.subTest(message), recorded_sleeps() as delays:
                self.requests.clear()
                self.replies = [self.error(message), {"data": {"ok": True}}]
                self.assertEqual(await self.twitch.gql_request(self.op), {"data": {"ok": True}})
                self.assertEqual(delays, [5])

                self.replies = [self.error(message), self.error(message)]
                with self.assertRaises(GQLException):
                    await self.twitch.gql_request(self.op)
                self.assertEqual(len(self.requests), 4)

    async def test_transient_timeouts_are_retried_until_data_arrives(self):
        self.replies = [self.error("service timeout"), self.error("context deadline exceeded"),
                        self.error("service unavailable"), {"data": {"ok": True}}]
        with recorded_sleeps() as delays:
            self.assertEqual(await self.twitch.gql_request(self.op), {"data": {"ok": True}})
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(len(delays), 3)

    async def test_server_error_nulls_only_the_failed_field(self):
        self.replies = [{
            "data": {"user": {"stream": {"id": "1"}, "displayName": "Streamer"}},
            "errors": [{"message": "server error", "path": ["user", "stream"]}],
        }]
        response = await self.twitch.gql_request(self.op)
        self.assertEqual(response["data"], {"user": {"stream": None, "displayName": "Streamer"}})
        self.assertEqual(len(self.requests), 1)

    async def test_batched_operations_fail_when_any_reply_fails(self):
        self.replies = [[{"data": {"a": 1}}, {"data": {"b": 2}}]]
        self.assertEqual(await self.twitch.gql_request([self.op, self.op]),
                         [{"data": {"a": 1}}, {"data": {"b": 2}}])
        self.replies = [[{"data": {"a": 1}}, self.error("forbidden")]]
        with self.assertRaises(GQLException):
            await self.twitch.gql_request([self.op, self.op])


class DeviceCodeLoginTests(TransportTestCase):
    """Risk row 3: connecting through Twitch's OAuth device-code flow."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.device_replies = iter([
            {"device_code": "device-1", "user_code": "CODE-ONE", "interval": 0, "expires_in": 60,
             "verification_uri": "https://www.twitch.tv/activate"},
            {"device_code": "device-2", "user_code": "CODE-TWO", "interval": 0, "expires_in": 60,
             "verification_uri": "https://www.twitch.tv/activate"},
        ])
        self.grants: dict[str, list] = {"device-1": [], "device-2": []}
        self.accounts = {"device-token": ("303", "oauth_account", ClientType.SMARTBOX.CLIENT_ID),
                         "settings-token": ("404", "settings_account", ClientType.WEB.CLIENT_ID),
                         "env-token": ("505", "env_account", ClientType.WEB.CLIENT_ID)}
        self.handlers.update({
            ("GET", "www.twitch.tv/"): self.twitch_page,
            ("POST", "id.twitch.tv/oauth2/device"): self.device,
            ("POST", "id.twitch.tv/oauth2/token"): self.token,
            ("GET", "id.twitch.tv/oauth2/validate"): self.validate,
        })
        self.on_poll = None

    def twitch_page(self, session, kwargs):
        session.cookie_jar.update_cookies({"unique_id": "device-id"}, ClientType.WEB.CLIENT_URL)
        return FakeResponse(200)

    def device(self, session, kwargs):
        form = kwargs["data"]
        # Twitch requires exactly the client ID and scopes to issue a device code.
        if set(form) != {"client_id", "scopes"}:
            return FakeResponse(400, {"status": 400, "message": "missing scopes"})
        return FakeResponse(200, next(self.device_replies))

    def token(self, session, kwargs):
        if len(self.polls()) > 10:
            self.fail("device-code login kept polling without being authorized")
        code = kwargs["data"]["device_code"]
        replies = self.grants[code]
        if self.on_poll is not None:
            self.on_poll(code)
        if replies:
            return FakeResponse(200, {"access_token": replies.pop(0)})
        return FakeResponse(400, {"status": 400, "message": "authorization_pending"})

    def validate(self, session, kwargs):
        token = kwargs["headers"]["Authorization"].removeprefix("OAuth ")
        if token not in self.accounts:
            return FakeResponse(401, {"status": 401, "message": "invalid access token"})
        user_id, login, client_id = self.accounts[token]
        return FakeResponse(200, {"user_id": user_id, "login": login, "client_id": client_id})

    def published_codes(self, twitch):
        return [m.split("required: ")[1].split(" at ")[0]
                for m in self.logged(twitch, "Twitch activation code required")]

    def polls(self):
        return [kwargs["data"]["device_code"] for kwargs in self.sent("POST", "id.twitch.tv/oauth2/token")]

    async def test_device_code_is_shown_then_polled_until_the_user_authorizes(self):
        polls = 0

        def authorize_on_third_poll(code):
            nonlocal polls
            polls += 1
            if polls == 3:
                self.grants[code].append("device-token")

        self.on_poll = authorize_on_third_poll
        auth = await self.twitch.get_auth()

        self.assertEqual(self.published_codes(self.twitch), ["CODE-ONE"])
        self.assertEqual(self.polls(), ["device-1"] * 3)
        self.assertEqual(auth.user_id, 303)
        self.assertTrue(self.twitch.gui.is_logged_in)
        self.assertIsNone(self.twitch.gui.device_code_data)  # the prompt is dismissed
        self.assertIs(self.twitch._client_type, ClientType.SMARTBOX)

    async def test_device_login_survives_a_restart_without_a_new_code(self):
        self.grants["device-1"].append("device-token")
        await self.twitch.get_auth()
        self.handlers[("POST", "id.twitch.tv/oauth2/device")] = lambda s, k: self.fail("asked for a code")

        restarted = self.new_twitch()
        auth = await restarted.get_auth()

        self.assertEqual(auth.user_id, 303)
        self.assertIs(restarted._client_type, ClientType.SMARTBOX)
        self.assertEqual(self.published_codes(restarted), [])

    async def test_expired_device_code_is_replaced_with_a_new_one(self):
        def expire_first_code(code):
            if code == "device-1":
                Clock.current += timedelta(seconds=55)  # within the 10 s request timeout of expiry
            else:
                self.grants[code].append("device-token")

        self.on_poll = expire_first_code
        auth = await self.twitch.get_auth()

        self.assertEqual(self.published_codes(self.twitch), ["CODE-ONE", "CODE-TWO"])
        self.assertEqual(self.polls(), ["device-1", "device-2"])
        self.assertEqual(auth.user_id, 303)

    async def test_unavailable_device_flow_fails_the_login(self):
        self.handlers[("POST", "id.twitch.tv/oauth2/device")] = (
            lambda s, k: FakeResponse(400, {"status": 400, "message": "invalid client"}))
        with self.assertRaisesRegex(LoginException, "invalid client"):
            await self.twitch.get_auth()
        self.assertEqual(self.polls(), [])
        self.assertFalse(self.twitch.gui.is_logged_in)

    async def test_saved_settings_token_wins_over_the_environment_token(self):
        os.environ["TWITCH_AUTH_TOKEN"] = "env-token"
        cases = {"settings": ("settings-token", 404), "environment": ("", 505)}
        for label, (settings_token, user_id) in cases.items():
            with self.subTest(label):
                twitch = self.new_twitch(auth_token=settings_token)
                self.cookies_path.unlink(missing_ok=True)
                auth = await twitch.get_auth()
                self.assertEqual(auth.user_id, user_id)
                self.assertIs(twitch._client_type, ClientType.WEB)
        self.assertEqual(self.sent("POST", "id.twitch.tv/oauth2/device"), [])


if __name__ == "__main__":
    unittest.main()
