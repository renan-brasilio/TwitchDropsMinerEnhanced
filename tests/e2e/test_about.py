"""Settings → About: versions, project links and Mac app updates (web side of the mac bridge)."""
from __future__ import annotations

from version import __version__

from .harness import DashboardTestCase, expect, native_bridge_script

REPO = "https://github.com/renan-brasilio/TwitchDropsMinerEnhanced"
RELEASE_PAGE = f"{REPO}/releases/tag/v9.9.0"
AVAILABLE = {"state": "available", "latestVersion": "9.9.0", "releaseURL": RELEASE_PAGE}


class AboutTests(DashboardTestCase):
    harness_options = {"disconnected": True}

    def about(self):
        return self.page.locator("#aboutSettings")

    def open_in_app(self, **bridge):
        self.page = self.context.new_page()
        self.page.add_init_script(native_bridge_script(**bridge))
        self.page.goto(self.harness.url)
        self.open_settings()
        expect(self.about().locator("#aboutAppVersion")).to_be_visible()

    def posted(self, count):
        self.page.wait_for_function("n => window.__nativeBridge.messages.length >= n", arg=count)
        return self.page.evaluate("window.__nativeBridge.messages")

    def test_browser_shows_the_miner_version_and_project_links_without_update_controls(self):
        self.open_dashboard()
        self.open_settings()
        about = self.about()
        expect(about.locator("#aboutMinerVersion")).to_have_text(f"v{__version__}")
        for name, href in (("GitHub repository", REPO), ("Releases", f"{REPO}/releases"),
                           ("Report an issue", f"{REPO}/issues")):
            with self.subTest(link=name):
                expect(about.get_by_role("link", name=name)).to_have_attribute("href", href)
        expect(about.locator("#aboutAppRow")).to_be_hidden()
        expect(about.get_by_role("button", name="Check for updates")).to_be_hidden()

    def test_checking_finds_a_release_and_install_is_handed_to_the_app(self):
        self.open_in_app(app_version="1.0.0", mode="remote", server_url="https://miner.example.test:8443",
                         check_result=AVAILABLE)
        about = self.about()
        expect(about.locator("#aboutAppVersion")).to_have_text("v1.0.0")
        expect(about.locator("#aboutMinerVersion")).to_have_text(f"v{__version__} on miner.example.test:8443")
        expect(about.locator("#updateStatus")).to_have_text("Not checked yet.")

        about.get_by_role("button", name="Check for updates").click()
        self.assertEqual(self.posted(1), [{"update": "check"}])
        expect(about.locator("#updateStatus")).to_have_text("Version 9.9.0 is available.")
        expect(about.get_by_role("link", name="Release notes")).to_have_attribute("href", RELEASE_PAGE)
        # The app update never touches the miner, so a miner behind the release is called out.
        expect(about.locator("#minerUpdateHint")).to_have_text(
            f"The miner on miner.example.test:8443 runs v{__version__}; v9.9.0 is out.")

        install = about.get_by_role("button", name="Install v9.9.0 and restart")
        install.click()
        self.assertEqual(self.posted(2)[1], {"update": "install"})
        expect(about.locator("#updateStatus")).to_have_text("Installing v9.9.0. The app restarts when it's done.")
        expect(install).to_be_disabled()
        expect(about.get_by_role("button", name="Check for updates")).to_be_disabled()

    def test_up_to_date_hides_install_and_the_daily_check_can_be_turned_off(self):
        self.open_in_app(app_version=__version__, auto_check_updates=True)
        about = self.about()
        about.get_by_role("button", name="Check for updates").click()
        expect(about.locator("#updateStatus")).to_have_text(f"Up to date. v{__version__} is the latest release.")
        expect(about.locator("#btnInstallUpdate")).to_be_hidden()
        expect(about.locator("#minerUpdateHint")).to_be_hidden()

        auto = about.get_by_label("Check automatically every day")
        expect(auto).to_be_checked()
        auto.uncheck()
        self.assertEqual(self.posted(2)[1], {"autoCheckUpdates": False})
        expect(auto).not_to_be_checked()
        expect(auto).to_be_enabled()  # the app confirmed the change
