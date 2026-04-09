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

import analyze_change_scope  # noqa: E402
import branch_readiness  # noqa: E402
import memory_sync  # noqa: E402
import policy_check  # noqa: E402
import report_builder  # noqa: E402
import state_doctor  # noqa: E402
import workflow_state  # noqa: E402


class Phase1MemoryBuglogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self, script_name: str, *args: str, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(ROOT / "scripts" / script_name), "--repo", str(self.repo), *args],
            input=input_text,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_legacy_memory_stays_healthy_and_normalizes_optional_sections(self) -> None:
        paths = workflow_state.ensure_state_files(self.repo)
        legacy_memory = (
            "# Project Memory\n\n"
            "## Stable Facts\n"
            "- Existing durable fact.\n\n"
            "## Preferences\n"
            "- Existing workflow preference.\n\n"
            "## Constraints\n"
            "- Existing durable constraint.\n\n"
            "## Open Questions\n"
            "- Existing open question.\n"
        )
        paths["memory"].write_text(legacy_memory, encoding="utf-8")

        status = workflow_state.get_memory_status(self.repo)
        normalized = workflow_state.normalize_memory_text(legacy_memory)

        self.assertEqual(status["status"], "healthy")
        self.assertEqual(
            status["missing_optional_sections"],
            ["Do-Not-Repeat", "Decision Log"],
        )
        self.assertIn("## Do-Not-Repeat", normalized)
        self.assertIn("## Decision Log", normalized)
        self.assertIn("- Existing durable fact.", normalized)

    def test_memory_sync_append_memory_section_targets_optional_section_without_duplicates(self) -> None:
        self.run_script("memory_sync.py", "--init")
        self.run_script(
            "memory_sync.py",
            "--append-memory",
            "Avoid hand-editing generated snapshots.",
            "--append-memory-section",
            "Do-Not-Repeat",
        )
        self.run_script(
            "memory_sync.py",
            "--append-memory",
            "Avoid hand-editing generated snapshots.",
            "--append-memory-section",
            "Do-Not-Repeat",
        )

        memory_text = (self.repo / ".codex-workflows" / "memory.md").read_text(encoding="utf-8")
        sections = workflow_state.parse_memory_sections(memory_text)
        lines = [line for line in sections["Do-Not-Repeat"] if line.strip()]

        self.assertEqual(lines, ["- Avoid hand-editing generated snapshots."])

    def test_buglog_cli_append_and_search_json_output(self) -> None:
        payload = {
            "file": str(self.repo / "src" / "app.py"),
            "symptom": "Login retried forever after a stale token.",
            "root_cause": "Retry state was never reset after the token refresh path failed.",
            "fix": "Reset the retry counter before surfacing the refresh failure.",
            "tags": ["Auth", "auth", "BugFix"],
            "source": "verify-change",
        }

        self.run_script("buglog.py", "--append-json", json.dumps(payload))
        result = self.run_script(
            "buglog.py",
            "--search",
            "token",
            "--path",
            "src/app.py",
            "--json",
        )

        parsed = json.loads(result.stdout)
        match = parsed["matches"][0]["entry"]

        self.assertEqual(parsed["state"]["status"], "healthy")
        self.assertEqual(parsed["state"]["entry_count"], 1)
        self.assertEqual(match["file"], "src/app.py")
        self.assertEqual(match["tags"], ["auth", "bugfix"])
        self.assertEqual(match["source"], "verify-change")

    def test_missing_buglog_is_non_blocking_for_analysis(self) -> None:
        paths = workflow_state.ensure_state_files(self.repo)
        paths["buglog"].unlink()

        state = workflow_state.inspect_workflow_state(self.repo)
        result = analyze_change_scope.analyze_change_scope(self.repo)

        self.assertEqual(state["buglog"]["status"], "missing")
        self.assertEqual(result["buglog_status"], "missing")
        self.assertFalse(result["state_repair_needed"])

    def test_state_doctor_repairs_invalid_buglog_and_reports_data_loss(self) -> None:
        paths = workflow_state.ensure_state_files(self.repo)
        valid_entry = {
            "timestamp": workflow_state.now_timestamp(),
            "file": "src/app.py",
            "symptom": "App crashed on empty state.",
            "root_cause": "None handling was missing.",
            "fix": "Guard the empty state before rendering.",
            "tags": ["bugfix"],
            "source": "manual",
        }
        paths["buglog"].write_text(
            json.dumps(valid_entry, sort_keys=True) + "\n" + '{"bad": true}\n',
            encoding="utf-8",
        )

        before = state_doctor.build_check_report(self.repo)
        repair = state_doctor.repair_state(self.repo)
        after = state_doctor.build_check_report(self.repo)
        lines = paths["buglog"].read_text(encoding="utf-8").splitlines()

        self.assertEqual(before["files"]["buglog"]["status"], "invalid")
        self.assertIn("buglog", repair["repaired"])
        self.assertTrue(any(item.startswith("buglog: dropped 1 invalid line") for item in repair["data_loss"]))
        self.assertEqual(after["files"]["buglog"]["status"], "healthy")
        self.assertEqual(len(lines), 1)

    def test_state_doctor_repairs_invalid_reasoning_hotspot_log_and_reports_data_loss(self) -> None:
        paths = workflow_state.ensure_state_files(self.repo)
        valid_entry = {
            "timestamp": workflow_state.now_timestamp(),
            "kind": "risk-escalation",
            "summary": "Risk moved above the team default threshold after a multi-file edit.",
            "source": "post-tool-hook",
            "recommended_skills": ["verify-change"],
            "related_items": ["app.py", "worker.py"],
            "questions": ["What changed in my understanding since the initial plan?"],
        }
        paths["reasoning_hotspots"].write_text(
            json.dumps(valid_entry, sort_keys=True) + "\n" + '{"bad": true}\n',
            encoding="utf-8",
        )

        before = state_doctor.build_check_report(self.repo)
        repair = state_doctor.repair_state(self.repo)
        after = state_doctor.build_check_report(self.repo)
        lines = paths["reasoning_hotspots"].read_text(encoding="utf-8").splitlines()

        self.assertEqual(before["files"]["reasoning_hotspots"]["status"], "invalid")
        self.assertIn("reasoning_hotspots", repair["repaired"])
        self.assertTrue(
            any(
                item.startswith("reasoning_hotspots: dropped 1 invalid line")
                for item in repair["data_loss"]
            )
        )
        self.assertEqual(after["files"]["reasoning_hotspots"]["status"], "healthy")
        self.assertEqual(len(lines), 1)

    def test_hook_repeat_edit_reminder_appears_on_third_write_once_per_window(self) -> None:
        target = self.repo / "src" / "widget.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"tool_input": {"file_path": str(target)}})

        outputs = [
            self.run_script("analyze_change_scope.py", "--hook", input_text=payload).stdout.strip()
            for _ in range(3)
        ]
        fourth = self.run_script("analyze_change_scope.py", "--hook", input_text=payload).stdout.strip()
        hook_state_path = self.repo / ".codex-workflows" / "runtime" / "hook-state.json"

        self.assertEqual(outputs[0], "")
        self.assertEqual(outputs[1], "")
        self.assertIn("edited at least 3 times", outputs[2])
        self.assertIn("buglog entry", outputs[2])
        self.assertEqual(fourth, "")
        self.assertTrue(hook_state_path.exists())

    def test_existing_scope_and_risk_hook_reminders_still_render(self) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        for name in ("a.py", "b.py", "c.py"):
            (self.repo / name).write_text("print('changed')\n", encoding="utf-8")

        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "a.py")}})
        output = self.run_script("analyze_change_scope.py", "--hook", input_text=payload).stdout

        self.assertIn("Multi-file change detected", output)
        self.assertIn("run verify-change", output)

    def test_hotspot_micro_reasoning_is_reported_and_mentions_initial_plan(self) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        for name in ("a.py", "b.py", "c.py"):
            (self.repo / name).write_text("print('changed')\n", encoding="utf-8")

        result = analyze_change_scope.analyze_change_scope(self.repo)
        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "a.py")}})
        output = self.run_script("analyze_change_scope.py", "--hook", input_text=payload).stdout
        hotspot_kinds = {item["kind"] for item in result["reasoning_hotspots"]}

        self.assertTrue(result["micro_reasoning_recommended"])
        self.assertIn("plan-drift", hotspot_kinds)
        self.assertIn("risk-escalation", hotspot_kinds)
        self.assertIn("verification-gap", hotspot_kinds)
        self.assertIn("Hotspot-triggered micro reasoning is recommended now", output)
        self.assertIn("supplements, not replaces, the upfront plan", output)
        self.assertIn("What changed in my understanding since the initial plan?", output)

    def test_hook_persists_reasoning_hotspot_log_and_dedupes_recent_duplicates(self) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        for name in ("a.py", "b.py", "c.py"):
            (self.repo / name).write_text("print('changed')\n", encoding="utf-8")

        expected = analyze_change_scope.analyze_change_scope(self.repo)["reasoning_hotspots"]
        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "a.py")}})

        self.run_script("analyze_change_scope.py", "--hook", input_text=payload)
        self.run_script("analyze_change_scope.py", "--hook", input_text=payload)

        hotspot_state = workflow_state.get_reasoning_hotspot_state(self.repo)
        entries = workflow_state.load_reasoning_hotspot_entries(self.repo)["entries"]
        logged_pairs = {(entry["kind"], entry["summary"]) for entry in entries}
        expected_pairs = {(entry["kind"], entry["summary"]) for entry in expected}

        self.assertEqual(hotspot_state["status"], "healthy")
        self.assertEqual(hotspot_state["entry_count"], len(expected))
        self.assertEqual(logged_pairs, expected_pairs)

    def test_recurring_hotspots_escalate_recommendations_and_reports(self) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        for name in ("a.py", "b.py", "c.py"):
            (self.repo / name).write_text("print('changed')\n", encoding="utf-8")

        for _ in range(2):
            workflow_state.append_reasoning_hotspot_entry(
                self.repo,
                {
                    "kind": "plan-drift",
                    "summary": "Earlier plan drift required a reset.",
                    "source": "manual",
                },
            )
            workflow_state.append_reasoning_hotspot_entry(
                self.repo,
                {
                    "kind": "risk-escalation",
                    "summary": "Earlier risk escalation pushed verification later than it should have.",
                    "source": "manual",
                },
            )

        scope = analyze_change_scope.analyze_change_scope(self.repo)
        policy = policy_check.build_policy_review(self.repo, "general")
        branch = branch_readiness.summarize_branch(self.repo)
        review_report, metadata = report_builder.build_review_ready_report(self.repo)
        handoff_report, _ = report_builder.build_handoff_report(self.repo)
        recurring_kinds = {item["kind"] for item in scope["recurring_reasoning_hotspots"]}

        self.assertTrue(scope["micro_reasoning_escalation_recommended"])
        self.assertIn("plan-drift", recurring_kinds)
        self.assertIn("risk-escalation", recurring_kinds)
        self.assertIn("implementation-plan", scope["escalated_recommended_skills"])
        self.assertIn("agentic-code-review", scope["escalated_recommended_skills"])
        self.assertIn(
            "Recurring hotspot patterns suggest the current plan or verification workflow needs escalation before shipping.",
            branch["blockers"],
        )
        self.assertTrue(policy["micro_reasoning_escalation_recommended"])
        self.assertIn("implementation-plan", policy["recommended_skills"])
        self.assertIn("agentic-code-review", policy["recommended_skills"])
        self.assertIn("## Recurring Hotspots", review_report)
        self.assertIn("## Recent Hotspot History", review_report)
        self.assertIn("Earlier plan drift required a reset.", review_report)
        self.assertIn("Escalate recurring hotspot patterns with", "\n".join(metadata["next_actions"]))
        self.assertIn("## Recurring Hotspots", handoff_report)
        self.assertIn("Earlier risk escalation pushed verification later than it should have.", handoff_report)

    def test_handoff_report_includes_do_not_repeat_and_recent_decision_log(self) -> None:
        workflow_state.ensure_state_files(self.repo)
        workflow_state.append_memory_entry(
            self.repo,
            "Avoid hand-editing generated snapshot files.",
            section="Do-Not-Repeat",
        )
        for index in range(4):
            workflow_state.append_memory_entry(
                self.repo,
                f"Decision {index + 1}",
                section="Decision Log",
            )

        report, _ = report_builder.build_handoff_report(self.repo)

        self.assertIn("## Do-Not-Repeat", report)
        self.assertIn("- Avoid hand-editing generated snapshot files.", report)
        self.assertIn("## Recent Decision Log", report)
        self.assertNotIn("- Decision 1", report)
        self.assertIn("- Decision 2", report)
        self.assertIn("- Decision 3", report)
        self.assertIn("- Decision 4", report)

    def test_stream_coverage_gap_surfaces_in_readiness_and_reports(self) -> None:
        subprocess.run(
            ["git", "init"],
            cwd=self.repo,
            check=True,
            capture_output=True,
            text=True,
        )
        (self.repo / "app.py").write_text("print('changed')\n", encoding="utf-8")

        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Backend implementation",
            stream_id="backend",
            set_primary=True,
        )
        workflow_state.update_task_loop(
            self.repo,
            "- [ ] Active: Docs follow-up",
            stream_id="docs",
        )
        workflow_state.append_verification_entry(
            self.repo,
            {
                "timestamp": workflow_state.now_timestamp(),
                "scope": {"files": ["app.py"]},
                "checks": [],
                "verdict": "PASS",
                "stream_id": "backend",
            },
        )

        branch = branch_readiness.summarize_branch(self.repo)
        review_report, metadata = report_builder.build_review_ready_report(self.repo)
        handoff_report, _ = report_builder.build_handoff_report(self.repo)

        self.assertEqual(branch["task_loop_mode"], "streams")
        self.assertEqual(branch["task_stream_count"], 2)
        self.assertIn(
            "Task stream docs is missing current verification coverage.",
            branch["blockers"],
        )
        self.assertIn("- Task loop mode: streams", review_report)
        self.assertIn("- Task streams: 2", review_report)
        self.assertIn("  - docs: missing", review_report)
        self.assertIn(
            "Refresh verification so every open task stream has current coverage.",
            metadata["next_actions"],
        )
        self.assertIn(
            "- Task stream docs is missing current verification coverage.",
            handoff_report,
        )

    def test_memory_sync_and_reports_surface_reasoning_hotspot_state(self) -> None:
        workflow_state.ensure_state_files(self.repo)
        workflow_state.append_reasoning_hotspot_entry(
            self.repo,
            {
                "kind": "verification-gap",
                "summary": "Verification is still missing for the current change scope.",
                "source": "manual",
            },
        )

        summary = workflow_state.inspect_workflow_state(self.repo)
        memory_sync_text = memory_sync.render_text(summary)
        review_report, _ = report_builder.build_review_ready_report(self.repo)
        handoff_report, _ = report_builder.build_handoff_report(self.repo)

        self.assertIn("Reasoning hotspots: healthy (1 entries)", memory_sync_text)
        self.assertIn("- Reasoning hotspots: healthy (1)", review_report)
        self.assertIn("- Reasoning hotspots: healthy (1)", handoff_report)


if __name__ == "__main__":
    unittest.main()
