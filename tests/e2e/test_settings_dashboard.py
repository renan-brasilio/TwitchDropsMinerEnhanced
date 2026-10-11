"""Dashboard Settings E2E specs (risk rows 5 and 6) against a real in-process backend.

Run: ``python -m unittest discover -s tests -v`` (skips without Playwright unless REQUIRE_E2E=1).
"""
from __future__ import annotations

import json
import unittest

from .harness import (
    CAMPAIGN_NAME, DROP_NAME, GAME_NAME, PRIMARY_TOKEN, REJECTED_TOKEN, SECOND_TOKEN,
    DashboardTestCase, expect, native_bridge_script,
)

DISCONNECTED_STATUS = "Disconnected. Connect explicitly to resume mining."
MINING_CONTROLS = ("Pause", "Resume", "Reload", "Claim Drops", "Switch Streamer", "Refresh")


class SettingsLayoutTests(DashboardTestCase):
    def test_settings_tab_keeps_dashboard_overview_visible(self):
        self.open_dashboard()
        expect(self.page.locator("#dropTitle")).to_have_text(DROP_NAME)
        self.open_settings()
        expect(self.page.locator(".cockpit-card")).to_be_visible()
        expect(self.page.locator(".metrics-row")).to_be_visible()
        expect(self.page.locator("#dropTitle")).to_have_text(DROP_NAME)
        expect(self.page.locator("#campaignText")).to_have_text(CAMPAIGN_NAME)
        expect(self.page.locator("#tab-channels")).to_be_hidden()


class ConnectedAccountTests(DashboardTestCase):
    def account_status(self):
        return self.page.locator("#accountStatus")

    def test_connected_account_offers_only_disconnect(self):
        self.open_dashboard()
        self.open_settings()
        expect(self.account_status()).to_have_text("Connected as e2e_viewer")
        expect(self.page.locator("#btnDisconnect")).to_be_visible()
        expect(self.page.locator("#btnDisconnect")).to_be_enabled()
        expect(self.page.locator("#btnConnect")).to_be_hidden()
        expect(self.page.locator("#tokenAuthentication")).to_be_hidden()
        expect(self.page.locator("#authTokenInput")).to_be_hidden()

    def test_disconnect_asks_for_confirmation_and_cancel_keeps_account(self):
        self.open_dashboard()
        self.open_settings()
        expect(self.account_status()).to_have_text("Connected as e2e_viewer")
        confirmation = self.page.get_by_role("group", name="Confirm local account disconnect")
        expect(confirmation).to_be_hidden()

        self.page.locator("#btnDisconnect").click()
        expect(confirmation).to_be_visible()
        expect(confirmation.get_by_role("button", name="Disconnect account")).to_be_focused()
        confirmation.get_by_role("button", name="Cancel").click()

        expect(confirmation).to_be_hidden()
        expect(self.account_status()).to_have_text("Connected as e2e_viewer")
        self.page.reload()
        self.open_settings()
        expect(self.account_status()).to_have_text("Connected as e2e_viewer")
        persisted = json.loads(self.harness.settings_path.read_text())
        self.assertEqual(persisted["auth_token"], PRIMARY_TOKEN)
        self.assertFalse(persisted["auth_disconnected"])

    def assert_disconnected_controls(self):
        expect(self.account_status()).to_have_text(DISCONNECTED_STATUS)
        expect(self.page.locator("#btnConnect")).to_be_visible()
        expect(self.page.locator("#btnConnect")).to_be_enabled()
        expect(self.page.locator("#tokenAuthentication")).to_be_visible()
        expect(self.page.locator("#btnDisconnect")).to_be_hidden()
        for name in MINING_CONTROLS:  # Switch Streamer/Refresh live in inactive tabs
            with self.subTest(control=name):
                control = self.page.get_by_role("button", name=name, exact=True, include_hidden=True)
                expect(control).to_be_disabled()

    def test_confirmed_disconnect_shows_signed_out_state_that_survives_restart(self):
        self.open_dashboard()
        self.open_settings()
        expect(self.account_status()).to_have_text("Connected as e2e_viewer")
        self.page.locator("#btnDisconnect").click()
        self.page.get_by_role("button", name="Disconnect account").click()

        self.assert_disconnected_controls()
        expect(self.page.get_by_role("group", name="Confirm local account disconnect")).to_be_hidden()
        expect(self.page.locator("#dropTitle")).to_have_text("No Active Campaign")

        validations = self.harness.upstream.count("GET", "id.twitch.tv/oauth2/validate")
        self.harness.restart_backend()
        self.page.reload()
        self.open_settings()
        self.assert_disconnected_controls()
        expect(self.page.locator("#statAuth")).to_have_text("Not Connected")
        self.assertEqual(self.harness.upstream.count("GET", "id.twitch.tv/oauth2/validate"), validations,
                         "a disconnected miner must not log back in after a restart")


class TokenReconnectTests(DashboardTestCase):
    harness_options = {"disconnected": True}

    def submit_token(self, token):
        self.open_dashboard()
        self.open_settings()
        expect(self.page.locator("#accountStatus")).to_have_text(DISCONNECTED_STATUS)
        self.page.get_by_text("Advanced: authenticate with a browser token").click()
        self.page.get_by_label("Twitch auth token:").fill(token)
        self.page.get_by_role("button", name="Save token & connect").click()

    def assert_token_not_rendered(self, token):
        self.assertNotIn(token, self.page.content())
        self.assertNotIn(token, self.page.locator("body").inner_text())

    def test_token_connects_as_its_account_and_clears_the_field(self):
        self.submit_token(SECOND_TOKEN)
        expect(self.page.locator("#accountStatus")).to_have_text("Connected as second_viewer")
        expect(self.page.locator("#authTokenInput")).to_have_value("")
        expect(self.page.locator("#tokenAuthentication")).to_be_hidden()
        expect(self.page.locator("#btnDisconnect")).to_be_visible()
        self.assert_token_not_rendered(SECOND_TOKEN)

    def test_token_endpoint_error_is_reported_without_echoing_the_token(self):
        token = "e2e-token-echo-canary"
        self.page.route("**/api/auth/token", lambda route: route.fulfill(
            status=400, content_type="application/json",
            body=json.dumps({"error": f"Twitch rejected {token}"}),
        ))
        self.submit_token(token)
        expect(self.page.locator("#accountError")).to_have_text(
            "Unable to authenticate token. Check the token and try again.")
        expect(self.page.locator("#btnSaveToken")).to_be_enabled()
        self.assert_token_not_rendered(token)

    def test_rejected_token_shows_an_error_without_echoing_it(self):
        self.submit_token(REJECTED_TOKEN)
        expect(self.page.locator("#accountError")).not_to_be_empty(timeout=5_000)
        self.assert_token_not_rendered(REJECTED_TOKEN)

    def test_valid_token_still_connects_after_a_rejected_one(self):
        self.submit_token(REJECTED_TOKEN)
        expect(self.page.locator("#accountStatus")).to_have_text(
            "Not connected. Waiting for Twitch authentication.")
        self.page.get_by_label("Twitch auth token:").fill(SECOND_TOKEN)
        self.page.get_by_role("button", name="Save token & connect").click()
        expect(self.page.locator("#accountStatus")).to_have_text("Connected as second_viewer", timeout=5_000)


def auto_claim_toggles(page):
    return page.locator("#settingsAutoClaimToggle"), page.locator("#autoClaimToggle")


class AutoClaimFromSettingsTests(DashboardTestCase):
    harness_options = {"auto_claim": False}

    def test_settings_toggle_updates_campaigns_shortcut_and_persists(self):
        self.open_dashboard()
        self.open_settings()
        settings_toggle, shortcut = auto_claim_toggles(self.page)
        expect(settings_toggle).not_to_be_checked()
        expect(settings_toggle).to_be_enabled()
        settings_toggle.check()
        expect(settings_toggle).to_be_enabled()  # re-enabled once the backend saved it
        self.open_campaigns()
        expect(shortcut).to_be_checked()

        self.page.reload()
        expect(shortcut).to_be_checked()
        self.open_settings()
        expect(settings_toggle).to_be_checked()


class AutoClaimFromCampaignsTests(DashboardTestCase):
    harness_options = {"auto_claim": True}

    def test_campaigns_shortcut_updates_settings_toggle_and_persists(self):
        self.open_dashboard()
        self.open_campaigns()
        settings_toggle, shortcut = auto_claim_toggles(self.page)
        expect(shortcut).to_be_checked()
        expect(shortcut).to_be_enabled()
        shortcut.uncheck()
        expect(shortcut).to_be_enabled()
        self.open_settings()
        expect(settings_toggle).not_to_be_checked()

        self.page.reload()
        self.open_settings()
        expect(settings_toggle).to_be_enabled()
        expect(settings_toggle).not_to_be_checked()


class PriorityEditorTests(DashboardTestCase):
    harness_options = {"priority": (GAME_NAME,)}

    def ranked_games(self):
        return self.page.locator("#priorityRankedList strong")

    def reload_settings(self):
        self.page.reload()
        self.open_settings()

    def test_custom_game_order_and_mode_persist_after_reload(self):
        self.open_dashboard()
        self.open_settings()
        expect(self.ranked_games()).to_have_text([GAME_NAME])
        mode = self.page.get_by_label("Priority Mode:")
        expect(mode).to_have_value("0")

        self.page.get_by_label("Custom game title").fill("Custom Frontier")
        self.page.get_by_role("button", name="Add Custom").click()
        expect(self.ranked_games()).to_have_text([GAME_NAME, "Custom Frontier"])
        expect(self.page.get_by_label("Custom game title")).to_have_value("")
        row = self.page.locator("#priorityRankedList > div").filter(has_text="Custom Frontier")
        row.get_by_role("button", name="▲").click()
        expect(self.ranked_games()).to_have_text(["Custom Frontier", GAME_NAME])
        expect(mode).to_be_enabled()  # editor controls unlock after the save round-trip

        self.reload_settings()
        expect(self.ranked_games()).to_have_text(["Custom Frontier", GAME_NAME])
        expect(self.page.locator("#priorityRankedList .pill")).to_have_text(["#1", "#2"])
        expect(mode).to_have_value("0")
        mode.select_option(label="Ending Soonest")
        expect(mode).to_be_enabled()

        self.reload_settings()
        expect(mode).to_have_value("1")
        expect(self.ranked_games()).to_have_text(["Custom Frontier", GAME_NAME])


class NativeBridgeTests(DashboardTestCase):
    """Web side of the mac/main.swift settings bridge, with a simulated WKScriptMessageHandler."""

    harness_options = {"disconnected": True}
    SERVER = "https://miner.example.test:8443"

    def open_with_bridge(self, **bridge):
        page = self.context.new_page()
        page.add_init_script(native_bridge_script(**bridge))
        page.goto(self.harness.url)
        self.page = page
        self.open_settings()
        expect(page.get_by_role("heading", name="Application")).to_be_visible()
        return page

    def posted(self, count=None):
        if count is not None:
            self.page.wait_for_function("n => window.__nativeBridge.messages.length >= n", arg=count)
        return self.page.evaluate("window.__nativeBridge.messages")

    def choose_remote(self, server_url, api_key=""):
        self.page.get_by_label("Remote server (Docker)").check()
        self.page.get_by_label("Server URL:").fill(server_url)
        self.page.get_by_label("API key (optional):").fill(api_key)

    def save(self):
        self.page.get_by_role("button", name="Save & reconnect").click()

    def test_application_section_is_hidden_without_native_bridge(self):
        self.open_dashboard()
        self.open_settings()
        expect(self.page.locator("#nativeApplicationSettings")).to_be_hidden()
        expect(self.page.get_by_role("heading", name="Miner connection")).to_be_hidden()

    def test_application_section_reflects_native_preferences(self):
        page = self.open_with_bridge(keep_running=False, menu_bar_style="percentage",
                                     mode="remote", server_url=self.SERVER)
        expect(page.get_by_label("Keep running in the menu bar when closing the window")).not_to_be_checked()
        expect(page.get_by_label("Menu bar display:")).to_have_value("percentage")
        expect(page.get_by_label("Remote server (Docker)")).to_be_checked()
        expect(page.get_by_label("Server URL:")).to_have_value(self.SERVER)
        expect(page.get_by_label("Remove saved API key")).to_be_hidden()  # no saved key

    def test_keep_running_toggle_posts_change_and_shows_it_only_after_ack(self):
        page = self.open_with_bridge(keep_running=True, auto_reply=False)
        toggle = page.get_by_label("Keep running in the menu bar when closing the window")
        expect(toggle).to_be_checked()
        toggle.click()

        self.assertEqual(self.posted(1), [{"keepRunningOnClose": False}])
        expect(toggle).to_be_checked()  # unconfirmed changes are not shown as applied
        expect(toggle).to_be_disabled()
        expect(page.locator("#nativeSettingsStatus")).to_have_text("Saving application preference...")

        page.evaluate("window.__nativeBridge.replyToPending()")
        expect(toggle).not_to_be_checked()
        expect(toggle).to_be_enabled()
        expect(page.locator("#nativeSettingsStatus")).to_be_empty()

    def test_remote_mode_reveals_server_url_and_api_key_fields(self):
        page = self.open_with_bridge(mode="local")
        server, key = page.get_by_label("Server URL:"), page.get_by_label("API key (optional):")
        expect(server).to_be_hidden()
        expect(key).to_be_hidden()
        page.get_by_label("Remote server (Docker)").check()
        expect(server).to_be_visible()
        expect(key).to_be_visible()
        page.get_by_label("Local (this Mac)").check()
        expect(server).to_be_hidden()

    def test_invalid_server_url_shows_error_and_posts_nothing(self):
        page = self.open_with_bridge(mode="local")
        for url in ("ftp://miner.example.test", "miner.example.test:23432", ""):
            with self.subTest(url=url):
                self.choose_remote(url)
                self.save()
                expect(page.locator("#connectionStatus")).to_have_text(
                    "Enter a server URL starting with http:// or https://.")
                expect(page.get_by_role("button", name="Save & reconnect")).to_be_enabled()
                self.assertEqual(self.posted(), [])

    def test_remote_save_posts_url_and_omits_blank_api_key(self):
        page = self.open_with_bridge(mode="local")
        self.choose_remote(f"  {self.SERVER}  ")
        self.save()
        self.assertEqual(self.posted(1), [{"connection": {"mode": "remote", "serverURL": self.SERVER}}])
        expect(page.get_by_role("button", name="Save & reconnect")).to_be_disabled()
        expect(page.locator("#connectionStatus")).to_have_text("Reconnecting...")

    def test_remote_save_posts_typed_api_key_trimmed(self):
        self.open_with_bridge(mode="local")
        self.choose_remote(self.SERVER, api_key="  e2e-typed-key  ")
        self.save()
        self.assertEqual(self.posted(1), [{"connection": {
            "mode": "remote", "serverURL": self.SERVER, "apiKey": "e2e-typed-key"}}])

    def test_remove_saved_api_key_posts_empty_key_and_saved_key_is_never_rendered(self):
        secret = "e2e-native-stored-secret"
        page = self.open_with_bridge(mode="remote", server_url=self.SERVER, api_key=secret)
        key = page.get_by_label("API key (optional):")
        expect(key).to_have_value("")
        expect(key).to_have_attribute("placeholder", "Saved — leave blank to keep it")
        self.assertNotIn(secret, page.content())
        page.get_by_label("Remove saved API key").check()
        self.save()
        self.assertEqual(self.posted(1), [{"connection": {
            "mode": "remote", "serverURL": self.SERVER, "apiKey": ""}}])

    def test_native_error_reply_reenables_save_shows_message_and_clears_key(self):
        error = "Could not reach the miner at that address."
        secret = "e2e-typed-secret-key"
        page = self.open_with_bridge(mode="local", connection_error=error)
        self.choose_remote(self.SERVER, api_key=secret)
        self.save()
        self.assertEqual(len(self.posted(1)), 1)

        expect(page.locator("#connectionStatus")).to_have_text(error)
        save = page.get_by_role("button", name="Save & reconnect")
        expect(save).to_be_enabled()
        expect(page.get_by_label("API key (optional):")).to_have_value("")
        self.assertNotIn(secret, page.content())
        values = page.eval_on_selector_all("input", "inputs => inputs.map(input => input.value)")
        self.assertNotIn(secret, values)
        save.click()
        self.assertEqual(len(self.posted(2)), 2)  # a failed switch can be retried


if __name__ == "__main__":
    unittest.main()
