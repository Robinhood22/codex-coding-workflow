from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import project_map  # noqa: E402


class ProjectMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(ROOT / "scripts" / "project_map.py"), "--repo", str(self.repo), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def seed_repo(self) -> None:
        (self.repo / "package.json").write_text(
            json.dumps(
                {
                    "name": "demo-project",
                    "scripts": {
                        "build": "echo build",
                        "test": "echo test",
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (self.repo / "src").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "router.py").write_text(
            '"""HTTP router root for demo requests."""\n\nROUTES = []\n',
            encoding="utf-8",
        )
        (self.repo / "scripts").mkdir(parents=True, exist_ok=True)
        (self.repo / "scripts" / "dev.py").write_text(
            "# Launch the local development loop.\nprint('dev')\n",
            encoding="utf-8",
        )
        (self.repo / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo / "tests" / "test_widget.py").write_text(
            "# Covers widget regressions.\n\ndef test_widget():\n    assert True\n",
            encoding="utf-8",
        )
        (self.repo / "pytest.ini").write_text("[pytest]\naddopts = -q\n", encoding="utf-8")
        (self.repo / ".env.local").write_text("SECRET=value\n", encoding="utf-8")
        (self.repo / "node_modules" / "pkg").mkdir(parents=True, exist_ok=True)
        (self.repo / "node_modules" / "pkg" / "index.js").write_text("console.log('skip')\n", encoding="utf-8")
        (self.repo / "dist").mkdir(parents=True, exist_ok=True)
        (self.repo / "dist" / "bundle.js").write_text("console.log('skip')\n", encoding="utf-8")
        (self.repo / ".codex-workflows").mkdir(parents=True, exist_ok=True)
        (self.repo / ".codex-workflows" / "notes.md").write_text("# Ignore me\n", encoding="utf-8")
        (self.repo / "large.txt").write_bytes(b"a" * (1_000_000 + 1))
        (self.repo / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    def test_generate_writes_stable_filtered_project_map(self) -> None:
        self.seed_repo()

        result = project_map.generate_project_map(self.repo)
        map_path = Path(result["path"])
        text = map_path.read_text(encoding="utf-8")

        self.assertTrue(map_path.exists())
        self.assertIn("## Manifests And Config", text)
        self.assertIn("## Entrypoints And Router Roots", text)
        self.assertIn("## Server, CLI, And Scripts", text)
        self.assertIn("## Tests And Test Config", text)
        self.assertIn("## Changed Files", text)
        self.assertIn("`package.json` — Package manifest for demo-project; scripts: build, test.", text)
        self.assertIn("`src/router.py` — HTTP router root for demo requests.", text)
        self.assertIn("`scripts/dev.py` — Launch the local development loop.", text)
        self.assertIn("`tests/test_widget.py` — Covers widget regressions.", text)
        self.assertIn("`pytest.ini`", text)
        self.assertNotIn(".env.local", text)
        self.assertNotIn("node_modules/pkg/index.js", text)
        self.assertNotIn("dist/bundle.js", text)
        self.assertNotIn(".codex-workflows/notes.md", text)
        self.assertNotIn("large.txt", text)
        self.assertNotIn("logo.png", text)
        self.assertLess(text.index("## Manifests And Config"), text.index("## Entrypoints And Router Roots"))
        self.assertLess(text.index("## Entrypoints And Router Roots"), text.index("## Server, CLI, And Scripts"))
        self.assertLess(text.index("## Server, CLI, And Scripts"), text.index("## Tests And Test Config"))
        self.assertLess(text.index("## Tests And Test Config"), text.index("## Changed Files"))

    def test_check_ignores_timestamp_drift(self) -> None:
        self.seed_repo()
        result = project_map.generate_project_map(self.repo)
        map_path = Path(result["path"])
        original = map_path.read_text(encoding="utf-8")
        updated = original.replace(
            next(line for line in original.splitlines() if line.startswith(project_map.TIMESTAMP_PREFIX)),
            f"{project_map.TIMESTAMP_PREFIX} 1999-01-01T00:00:00+00:00",
            1,
        )
        map_path.write_text(updated, encoding="utf-8")

        check = project_map.check_project_map(self.repo)

        self.assertTrue(check["matches"])
        self.assertEqual(check["status"], "match")

    def test_check_detects_real_drift(self) -> None:
        self.seed_repo()
        project_map.generate_project_map(self.repo)
        (self.repo / "src" / "app.py").write_text(
            "# Main application entrypoint.\nprint('app')\n",
            encoding="utf-8",
        )

        check = project_map.check_project_map(self.repo)

        self.assertFalse(check["matches"])
        self.assertEqual(check["status"], "drift")

    def test_cli_generate_json_reports_output_path(self) -> None:
        self.seed_repo()

        result = self.run_script("--generate", "--json")

        self.assertEqual(result.returncode, 0)
        parsed = json.loads(result.stdout)
        self.assertEqual(parsed["action"], "generate")
        self.assertTrue(parsed["path"].endswith(".codex-workflows/project-map.md"))
        self.assertGreater(parsed["section_counts"]["Changed Files"], 0)


if __name__ == "__main__":
    unittest.main()
