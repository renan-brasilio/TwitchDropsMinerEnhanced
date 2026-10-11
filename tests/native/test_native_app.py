"""Smoke tests for the native macOS shell (risk row 6), driven through System Events.

Opt-in and local only: set ``RUN_NATIVE_UI=1`` on a Mac whose terminal has the Accessibility
permission. GitHub runners cannot grant it; CI covers the extracted logic with
``swift test --package-path mac`` instead.

Isolation: every test runs a TEST COPY of the app, compiled into a temp dir with its own bundle
identifier (so its UserDefaults domain is not the user's), pointed at an in-process backend
(``tests/e2e/harness.py``) through argument-domain overrides, so it never spawns or connects to
the user's miner on port 23432. The copy is driven by PID, never by name or bundle id shared
with the installed app.
"""
from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
USER_BUNDLE_ID = "dev.brasilio.TwitchDropsMinerEnhanced"
TEST_BUNDLE_ID = "dev.brasilio.TwitchDropsMinerEnhanced.uitest"
TEST_APP_NAME = "Twitch Drops Miner Enhanced UITest"
KEEP_RUNNING_ITEM = "Keep Running in Menu Bar When Window Closes"
TIMEOUT = 20.0

ENABLED = os.environ.get("RUN_NATIVE_UI") == "1"
if sys.platform != "darwin":
    SKIP_REASON = "native macOS UI smoke tests require macOS"
elif not ENABLED:
    SKIP_REASON = ("native macOS UI smoke tests drive the app through System Events; "
                   "set RUN_NATIVE_UI=1 to run them (needs Accessibility permission for this terminal)")
else:
    SKIP_REASON = ""

# --- System Events scripts (arguments arrive through argv, never string interpolation) ----------
_PROCESS = "set p to first process whose unix id is ((item 1 of argv) as integer)"

AX_ENABLED = 'tell application "System Events" to return UI elements enabled'

WINDOW_STATE = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set frontmost of p to true
        delay 0.2
        set names to ""
        repeat with w in (windows of p)
            set names to names & (name of w) & linefeed
        end repeat
        return ((background only of p) as text) & linefeed & ((count menu bar items of menu bar 2 of p) as text) & linefeed & names
    end tell
end run
"""

CLICK_APP_MENU = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set frontmost of p to true
        delay 0.2
        click menu item (item 2 of argv) of menu 1 of menu bar item 2 of menu bar 1 of p
    end tell
end run
"""

APP_MENU_MARK = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        return (value of attribute "AXMenuItemMarkChar" of menu item (item 2 of argv) of menu 1 of menu bar item 2 of menu bar 1 of p) as text
    end tell
end run
"""

CLICK_STATUS_MENU = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set statusItem to menu bar item 1 of menu bar 2 of p
        click statusItem
        delay 0.3
        click menu item (item 2 of argv) of menu 1 of statusItem
    end tell
end run
"""

CLOSE_WINDOW = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set frontmost of p to true
        delay 0.2
        click (first button of window 1 of p whose subrole is "AXCloseButton")
    end tell
end run
"""

# argv: pid, role -> titles of every element with that role in the window (one per line)
WEB_TITLES = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set frontmost of p to true
        set out to ""
        set els to entire contents of window 1 of p
        repeat with e in els
            try
                if role of e is (item 2 of argv) then set out to out & (title of e) & linefeed
            end try
        end repeat
        return out
    end tell
end run
"""

# argv: pid, then (role, title, action, value) groups; action is get | set | press.
# One traversal of the web content per call; "get" results come back one per line.
WEB_ACTIONS = f"""
on run argv
    tell application "System Events"
        {_PROCESS}
        set frontmost of p to true
        set els to entire contents of window 1 of p
        set out to ""
        set i to 2
        repeat while i < (count argv)
            set wantRole to item i of argv
            set wantTitle to item (i + 1) of argv
            set act to item (i + 2) of argv
            set val to item (i + 3) of argv
            set found to false
            repeat with e in els
                set r to ""
                set t to ""
                try
                    set r to role of e
                    set t to title of e
                end try
                if r is wantRole and t is wantTitle then
                    set found to true
                    if act is "get" then
                        set out to out & ((value of e) as text) & linefeed
                    else if act is "set" then
                        set focused of e to true
                        set value of e to val
                    else if act is "press" then
                        perform action "AXPress" of e
                    end if
                    exit repeat
                end if
            end repeat
            if not found then error "No " & wantRole & " titled " & quoted form of wantTitle
            set i to i + 4
        end repeat
        return out
    end tell
end run
"""


def osascript(script: str, *args: object) -> str:
    result = subprocess.run(["osascript", "-", *map(str, args)], input=script,
                            capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise AssertionError(f"osascript failed: {result.stderr.strip()}")
    return result.stdout.rstrip("\n")


def wait_until(probe, description: str, timeout: float = TIMEOUT):
    """Polls an out-of-process UI condition until it holds; returns the last observed value."""
    deadline = time.monotonic() + timeout
    last = error = None
    while time.monotonic() < deadline:
        try:
            last = probe()
            error = None
            if last:
                return last
        except AssertionError as exc:  # transient AX errors while windows appear/disappear
            error = exc
        time.sleep(0.25)
    raise AssertionError(f"Timed out waiting for {description}; last value {last!r}, error {error}")


def read_domain(bundle_id: str) -> dict:
    result = subprocess.run(["defaults", "export", bundle_id, "-"], capture_output=True)
    return plistlib.loads(result.stdout) if result.returncode == 0 and result.stdout else {}


def delete_domain(bundle_id: str) -> None:
    assert bundle_id == TEST_BUNDLE_ID, "refusing to delete any domain but the test copy's"
    subprocess.run(["defaults", "delete", bundle_id], capture_output=True)


def build_test_app(destination: Path) -> Path:
    """Compiles the shell like build_mac.sh, with build_mac.sh's Info.plist but the test bundle id."""
    app = destination / f"{TEST_APP_NAME}.app"
    macos = app / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    script = (REPO / "build_mac.sh").read_text()
    plist_xml = re.search(r"cat << 'EOF' > \"\$APP_BUNDLE/Contents/Info.plist\"\n(.*?)\nEOF\n", script, re.S)
    if plist_xml is None:
        raise AssertionError("Could not find the Info.plist heredoc in build_mac.sh")
    info = plistlib.loads(plist_xml.group(1).encode())
    info.update(CFBundleIdentifier=TEST_BUNDLE_ID, CFBundleName=TEST_APP_NAME, CFBundleDisplayName=TEST_APP_NAME)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    sources = [REPO / "mac" / "main.swift", *sorted((REPO / "mac" / "Sources" / "MinerCore").glob("*.swift"))]
    build = subprocess.run(["swiftc", "-O", *map(str, sources), "-o", str(macos / info["CFBundleExecutable"])],
                           capture_output=True, text=True, timeout=600)
    if build.returncode:
        raise AssertionError(f"swiftc failed:\n{build.stderr}")
    return app


@unittest.skipUnless(ENABLED and sys.platform == "darwin", SKIP_REASON)
class NativeAppSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if osascript(AX_ENABLED) != "true":
            raise AssertionError(
                "RUN_NATIVE_UI=1 but this terminal lacks the Accessibility permission. Grant it in "
                "System Settings > Privacy & Security > Accessibility, or unset RUN_NATIVE_UI.")
        if str(REPO / "tests") not in sys.path:
            sys.path.insert(0, str(REPO / "tests"))
        from e2e.harness import STREAMER, DashboardHarness
        cls.Harness, cls.streamer = DashboardHarness, STREAMER["login"]
        cls._build_dir = Path(tempfile.mkdtemp(prefix="tdm-native-ui-"))
        cls.addClassCleanup(cls._remove_test_app_traces)
        cls.app_path = build_test_app(cls._build_dir)

    @classmethod
    def _remove_test_app_traces(cls) -> None:
        delete_domain(TEST_BUNDLE_ID)
        library = Path.home() / "Library"
        for folder in ("Preferences", "Caches", "HTTPStorages", "WebKit", "Saved Application State"):
            for trace in library.joinpath(folder).glob(f"{TEST_BUNDLE_ID}*"):
                shutil.rmtree(trace, ignore_errors=True) if trace.is_dir() else trace.unlink(missing_ok=True)
        lsregister = Path("/System/Library/Frameworks/CoreServices.framework/Frameworks/"
                          "LaunchServices.framework/Support/lsregister")
        app_path = getattr(cls, "app_path", None)
        if app_path is not None and lsregister.exists():
            subprocess.run([str(lsregister), "-u", str(app_path)], capture_output=True)
        shutil.rmtree(cls._build_dir, ignore_errors=True)

    def setUp(self) -> None:
        delete_domain(TEST_BUNDLE_ID)
        self.user_domain_before = read_domain(USER_BUNDLE_ID)
        self.harness = self.Harness()
        self.harness.start()
        self.addCleanup(self.harness.stop)
        self.server_url = f"http://127.0.0.1:{self.harness.port}"
        executable = self.app_path / "Contents" / "MacOS" / "TwitchDropsMinerMenu"
        self.app = subprocess.Popen(
            [str(executable), "-isRemote", "YES", "-serverURL", self.server_url],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(delete_domain, TEST_BUNDLE_ID)
        self.addCleanup(self._terminate_app)  # LIFO: app first, then its defaults, then the backend
        self.wait_for_window()

    def tearDown(self) -> None:
        self.assertEqual(read_domain(USER_BUNDLE_ID), self.user_domain_before,
                         "the test copy must never write the installed app's defaults")

    def _terminate_app(self) -> None:
        if self.app.poll() is None:
            self.app.terminate()
            try:
                self.app.wait(10)
            except subprocess.TimeoutExpired:
                self.app.kill()
                self.app.wait(10)

    # -- observations -------------------------------------------------------------------------
    def window_state(self) -> tuple[bool, int, list[str]]:
        """(running in the menu bar only, status items, visible window titles)."""
        lines = osascript(WINDOW_STATE, self.app.pid).split("\n")
        return lines[0] == "true", int(lines[1]), [name for name in lines[2:] if name]

    def wait_for_window(self) -> list[str]:
        return wait_until(lambda: self.window_state()[2], "the dashboard window")

    def wait_for_menu_bar_only(self) -> None:
        def hidden():
            menu_bar_only, status_items, windows = self.window_state()
            return menu_bar_only and not windows and status_items >= 1
        wait_until(hidden, "the window to close into the menu bar")
        self.assertIsNone(self.app.poll(), "closing the window must not quit the app")

    def wait_for_restored_window(self, how: str) -> None:
        def restored():
            menu_bar_only, _, windows = self.window_state()
            return not menu_bar_only and windows
        wait_until(restored, f"{how} to restore the window and Dock icon")

    def web_titles(self, role: str) -> list[str]:
        return [title for title in osascript(WEB_TITLES, self.app.pid, role).split("\n") if title]

    def web(self, *steps: tuple[str, str, str, str]) -> list[str]:
        args = [part for step in steps for part in step]
        return osascript(WEB_ACTIONS, self.app.pid, *args).split("\n")

    # -- tests --------------------------------------------------------------------------------
    def test_window_title_shows_remote_miner_status(self) -> None:
        title = wait_until(
            lambda: next((t for t in self.window_state()[2] if f"Watching: {self.streamer}" in t), None),
            "the window title to show the watched streamer")
        self.assertEqual(title, f"Twitch Drops Miner Enhanced — Watching: {self.streamer} (Remote [127.0.0.1])")

    def test_settings_menu_opens_settings_tab(self) -> None:
        wait_until(lambda: "Live Channels" in self.web_titles("AXButton"), "the dashboard to load")
        self.assertNotIn("Miner connection", self.web_titles("AXHeading"))

        osascript(CLICK_APP_MENU, self.app.pid, "Settings...")

        # The connection section only renders after the app publishes native-settings.
        wait_until(
            lambda: {"Twitch account", "Application", "Miner connection"} <= set(self.web_titles("AXHeading")),
            "the Settings tab with the native sections")

    def test_close_keeps_running_in_menu_bar_and_reopens(self) -> None:
        self.assertEqual(osascript(APP_MENU_MARK, self.app.pid, KEEP_RUNNING_ITEM), "✓")

        osascript(CLOSE_WINDOW, self.app.pid)
        self.wait_for_menu_bar_only()

        osascript(CLICK_STATUS_MENU, self.app.pid, "Open Dashboard Window")
        self.wait_for_restored_window("the status item")

        osascript(CLOSE_WINDOW, self.app.pid)
        self.wait_for_menu_bar_only()

        subprocess.run(["open", str(self.app_path)], check=True)  # Dock / Finder reopen of the running copy
        self.wait_for_restored_window("reopening the app")
        self.assertIsNone(self.app.poll())

    def test_close_quits_when_keep_running_is_off(self) -> None:
        osascript(CLICK_APP_MENU, self.app.pid, KEEP_RUNNING_ITEM)
        wait_until(lambda: osascript(APP_MENU_MARK, self.app.pid, KEEP_RUNNING_ITEM) == "missing value",
                   "the keep-running menu item to uncheck")
        self.assertIs(read_domain(TEST_BUNDLE_ID).get("keepRunningOnClose"), False)

        osascript(CLOSE_WINDOW, self.app.pid)

        self.assertEqual(self.app.wait(TIMEOUT), 0)

    def test_connection_switch_persists_to_test_domain(self) -> None:
        osascript(CLICK_APP_MENU, self.app.pid, "Settings...")
        shown = wait_until(
            lambda: self.web(("AXTextField", "Server URL:", "get", ""))[0],
            "the Settings page to show the current connection")
        self.assertEqual(shown, self.server_url)

        new_url = f"http://localhost:{self.harness.port}"
        self.web(("AXTextField", "Server URL:", "set", new_url),
                 ("AXTextField", "API key (optional):", "set", "uitest-key"),
                 ("AXButton", "Save & reconnect", "press", ""))

        saved = wait_until(
            lambda: (d := read_domain(TEST_BUNDLE_ID)).get("serverURL") == new_url and d,
            "the new connection in the test copy's defaults")
        self.assertIs(saved.get("isRemote"), True)
        self.assertEqual(saved.get("apiKey"), "uitest-key")


if __name__ == "__main__":
    unittest.main()
