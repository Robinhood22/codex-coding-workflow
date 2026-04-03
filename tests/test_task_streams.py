from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import state_doctor  # noqa: E402
import verification_summary  # noqa: E402
import workflow_state  # noqa: E402


class TaskStreamTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_legacy_task_loop_stays_legacy_without_streams(self) -> None:
        workflow_state.ensure_state_files(self.repo)
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: First legacy step\n- [ ] Pending: Follow-up legacy step",
        )

        task_state = workflow_state.get_task_state(self.repo)

        self.assertEqual(task_state["mode"], "legacy")
        self.assertEqual(task_state["status"], "healthy")
        self.assertEqual(task_state["active_step_count"], 1)
        self.assertEqual(task_state["stream_count"], 1)

    def test_stream_update_migrates_legacy_loop_and_generates_summary(self) -> None:
        workflow_state.ensure_state_files(self.repo)
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Keep compatibility\n- [ ] Pending: Preserve the old loop",
        )
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Add docs stream",
            stream_id="docs",
        )

        task_state = workflow_state.get_task_state(self.repo)
        summary_path = self.repo / ".codex-workflows" / "active-task-loop.md"
        summary_text = summary_path.read_text(encoding="utf-8")

        self.assertEqual(task_state["mode"], "streams")
        self.assertEqual(task_state["primary_stream_id"], "default")
        self.assertEqual(task_state["stream_count"], 2)
        self.assertEqual([stream["id"] for stream in task_state["streams"]], ["default", "docs"])
        self.assertIn("Mode: streams", summary_text)
        self.assertIn("Primary stream: default", summary_text)
        self.assertIn("## Default", summary_text)
        self.assertIn("## Docs", summary_text)

    def test_verification_summary_flags_uncovered_open_stream(self) -> None:
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Backend implementation",
            stream_id="backend",
            set_primary=True,
        )
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Docs cleanup",
            stream_id="docs",
        )
        workflow_state.append_verification_entry(
            self.repo,
            {
                "timestamp": workflow_state.now_timestamp(),
                "scope": {"files": ["backend.py"]},
                "checks": [],
                "verdict": "PASS",
                "stream_id": "backend",
            },
        )

        summary = verification_summary.build_verification_summary(self.repo)
        coverage = {item["id"]: item["covered"] for item in summary["stream_coverage"]}

        self.assertTrue(coverage["backend"])
        self.assertFalse(coverage["docs"])
        self.assertIn(
            "Task stream docs is missing current verification coverage.",
            summary["blockers"],
        )

    def test_state_doctor_repairs_invalid_stream_file(self) -> None:
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Backend implementation",
            stream_id="backend",
            set_primary=True,
        )
        stream_path = workflow_state.get_task_stream_path(self.repo, "backend")
        stream_path.write_text(
            (
                "# Task Stream\n"
                "ID: backend\n"
                "Title: Backend\n"
                "State: open\n"
                f"Updated: {workflow_state.now_timestamp()}\n\n"
                "- [ ] Active: One\n"
                "- [ ] Active: Two\n"
            ),
            encoding="utf-8",
        )

        before = state_doctor.build_check_report(self.repo)
        repair = state_doctor.repair_state(self.repo)
        after = state_doctor.build_check_report(self.repo)

        self.assertEqual(before["files"]["task_loop"]["status"], "invalid")
        self.assertIn("task_loop", repair["repaired"])
        self.assertEqual(after["files"]["task_loop"]["status"], "healthy")

    def test_memory_sync_cli_creates_and_lists_streams(self) -> None:
        script_path = ROOT / "scripts" / "memory_sync.py"
        subprocess.run(
            [
                "python3",
                str(script_path),
                "--repo",
                str(self.repo),
                "--init",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "python3",
                str(script_path),
                "--repo",
                str(self.repo),
                "--stream",
                "backend",
                "--set-task-loop",
                "- [ ] Active: Backend implementation",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        result = subprocess.run(
            [
                "python3",
                str(script_path),
                "--repo",
                str(self.repo),
                "--show",
                "--list-streams",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Streams:", result.stdout)
        self.assertIn("backend: healthy (open primary)", result.stdout)


if __name__ == "__main__":
    unittest.main()
