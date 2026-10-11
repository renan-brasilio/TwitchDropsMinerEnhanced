"""Release packaging consistency (risk row 9): one version labels every release artifact."""
import os
import plistlib
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from version import __version__


ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "build_mac.sh"
RELEASE_WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
TAG = f"v{__version__}"
ARTIFACT_BASE = f"TwitchDropsMinerEnhanced-{TAG}-macOS-arm64"


def build_script_plist():
    script = BUILD_SCRIPT.read_text(encoding="utf8")
    match = re.search(r"<<\s*'EOF'\s*>\s*\"\$APP_BUNDLE/Contents/Info\.plist\"\n(.*?)\nEOF\n", script, re.S)
    assert match is not None, "build_mac.sh no longer generates Info.plist from a heredoc"
    return plistlib.loads(match.group(1).encode("utf8"))


def build_script_artifacts():
    """Evaluate the build script's own artifact-naming assignments against this checkout."""
    script = BUILD_SCRIPT.read_text(encoding="utf8")
    assignments = re.findall(r"^(?:VERSION_TAG|ARTIFACT_BASE|DMG_OUT|ZIP_OUT)=.*$", script, re.M)
    shell = "\n".join(assignments + ['printf "%s\\n%s\\n" "$(basename "$DMG_OUT")" "$(basename "$ZIP_OUT")"'])
    result = subprocess.run(
        ["bash", "-c", shell], capture_output=True, text=True, check=True, timeout=10,
        env={"PATH": os.environ.get("PATH", ""), "DIR": str(ROOT)},
    )
    return result.stdout.split()


def release_uploads(tag):
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf8")
    match = re.search(r"^(\s*)files:\s*\|\n((?:\1\s+\S.*\n)+)", workflow, re.M)
    assert match is not None, "release.yml no longer declares uploaded files"
    return [line.strip().replace("${{ github.ref_name }}", tag) for line in match.group(2).splitlines()]


class ReleaseMetadataTests(unittest.TestCase):
    def test_app_bundle_versions_match_the_python_version(self):
        plist = build_script_plist()

        self.assertEqual(plist["CFBundleShortVersionString"], __version__)
        self.assertEqual(plist["CFBundleVersion"], __version__)

    @unittest.skipUnless(shutil.which("bash"), "bash is required to evaluate build_mac.sh naming")
    def test_build_script_names_artifacts_with_the_version_tag(self):
        self.assertEqual(build_script_artifacts(), [f"{ARTIFACT_BASE}.dmg", f"{ARTIFACT_BASE}.zip"])

    @unittest.skipUnless(shutil.which("bash"), "bash is required to evaluate build_mac.sh naming")
    def test_release_workflow_uploads_exactly_the_built_artifacts_for_the_version_tag(self):
        self.assertEqual(release_uploads(TAG), build_script_artifacts())

    def test_release_notes_exist_for_the_current_version(self):
        notes = ROOT / "docs" / f"RELEASE_NOTES_{TAG}.md"

        self.assertTrue(notes.is_file(), f"missing {notes.relative_to(ROOT)}")
        self.assertEqual(notes.read_text(encoding="utf8").splitlines()[0].strip(), f"# {TAG}")


if __name__ == "__main__":
    unittest.main()
