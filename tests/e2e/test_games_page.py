"""Games page E2E specs (risk rows 2 and 5): choosing mined games from the Twitch catalog.

The backend is real; Twitch's catalog comes from the harness's virtual Twitch (tests/e2e/harness.py).
"""
from __future__ import annotations

import json

from .harness import APOSTROPHE_GAME, CATALOG, GAME_NAME, DashboardTestCase, expect

FIRST_PAGE = 48  # the dashboard requests the catalog 48 games at a time


class GamesPageTestCase(DashboardTestCase):
    harness_options = {"priority": ()}

    def open_games(self) -> None:
        self.open_dashboard()
        self.page.get_by_role("button", name="Games", exact=True).click()
        expect(self.page.get_by_role("heading", name="Choose games to mine")).to_be_visible()

    def tile(self, name: str):
        return self.page.locator("#gamesGrid .game-tile").filter(
            has=self.page.locator(".game-name", has_text=name))

    def order(self):
        return self.page.locator("#priorityRankedList .priority-name")

    def saved_priority(self) -> list[str]:
        return json.loads(self.harness.settings_path.read_text())["priority"]

    def wait_saved(self, expected: list[str]) -> None:
        # Saves round-trip through the API; the mode select unlocks when the save finishes.
        expect(self.page.locator("#priorityMode")).to_be_enabled()
        self.assertEqual(self.saved_priority(), expected)


class DropGamesViewTests(GamesPageTestCase):
    def test_drop_games_open_first_and_selecting_a_cover_adds_then_removes_it(self):
        self.open_games()
        expect(self.page.locator("#gamesViewDrops")).to_have_attribute("aria-selected", "true")
        tile = self.tile(GAME_NAME)
        expect(tile).to_have_count(1)
        expect(tile.locator(".drops-tag")).to_have_text("Drops · 1 campaign")
        # Partial watch time must not be presented as a mined or claimed reward.
        expect(tile.locator(".drop-progress")).to_have_text("0/1 mined")
        expect(tile).to_have_attribute("aria-pressed", "false")

        tile.click()
        expect(tile).to_have_attribute("aria-pressed", "true")
        expect(tile.locator(".game-rank")).to_have_text("#1")
        expect(self.order()).to_have_text([GAME_NAME])
        self.wait_saved([GAME_NAME])

        self.page.reload()
        self.page.get_by_role("button", name="Games", exact=True).click()
        expect(self.order()).to_have_text([GAME_NAME])
        expect(self.tile(GAME_NAME)).to_have_attribute("aria-pressed", "true")

        self.tile(GAME_NAME).click()
        expect(self.tile(GAME_NAME)).to_have_attribute("aria-pressed", "false")
        expect(self.order()).to_have_count(0)
        expect(self.page.locator("#priorityEmptyNotice")).to_be_visible()
        self.wait_saved([])

    def test_status_polling_preserves_the_focused_game_cover_and_scroll(self):
        # Live account progress must update metadata without remounting the grid.
        self.page.set_viewport_size({"width": 1280, "height": 420})
        self.open_games()
        tile = self.tile(GAME_NAME)
        expect(tile.locator(".drop-progress")).to_have_text("0/1 mined")
        tile.focus()
        bottom = self.page.evaluate("scrollTo(0, document.scrollingElement.scrollHeight), scrollY")
        self.assertGreater(bottom, 0)
        for _ in range(2):
            with self.page.expect_response("**/api/status"):
                pass
        expect(tile).to_be_focused()
        self.assertEqual(self.page.evaluate("scrollY"), bottom)
        expect(tile.locator(".drop-progress")).to_have_text("0/1 mined")

    def test_disconnect_clears_account_progress_from_mounted_catalog_results(self):
        self.open_games()
        self.page.get_by_label("Search Twitch games").fill(GAME_NAME.lower())
        expect(self.tile(GAME_NAME).locator(".drop-progress")).to_have_text("0/1 mined")
        self.open_settings()
        self.page.locator("#btnDisconnect").click()
        self.page.get_by_role("button", name="Disconnect account").click()
        expect(self.page.locator("#btnConnect")).to_be_visible()
        # The search results remain mounted while Settings is open, but account data cannot.
        expect(self.page.locator("#gamesGrid .drop-progress")).to_have_count(0)
        expect(self.page.locator("#gamesGrid .drops-tag")).to_have_count(0)


class SearchTests(GamesPageTestCase):
    def test_search_finds_any_twitch_game_including_names_with_quotes(self):
        self.open_games()
        self.page.get_by_label("Search Twitch games").fill("assassin")
        expect(self.page.locator("#gamesStatus")).to_have_text("1 games match “assassin”.")
        expect(self.page.locator("#gamesViewAll")).to_be_disabled()  # views pause while searching

        self.tile(APOSTROPHE_GAME).click()
        expect(self.order()).to_have_text([APOSTROPHE_GAME])
        self.wait_saved([APOSTROPHE_GAME])

        self.page.get_by_label("Search Twitch games").press("Escape")
        expect(self.page.locator("#gamesViewDrops")).to_be_enabled()
        expect(self.tile(GAME_NAME)).to_have_count(1)

    def test_name_without_a_twitch_match_can_be_added_as_typed_and_is_flagged(self):
        self.open_games()
        search = self.page.get_by_label("Search Twitch games")
        search.fill("Zzz Renamed Game")
        expect(self.page.locator("#gamesStatus")).to_have_text("No Twitch game matches “Zzz Renamed Game”.")
        add_typed = self.page.get_by_role("button", name="Add “Zzz Renamed Game” exactly as typed")
        add_typed.click()
        expect(self.order()).to_have_text(["Zzz Renamed Game"])
        expect(add_typed).to_be_hidden()
        self.wait_saved(["Zzz Renamed Game"])
        expect(self.page.locator("#priorityRankedList .priority-sub")).to_have_text("Not found on Twitch")

    def test_exact_match_offers_no_typed_duplicate(self):
        self.open_games()
        self.page.get_by_label("Search Twitch games").fill(GAME_NAME.lower())
        expect(self.tile(GAME_NAME)).to_have_count(1)
        expect(self.page.locator("#btnAddTyped")).to_be_hidden()


class AllGamesTests(GamesPageTestCase):
    def test_all_games_pages_through_the_whole_catalog(self):
        self.open_games()
        self.page.locator("#gamesViewAll").click()
        tiles = self.page.locator("#gamesGrid .game-tile")
        # Page one, then infinite scroll / "Load more" fetches the rest; no duplicates.
        expect(tiles.first).to_be_visible()
        self.page.locator("#gamesSentinel").scroll_into_view_if_needed()
        try:
            expect(tiles).to_have_count(len(CATALOG), timeout=3000)
        except AssertionError:
            more = self.page.locator("#btnGamesMore")
            if more.is_visible():
                more.click()
            expect(tiles).to_have_count(len(CATALOG))
        expect(self.page.locator("#btnGamesMore")).to_be_hidden()
        names = tiles.locator(".game-name").all_inner_texts()
        self.assertEqual(names, [g["displayName"] for g in CATALOG])
        self.assertGreater(len(CATALOG), FIRST_PAGE)

    def test_account_progress_appears_in_catalog_and_search_but_not_unrelated_games(self):
        self.open_games()
        self.page.locator("#gamesViewAll").click()
        expect(self.tile(GAME_NAME).locator(".drops-tag")).to_have_text("Drops · 1 campaign")
        expect(self.tile(GAME_NAME).locator(".drop-progress")).to_have_text("0/1 mined")
        expect(self.tile("Catalog Game 01").locator(".drop-progress")).to_have_count(0)

        self.page.get_by_label("Search Twitch games").fill(GAME_NAME.lower())
        expect(self.tile(GAME_NAME)).to_have_count(1)
        expect(self.tile(GAME_NAME).locator(".drop-progress")).to_have_text("0/1 mined")


class MiningOrderTests(GamesPageTestCase):
    harness_options = {"priority": (GAME_NAME, "Catalog Game 01")}

    def test_reorder_remove_and_mode_persist_and_show_in_settings_summary(self):
        self.open_games()
        expect(self.order()).to_have_text([GAME_NAME, "Catalog Game 01"])
        self.page.get_by_role("button", name="Move Catalog Game 01 up").click()
        expect(self.order()).to_have_text(["Catalog Game 01", GAME_NAME])
        self.wait_saved(["Catalog Game 01", GAME_NAME])

        mode = self.page.get_by_label("Priority mode")
        mode.select_option(label="Ending Soonest")
        expect(mode).to_be_enabled()
        self.assertEqual(json.loads(self.harness.settings_path.read_text())["priority_mode"]["data"], 1)

        self.open_settings()
        expect(self.page.locator("#prioritySummary")).to_have_text(
            f"Mining order: Catalog Game 01, {GAME_NAME}. Mode: Ending Soonest.")
        self.page.get_by_role("button", name="Choose games").click()
        self.page.get_by_role("button", name=f"Remove {GAME_NAME}").click()
        expect(self.order()).to_have_text(["Catalog Game 01"])
        self.wait_saved(["Catalog Game 01"])


class DisconnectedCatalogTests(GamesPageTestCase):
    harness_options = {"priority": (), "disconnected": True}

    def test_browsing_twitch_requires_an_account_and_says_so(self):
        self.open_games()
        expect(self.page.locator("#gamesViewAll")).to_have_attribute("aria-selected", "true")
        expect(self.page.locator("#gamesStatus")).to_have_text("Connect a Twitch account to browse games")
        expect(self.page.get_by_role("button", name="Try again")).to_be_visible()
