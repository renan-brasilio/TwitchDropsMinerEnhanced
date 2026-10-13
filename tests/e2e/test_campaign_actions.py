"""Campaigns & Rewards actions, mining options and the drops refresh (risk rows 1, 2 and 5)."""
from __future__ import annotations

import json

from .harness import CAMPAIGN_NAME, GAME_NAME, DashboardTestCase, expect


class CampaignsTestCase(DashboardTestCase):
    def open_campaigns(self):
        self.open_dashboard()
        self.page.get_by_role("button", name="Campaigns & Rewards").click()
        card = self.page.locator(".campaign-item", has_text=CAMPAIGN_NAME)
        expect(card).to_be_visible()
        return card

    def saved(self):
        return json.loads(self.harness.settings_path.read_text())

    def saved_mode(self):
        return self.saved()["priority_mode"]["data"]


class CampaignActionTests(CampaignsTestCase):
    def test_list_keeps_its_scroll_position_while_the_dashboard_updates(self):
        self.page.set_viewport_size({"width": 1280, "height": 420})
        card = self.open_campaigns()
        bottom = self.page.evaluate("scrollTo(0, document.scrollingElement.scrollHeight), scrollY")
        self.assertGreater(bottom, 0)
        for _ in range(2):  # the dashboard polls every 2.5 s
            with self.page.expect_response("**/api/status"):
                pass
        self.assertEqual(self.page.evaluate("scrollY"), bottom)
        expect(card).to_be_visible()

    def test_mine_only_selected_games_is_the_priority_mode_of_the_games_page(self):
        self.open_campaigns()
        only = self.page.get_by_label("Mine only selected games")
        mode = self.page.locator("#priorityMode")
        expect(only).to_be_checked()

        only.uncheck()
        expect(only).to_be_enabled()  # save round-trip finished
        self.assertEqual(self.saved_mode(), 1)  # selected games first, then ending soonest

        self.page.get_by_role("button", name="Games", exact=True).click()
        expect(mode).to_have_value("1")
        mode.select_option(label="Lowest Availability First")
        expect(mode).to_be_enabled()
        self.page.get_by_role("button", name="Campaigns & Rewards").click()
        expect(only).not_to_be_checked()

        only.check()
        expect(only).to_be_enabled()
        self.assertEqual(self.saved_mode(), 0)
        only.uncheck()  # brings back the order chosen on the Games page
        expect(only).to_be_enabled()
        self.assertEqual(self.saved_mode(), 2)

    def test_ignore_moves_the_campaign_out_of_mining_and_restore_brings_it_back(self):
        card = self.open_campaigns()
        card.get_by_role("button", name="Ignore").click()

        expect(card).to_have_count(0)
        ignored = self.page.locator("#ignoredCampaigns")
        expect(ignored).to_be_visible()
        expect(ignored.locator("summary")).to_have_text("Ignored campaigns (1)")
        expect(self.page.locator("#statCampaigns")).to_have_text("0")
        self.assertEqual(len(self.saved()["ignored_campaigns"]["data"]), 1)

        ignored.locator("summary").click()
        ignored.get_by_role("button", name="Restore").click()
        expect(self.page.locator(".campaign-item", has_text=CAMPAIGN_NAME)).to_be_visible()
        expect(ignored).to_be_hidden()
        self.assertEqual(self.saved()["ignored_campaigns"]["data"], [])

    def test_mine_now_marks_the_campaign_first_and_stop_returns_to_normal(self):
        card = self.open_campaigns()
        card.get_by_role("button", name="Mine now").click()
        expect(card.locator(".pill", has_text="Mining first")).to_be_visible()
        expect(card).to_have_class("campaign-item campaign-focused")
        self.assertNotEqual(self.saved()["focused_campaign"], "")

        card.get_by_role("button", name="Stop").click()
        expect(card.get_by_role("button", name="Mine now")).to_be_visible()
        self.assertEqual(self.saved()["focused_campaign"], "")


class AutoSelectLinkedTests(CampaignsTestCase):
    harness_options = {"priority": ()}

    def test_turning_it_on_adds_the_linked_game_and_survives_a_reload(self):
        self.open_campaigns()
        auto = self.page.get_by_label("Automatically select linked games")
        expect(auto).not_to_be_checked()

        auto.check()
        expect(auto).to_be_enabled()
        self.page.get_by_role("button", name="Games", exact=True).click()
        expect(self.page.locator("#priorityRankedList .priority-name")).to_have_text([GAME_NAME])
        self.assertEqual(self.saved()["priority"], [GAME_NAME])
        self.assertTrue(self.saved()["auto_select_linked"])

        self.page.reload()
        self.page.get_by_role("button", name="Campaigns & Rewards").click()
        expect(auto).to_be_checked()


class DropsRefreshTests(DashboardTestCase):
    def test_drops_view_has_a_refresh_that_rechecks_twitch(self):
        self.open_dashboard()
        self.page.get_by_role("button", name="Games", exact=True).click()
        refresh = self.page.locator("#btnGamesRefresh")
        expect(refresh).to_be_visible()
        expect(self.page.locator("#gamesGrid .game-tile", has_text=GAME_NAME)).to_have_count(1)

        with self.page.expect_response("**/api/inventory/refresh") as refreshed:
            refresh.click()
        self.assertEqual(refreshed.value.status, 200)
        expect(refresh).to_be_enabled()
        expect(self.page.locator("#gamesGrid .game-tile", has_text=GAME_NAME)).to_have_count(1)

        self.page.locator("#gamesViewAll").click()
        expect(refresh).to_be_hidden()


class CockpitCardEmptyStateTests(DashboardTestCase):
    harness_options = {"disconnected": True}

    def test_empty_target_shows_clean_prompt_and_hides_idle_stats(self):
        self.open_dashboard()
        expect(self.page.locator("#dropTitle")).to_have_text("No Active Campaign")
        expect(self.page.locator("#dropEmptyHint")).to_be_visible()
        expect(self.page.locator("#dropEmptyHint")).to_contain_text("To start, select one from Campaigns & Rewards")
        expect(self.page.locator("#dropThumbnail")).to_be_hidden()
        expect(self.page.locator("#dropMeta")).to_be_hidden()
        expect(self.page.locator("#dropStats")).to_be_hidden()
        expect(self.page.locator("#dropProgressTrack")).to_be_hidden()
        expect(self.page.locator("#channelIndicator")).to_be_hidden()


class HeaderActionButtonsTests(DashboardTestCase):
    def test_active_mining_shows_pause_and_auto_claim_shows_automatically_claiming(self):
        self.open_dashboard()
        # With active campaign mining:
        expect(self.page.locator("#btnPause")).to_be_visible()
        expect(self.page.locator("#btnResume")).to_be_hidden()

        # Auto-claim button reflects option:
        claim_btn = self.page.locator("#btnClaim")
        claim_text = self.page.locator("#btnClaimText")
        # Default harness has auto_claim: False with no ready drops -> claim button is hidden
        expect(claim_btn).to_be_hidden()

        # Enable auto-claim in Settings tab:
        self.open_settings()
        self.page.locator("#settingsAutoClaimToggle").check()
        expect(claim_btn).to_be_visible()
        expect(claim_btn).to_be_disabled()
        expect(claim_text).to_have_text("Automatically claiming")
