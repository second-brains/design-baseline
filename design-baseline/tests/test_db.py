import json
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "db.py"


class DbFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, "DB_ROOT": str(self.root)}
        self.run_db("init")
        self.manifest = self.root / "design/manifest.json"
        data = json.loads(self.manifest.read_text())
        data["coverage"] = [{"up": "REQ", "down": "story", "min": 1}]
        data["checks"] = []
        self.manifest.write_text(json.dumps(data))
        (self.root / "design/facts/requirements.yaml").write_text("REQ-001: {summary: Fixture requirement}\n")
        story = self.root / "design/docs/stories/US-000.md"
        story.write_text("---\nid: US-000\ntype: story\nbased_on: [REQ-001@v1]\nuses_facts: []\nomits: []\n---\nFixture story\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_db(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                                env=self.env, text=True, capture_output=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def write_cp(self, cid, patches):
        p = self.root / "design/proposals" / f"{cid}.yaml"
        p.write_text("id: " + cid + "\nroot_cause: test fixture\npatches:\n" + patches)
        return p

    def approve(self, cid):
        result = self.run_db("cp", "hash", "--id", cid)
        raw = result.stdout.split("\n\n#", 1)[0]
        approval = json.loads(raw)
        approval.update(approved_by="tester", approved_at="2026-01-01T00:00:00")
        path = self.root / "design/proposals" / f"{cid}.approval.json"
        path.write_text(json.dumps(approval))
        return path

    def set_checks(self, checks):
        data = json.loads(self.manifest.read_text())
        data["checks"] = checks
        self.manifest.write_text(json.dumps(data))

    def propagate_fixture(self):
        self.run_db("propagate")
        self.run_db("verdict", "--up", "REQ-001", "--down", "US-000", "--verdict", "consistent",
                    "--evidence", "fixture relationship reviewed")

    def test_create_payload_mutation_after_approval_is_rejected(self):
        source = self.root / "payload.md"
        source.write_text("approved bytes\n")
        self.write_cp("CP-1001", "  - path: docs/story.md\n    action: create\n    content: payload.md\n")
        approval = self.approve("CP-1001")
        source.write_text("changed bytes\n")
        result = self.run_db("cp", "apply", "--id", "CP-1001", "--approval", str(approval), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("payload", result.stderr)
        self.assertFalse((self.root / "design/docs/story.md").exists())

    def test_edit_target_mutation_after_approval_is_rejected(self):
        target = self.root / "design/facts/requirements.yaml"
        target.write_text("REQ-001: {summary: before}\n")
        self.write_cp("CP-1002", "  - path: facts/requirements.yaml\n    action: edit\n    edits:\n      - find: before\n        replace: after\n")
        approval = self.approve("CP-1002")
        target.write_text("REQ-001: {summary: changed}\n")
        result = self.run_db("cp", "apply", "--id", "CP-1002", "--approval", str(approval), ok=False)
        self.assertNotEqual(result.returncode, 0)

    def test_removed_old_edge_is_written_to_pending(self):
        facts = self.root / "design/facts/requirements.yaml"
        facts.write_text("REQ-001: {summary: Need}\n")
        doc = self.root / "design/docs/stories/story.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text("---\nid: US-001\ntype: story\nbased_on: [REQ-001@v1]\nuses_facts: []\nomits: []\n---\nStory\n")
        self.run_db("propagate")
        doc.write_text("---\nid: US-001\ntype: story\nbased_on: []\nuses_facts: []\nomits: []\n---\nStory\n")
        self.run_db("propagate")
        pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-001"', pending)
        self.assertIn('"down": "US-001"', pending)

    def test_added_edge_opens_pair_for_l2_review(self):
        self.run_db("propagate")
        facts = self.root / "design/facts/requirements.yaml"
        facts.write_text("REQ-001: {summary: Fixture requirement}\nREQ-002: {summary: Added requirement}\n")
        story = self.root / "design/docs/stories/US-000.md"
        story.write_text("---\nid: US-000\ntype: story\nbased_on: [REQ-001@v1, REQ-002@v1]\nuses_facts: []\nomits: []\n---\nFixture story\n")
        self.run_db("propagate")
        pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-002"', pending)
        self.assertIn('"down": "US-000"', pending)

    def test_legacy_graph_survives_gate_rebuild_before_propagate(self):
        graph = self.root / "design/trace/graph.jsonl"
        graph.write_text(json.dumps({"up": "REQ-001", "down": "US-000", "kind": "based_on", "pin": 1}) + "\n")
        doc = self.root / "design/docs/stories/US-000.md"
        doc.write_text("---\nid: US-000\ntype: story\nbased_on: []\nuses_facts: []\nomits: []\n---\nFixture story\n")
        self.run_db("gate", ok=False)
        self.run_db("propagate")
        pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-001"', pending)
        self.assertIn('"down": "US-000"', pending)
        self.run_db("verdict", "--up", "REQ-001", "--down", "US-000", "--verdict", "consistent",
                    "--evidence", "deleted relation reviewed")
        self.assertNotEqual(self.run_db("gate", ok=False).returncode, 0)

    def test_empty_legacy_graph_is_known_and_new_edges_open_suspects(self):
        (self.root / "design/trace/graph.jsonl").write_text("")
        self.run_db("gate", ok=False)
        self.run_db("propagate")
        pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-001"', pending)
        self.assertIn('"down": "US-000"', pending)

    def test_graph_before_first_propagate_keeps_fresh_edges_pending(self):
        self.run_db("graph")
        self.run_db("propagate")
        pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-001"', pending)
        self.assertIn('"down": "US-000"', pending)

    def test_gate_rejects_artifact_version_not_yet_propagated(self):
        result = self.run_db("gate", ok=False)
        self.assertIn("尚未传播到当前版本", result.stdout + result.stderr)

    def test_gate_requires_l2_for_first_graph_then_passes_after_verdict(self):
        self.run_db("graph")
        self.run_db("propagate")
        result = self.run_db("gate", ok=False)
        self.assertIn("未核销疑点", result.stdout + result.stderr)
        self.run_db("verdict", "--up", "REQ-001", "--down", "US-000", "--verdict", "consistent",
                    "--evidence", "first graph relation reviewed")
        self.assertEqual(self.run_db("gate").returncode, 0)

    def test_coverage_counts_distinct_downstream_nodes_and_rejects_empty_scope(self):
        data = json.loads(self.manifest.read_text())
        data["coverage"] = [{"up": "REQ", "down": "story", "min": 2}]
        self.manifest.write_text(json.dumps(data))
        story = self.root / "design/docs/stories/US-000.md"
        story.write_text("---\nid: US-000\ntype: story\nbased_on: [REQ-001@v1, REQ-001@v1]\nuses_facts: []\nomits: []\n---\nFixture story\n")
        self.run_db("propagate")
        gate = self.run_db("gate", ok=False)
        self.assertIn("1/2", gate.stdout + gate.stderr)
        data["coverage"] = [{"up": "REQ", "down": "story", "min": 0}]
        self.manifest.write_text(json.dumps(data))
        self.assertIn("min 必须是正整数", self.run_db("gate", ok=False).stdout)
        data["coverage"] = []
        self.manifest.write_text(json.dumps(data))
        self.assertNotEqual(self.run_db("gate", ok=False).returncode, 0)

    def test_enforce_unknown_blocks_but_observe_unknown_does_not(self):
        self.run_db("check", "add", "--id", "GLOBAL-001", "--description", "Global rule",
                    "--manual")
        self.propagate_fixture()
        result = self.run_db("gate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("unknown", result.stdout + result.stderr)
        self.run_db("verify", "manual", "--check", "GLOBAL-001", "--result", "fail",
                    "--scenario", "regression fixture", "--evidence", "regression sample failed")
        result = self.run_db("gate")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("WARN", result.stdout + result.stderr)
        self.assertNotIn("PASS  检查 GLOBAL-001", result.stdout + result.stderr)
        self.set_checks([{"id": "GLOBAL-001", "mode": "enforce"}])
        result = self.run_db("gate", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown", result.stdout + result.stderr)

    def test_verification_is_invalidated_by_input_baseline_checker_or_environment(self):
        self.run_db("check", "add", "--id", "GLOBAL-002", "--description", "Global rule",
                    "--manual")
        self.propagate_fixture()
        evidence = self.root / "evidence.txt"
        evidence.write_text("normal sample passed\n")
        args = ("verify", "manual", "--check", "GLOBAL-002", "--result", "pass",
                "--scenario", "normal", "--input", "evidence.txt", "--evidence", "evidence.txt:1",
                "--evidence-file", "evidence.txt")
        self.run_db(*args)
        self.assertEqual(self.run_db("gate").returncode, 0)

        evidence.write_text("input changed\n")
        result = self.run_db("gate", ok=False)
        self.assertIn("输入已变化", result.stdout + result.stderr)

        evidence.write_text("normal sample passed\n")
        self.run_db(*args)
        data = json.loads(self.manifest.read_text())
        data["artifacts"]["facts/requirements.yaml"] = {"current": 1, "versions": {"1": {"hash": "changed"}}}
        self.manifest.write_text(json.dumps(data))
        result = self.run_db("gate", ok=False)
        self.assertIn("基线已变化", result.stdout + result.stderr)

        data = json.loads(self.manifest.read_text())
        data["artifacts"] = {}
        self.manifest.write_text(json.dumps(data))
        self.run_db(*args)
        ledger = [json.loads(line) for line in (self.root / "design/trace/ledger.jsonl").read_text().splitlines()]
        record = next(row for row in reversed(ledger) if row["kind"] == "verification")
        spec = importlib.util.spec_from_file_location("design_baseline_db", SCRIPT)
        db = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(db)
        with patch.object(db, "ROOT", self.root), patch.object(db, "checker_fingerprint", return_value="changed-checker"):
            self.assertEqual(db.check_freshness({}, record, data)[0], "unknown")
        with patch.object(db, "ROOT", self.root), patch.object(db, "verification_environment", return_value={"python": "changed", "platform": "changed"}):
            self.assertEqual(db.check_freshness({}, record, data)[0], "unknown")

    def test_approved_create_propagate_gate_happy_path(self):
        source = self.root / "story.md"
        source.write_text("---\nid: US-001\ntype: story\nbased_on: [REQ-001@v1]\nuses_facts: []\nomits: []\n---\nA normal story.\n")
        self.write_cp("CP-1003", "  - path: docs/stories/US-001.md\n    action: create\n    content: story.md\n")
        self.run_db("cp", "validate", "--id", "CP-1003")
        approval = self.approve("CP-1003")
        self.run_db("cp", "apply", "--id", "CP-1003", "--approval", str(approval))
        self.run_db("propagate")
        initial_pending = (self.root / "design/trace/pending.jsonl").read_text()
        self.assertIn('"up": "REQ-001"', initial_pending)
        self.run_db("verdict", "--up", "REQ-001", "--down", "US-001", "--verdict", "consistent",
                    "--evidence", "normal story cites the requirement")
        self.run_db("verdict", "--up", "REQ-001", "--down", "US-000", "--verdict", "consistent",
                    "--evidence", "fixture relationship reviewed")
        evidence = self.root / "normal-check.py"
        evidence.write_text("print('normal sample passed')\n")
        self.run_db("check", "add", "--id", "GLOBAL-003", "--description", "Run normal sample",
                    "--command", sys.executable, "normal-check.py", "--input", "normal-check.py",
                    "--method", "e2e")
        manual = self.run_db("verify", "manual", "--check", "GLOBAL-003", "--result", "pass",
                             "--scenario", "normal", "--evidence", "fabricated", ok=False)
        self.assertNotEqual(manual.returncode, 0)
        self.run_db("verify", "run", "--check", "GLOBAL-003")
        self.run_db("check", "enforce", "--id", "GLOBAL-003")
        self.assertNotEqual(self.run_db("gate", ok=False).returncode, 0)
        self.run_db("verify", "run", "--check", "GLOBAL-003")
        result = self.run_db("gate")
        self.assertIn("门禁：未核销疑点 0", result.stdout)
        log = self.root / "design/verification/logs/GLOBAL-003.log"
        log.write_text("altered\n")
        self.assertIn("日志已变化", self.run_db("gate", ok=False).stdout)
        self.run_db("verify", "run", "--check", "GLOBAL-003")

    def test_runner_timeout_restores_policy_and_records_tamper(self):
        script = self.root / "timeout-check.py"
        script.write_text("from pathlib import Path\nimport time\nPath('design/manifest.json').write_text('{}')\ntime.sleep(3)\n")
        self.run_db("check", "add", "--id", "GLOBAL-005", "--description", "Timeout fixture",
                    "--command", sys.executable, "timeout-check.py", "--input", "timeout-check.py",
                    "--timeout", "1", "--method", "e2e")
        result = self.run_db("verify", "run", "--check", "GLOBAL-005", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(json.loads(self.manifest.read_text())["checks"][0]["id"], "GLOBAL-005")
        self.assertNotEqual(self.run_db("gate", ok=False).returncode, 0)

    def test_installed_global_coverage_checker_has_bug_and_normal_samples(self):
        run = subprocess.run([sys.executable, "design/tools/global_coverage.py"], cwd=self.root,
                             env=self.env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        story = self.root / "design/docs/stories/US-000.md"
        story.write_text("---\nid: US-000\ntype: story\nbased_on: []\nuses_facts: []\nomits: []\n---\nFixture story\n")
        run = subprocess.run([sys.executable, "design/tools/global_coverage.py"], cwd=self.root,
                             env=self.env, text=True, capture_output=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertIn("REQ-001", run.stderr)

    def test_runner_restores_self_modified_policy_and_requires_audit_resolution(self):
        script = self.root / "mutate-policy.py"
        script.write_text("from pathlib import Path\nPath('design/manifest.json').write_text('{}')\n")
        self.run_db("check", "add", "--id", "GLOBAL-004", "--description", "No policy mutation",
                    "--command", sys.executable, "mutate-policy.py", "--input", "mutate-policy.py",
                    "--method", "e2e")
        result = self.run_db("verify", "run", "--check", "GLOBAL-004", ok=False)
        self.assertNotEqual(result.returncode, 0)
        restored = json.loads(self.manifest.read_text())
        self.assertEqual(restored["checks"][0]["id"], "GLOBAL-004")
        self.assertNotEqual(self.run_db("gate", ok=False).returncode, 0)
        self.run_db("check", "resolve", "--id", "GLOBAL-004", "--reason", "Reviewed attempted policy mutation")
        self.propagate_fixture()
        self.assertEqual(self.run_db("gate").returncode, 0)


if __name__ == "__main__":
    unittest.main()
