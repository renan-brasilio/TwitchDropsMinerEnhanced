"""Campaigns & Rewards actions and the drops refresh (risk rows 1, 2 and 5)."""
from __future__ import annotations

import json

from .harness import CAMPAIGN_NAME, GAME_NAME, DashboardTestCase, expect


class CampaignActionTests(DashboardTestCase):
    def open_campaigns(self):
        self.open_dashboard()
        self.page.get_by_role("button", name="Campaigns & Rewards").click()
        card = self.page.locator(".campaign-item", has_text=CAMPAIGN_NAME)
        expect(card).to_be_visible()
        return card

    def saved(self):
        return json.loads(self.harness.settings_path.read_text())

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
