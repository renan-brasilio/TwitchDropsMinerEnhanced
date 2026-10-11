"""Regression coverage for settings.json persistence (risk row 8)."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from yarl import URL

from constants import PriorityMode
from settings import Settings


def args(**overrides):
    return SimpleNamespace(dump=False, **overrides)


class SettingsPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name, "settings.json")
        self.path_patch = patch("settings.SETTINGS_PATH", self.path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.temp.cleanup()

    def write_file(self, contents):
        self.path.write_text(json.dumps(contents), encoding="utf8")

    def test_missing_file_starts_with_first_run_defaults(self):
        settings = Settings(args())

        self.assertEqual(settings.priority, [])
        self.assertEqual(settings.exclude, set())
        self.assertIs(settings.priority_mode, PriorityMode.PRIORITY_ONLY)
        self.assertTrue(settings.auto_claim)
        self.assertFalse(settings.auth_disconnected)
        self.assertEqual(settings.auth_token, "")
        self.assertEqual(settings.proxy, URL())

    def test_changed_settings_survive_a_restart_with_their_types(self):
        settings = Settings(args())
        settings.proxy = URL("http://127.0.0.1:8080")
        settings.language = "Deutsch"
        settings.dark_mode = True
        settings.exclude = {"Rust", "Dota 2"}
        settings.priority = ["Overwatch 2", "Valorant"]
        settings.autostart_tray = True
        settings.connection_quality = 4
        settings.tray_notifications = False
        settings.enable_badges_emotes = True
        settings.available_drops_check = True
        settings.priority_mode = PriorityMode.ENDING_SOONEST
        settings.auth_token = "synthetic-token"
        settings.auth_disconnected = True
        settings.api_port = 24000
        settings.auto_claim = False
        settings.save()

        restarted = Settings(args())

        self.assertEqual(restarted.proxy, URL("http://127.0.0.1:8080"))
        self.assertIsInstance(restarted.proxy, URL)
        self.assertEqual(restarted.language, "Deutsch")
        self.assertIs(restarted.dark_mode, True)
        self.assertEqual(restarted.exclude, {"Rust", "Dota 2"})
        self.assertIsInstance(restarted.exclude, set)
        self.assertEqual(restarted.priority, ["Overwatch 2", "Valorant"])
        self.assertIs(restarted.autostart_tray, True)
        self.assertEqual(restarted.connection_quality, 4)
        self.assertIs(restarted.tray_notifications, False)
        self.assertIs(restarted.enable_badges_emotes, True)
        self.assertIs(restarted.available_drops_check, True)
        self.assertIs(restarted.priority_mode, PriorityMode.ENDING_SOONEST)
        self.assertEqual(restarted.auth_token, "synthetic-token")
        self.assertIs(restarted.auth_disconnected, True)
        self.assertEqual(restarted.api_port, 24000)
        self.assertIs(restarted.auto_claim, False)

    def test_partial_file_keeps_saved_values_and_fills_missing_keys_with_defaults(self):
        self.write_file({"priority": ["Overwatch 2"], "auto_claim": False})

        settings = Settings(args())

        self.assertEqual(settings.priority, ["Overwatch 2"])
        self.assertIs(settings.auto_claim, False)
        self.assertEqual(settings.exclude, set())
        self.assertIs(settings.priority_mode, PriorityMode.PRIORITY_ONLY)
        self.assertEqual(settings.api_port, 23432)

    def test_wrongly_typed_or_unknown_values_fall_back_to_defaults(self):
        self.write_file({
            "priority": "Overwatch 2",                      # str instead of list
            "exclude": ["Rust"],                            # untagged list instead of set
            "priority_mode": 1,                             # untagged int instead of enum
            "proxy": {"__type": "Mystery", "data": "x"},    # unknown serialized type
            "api_port": 24000,
            "obsolete_key": True,
        })

        settings = Settings(args())

        self.assertEqual(settings.priority, [])
        self.assertEqual(settings.exclude, set())
        self.assertIs(settings.priority_mode, PriorityMode.PRIORITY_ONLY)
        self.assertEqual(settings.proxy, URL())
        self.assertEqual(settings.api_port, 24000)
        settings.save(force=True)
        self.assertNotIn("obsolete_key", json.loads(self.path.read_text(encoding="utf8")))

    def test_corrupt_interrupted_save_falls_back_to_the_last_complete_file(self):
        self.write_file({"priority": ["Overwatch 2"]})
        Path(self.temp.name, "settings.json.new").write_text('{"priority": ["Valo', encoding="utf8")

        settings = Settings(args())

        self.assertEqual(settings.priority, ["Overwatch 2"])
        self.assertFalse(Path(self.temp.name, "settings.json.new").exists())

    def test_completed_but_unrenamed_save_is_preferred_over_the_older_file(self):
        self.write_file({"priority": ["Old Game"]})
        Path(self.temp.name, "settings.json.new").write_text(
            json.dumps({"priority": ["New Game"]}), encoding="utf8")

        self.assertEqual(Settings(args()).priority, ["New Game"])

    def test_setting_an_unknown_attribute_raises_type_error_and_is_not_persisted(self):
        settings = Settings(args())

        with self.assertRaises(TypeError):
            settings.not_a_setting = 1
        settings.save(force=True)

        self.assertNotIn("not_a_setting", json.loads(self.path.read_text(encoding="utf8")))

    def test_settings_cannot_be_deleted(self):
        settings = Settings(args())

        with self.assertRaises(RuntimeError):
            del settings.priority
        self.assertEqual(settings.priority, [])

    def test_command_line_args_take_precedence_over_file_values(self):
        self.write_file({"language": "Deutsch", "api_port": 24000})

        settings = Settings(args(language="Español"))

        self.assertEqual(settings.language, "Español")
        self.assertEqual(settings.api_port, 24000)


if __name__ == "__main__":
    unittest.main()
