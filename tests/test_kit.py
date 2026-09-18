from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


PACKAGE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE / "scripts"))

from kit import (  # noqa: E402
    KitError,
    create_vault,
    load_scaffold,
    load_tasks,
    parse_answers,
    render_task_reference,
    validate_bootstrap,
    validate_reference,
)


class KitIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="second-brain-tests-")
        self.workspace = Path(self.temporary.name).resolve()
        self.root = self.workspace / "vault with spaces"
        self.answers_path = self.workspace / "answers.json"
        self.scaffold = load_scaffold()
        self.tasks = load_tasks()
        self.write_answers()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def fixture_answers(self) -> dict[str, object]:
        data = json.loads(
            (PACKAGE / "fixtures" / "sample-answers.json").read_text(encoding="utf-8")
        )
        data["workspace"]["destination"] = str(self.root)
        data["confirmed"] = True
        return data

    def write_answers(self, transform=None) -> None:
        data = self.fixture_answers()
        if transform is not None:
            transform(data)
        self.answers_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def cli(self, script: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PACKAGE / "scripts" / script), *arguments],
            check=False,
            text=True,
            capture_output=True,
        )

    def bootstrap_cli(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "bootstrap.py",
            "--answers",
            str(self.answers_path),
            "--root",
            str(self.root),
            *extra,
        )

    def test_successful_generated_skeleton_and_validation(self) -> None:
        completed = self.bootstrap_cli("--json")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        directories = [path for path in self.root.rglob("*") if path.is_dir()]
        files = [path for path in self.root.rglob("*") if path.is_file()]
        self.assertEqual(len(directories), 17)
        self.assertEqual(len(files), 20)
        validation = self.cli("validate.py", "--root", str(self.root), "--json")
        self.assertEqual(validation.returncode, 0, validation.stdout)
        self.assertEqual(json.loads(validation.stdout)["mode"], "bootstrap-only")

    def test_second_run_is_rejected_without_changes(self) -> None:
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        before = self.tree_digest()
        second = self.bootstrap_cli()
        self.assertEqual(second.returncode, 3)
        self.assertIn("nonempty_root", second.stderr)
        self.assertEqual(before, self.tree_digest())

    def test_visible_and_hidden_nonempty_roots_are_rejected(self) -> None:
        for name in ("visible.txt", ".hidden"):
            with self.subTest(name=name):
                root = self.workspace / name.replace(".", "-")
                root.mkdir()
                (root / name).write_text("preserve", encoding="utf-8")
                self.root = root
                self.write_answers()
                before = self.tree_digest()
                completed = self.bootstrap_cli()
                self.assertEqual(completed.returncode, 3)
                self.assertIn("nonempty_root", completed.stderr)
                self.assertEqual(before, self.tree_digest())

    @unittest.skipUnless(os.name == "nt", "Windows hidden attributes are Windows-specific")
    def test_windows_hidden_attribute_root_entry_is_rejected(self) -> None:
        import ctypes

        self.root.mkdir()
        hidden = self.root / "hidden-by-attribute.txt"
        hidden.write_text("preserve", encoding="utf-8")
        hidden_attribute = 0x2
        if not ctypes.windll.kernel32.SetFileAttributesW(str(hidden), hidden_attribute):
            self.skipTest("could not set Windows hidden attribute")
        self.write_answers()
        completed = self.bootstrap_cli()
        self.assertEqual(completed.returncode, 3)
        self.assertIn("nonempty_root", completed.stderr)
        self.assertEqual(hidden.read_text(encoding="utf-8"), "preserve")

    @unittest.skipUnless(os.name != "nt", "Unix dotfile behavior is Unix-specific")
    def test_unix_dotfile_root_entry_is_rejected(self) -> None:
        self.root.mkdir()
        hidden = self.root / ".hidden"
        hidden.write_text("preserve", encoding="utf-8")
        self.write_answers()
        completed = self.bootstrap_cli()
        self.assertEqual(completed.returncode, 3)
        self.assertIn("nonempty_root", completed.stderr)

    def test_invalid_answers_are_rejected_before_writes(self) -> None:
        cases = [
            lambda data: data.update({"unknown": True}),
            lambda data: data.update({"confirmed": False}),
            lambda data: data.update({"external_actions": "allowed"}),
            lambda data: data["sources"][0].update({"capability_status": "magical"}),
            lambda data: data.update({"primary_purpose": "bad\n# injected"}),
        ]
        for number, transform in enumerate(cases):
            with self.subTest(case=number):
                self.root = self.workspace / f"invalid-{number}"
                self.write_answers(transform)
                completed = self.bootstrap_cli()
                self.assertEqual(completed.returncode, 3)
                self.assertFalse(self.root.exists())

    def test_unsafe_manifest_paths_are_rejected_before_writes(self) -> None:
        base = json.loads((PACKAGE / "manifests" / "scaffold.json").read_text(encoding="utf-8"))
        for number, unsafe in enumerate(("../escape", str(self.workspace / "absolute"))):
            with self.subTest(path=unsafe):
                manifest = self.workspace / f"manifest-{number}.json"
                changed = json.loads(json.dumps(base))
                changed["directories"][0] = unsafe
                manifest.write_text(json.dumps(changed), encoding="utf-8")
                self.root = self.workspace / f"unsafe-{number}"
                self.write_answers()
                completed = self.bootstrap_cli("--manifest", str(manifest))
                self.assertEqual(completed.returncode, 3)
                self.assertIn("unsafe_manifest_path", completed.stderr)
                self.assertFalse(self.root.exists())

    def test_template_dates_and_project_null_state(self) -> None:
        before = datetime.now(timezone.utc).date()
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        after = datetime.now(timezone.utc).date()
        moc = (self.root / "_meta" / "MOCs" / "knowledge-map.md").read_text(encoding="utf-8")
        project = (self.root / "_meta" / "templates" / "project.md").read_text(encoding="utf-8")
        actual_date = next(
            line.removeprefix('created: "').removesuffix('"')
            for line in moc.splitlines()
            if line.startswith("created: ")
        )
        self.assertIn(datetime.fromisoformat(actual_date).date(), {before, after})
        self.assertNotEqual(actual_date, "2026-01-01")
        self.assertIn('target_state: "unknown"', project)
        self.assertIn("target_date: null", project)

    def test_partial_failure_preserves_created_and_existing_bytes(self) -> None:
        answers = parse_answers(self.answers_path)
        writes = 0

        def failing_writer(path: Path, content: bytes) -> None:
            nonlocal writes
            if writes == 2:
                raise OSError("synthetic write failure")
            with path.open("xb") as stream:
                stream.write(content)
            writes += 1

        with self.assertRaisesRegex(KitError, "synthetic write failure"):
            create_vault(
                self.root,
                answers,
                self.scaffold,
                self.tasks,
                writer=failing_writer,
            )
        first = self.root / "AGENTS.md"
        second = self.root / "README.md"
        self.assertTrue(first.read_bytes())
        saved = second.read_bytes()
        with self.assertRaises(KitError):
            create_vault(self.root, answers, self.scaffold, self.tasks)
        self.assertEqual(saved, second.read_bytes())

    def test_planned_source_location_is_inert(self) -> None:
        marker = self.workspace / "must-not-be-read.txt"
        self.write_answers(lambda data: data["sources"][0].update(
            {"account_or_location": str(marker)}
        ))
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        self.assertFalse(marker.exists())
        scope = (self.root / "_meta" / "scope.md").read_text(encoding="utf-8")
        self.assertIn(marker.name, scope)
        self.assertIn("&#92;", scope)

    def test_validator_detects_malformed_output(self) -> None:
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        (self.root / "_meta" / "templates" / "concept.md").write_text(
            "---\nkind:\n---\n# Broken\n", encoding="utf-8"
        )
        (self.root / "unexpected.md").write_text("extra", encoding="utf-8")
        issues = validate_bootstrap(self.root, self.scaffold, self.tasks)
        codes = {issue.code for issue in issues}
        self.assertIn("frontmatter", codes)
        self.assertIn("extra_file", codes)

    def test_validator_detects_completed_task_with_incomplete_prerequisite(self) -> None:
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        checklist = self.root / "_meta" / "setup-checklist.md"
        text = checklist.read_text(encoding="utf-8")
        s06_line = next(line for line in text.splitlines() if line.startswith("| S06 |"))
        changed = s06_line.replace("| not_started |", "| complete |")
        checklist.write_text(text.replace(s06_line, changed), encoding="utf-8")
        issues = validate_bootstrap(self.root, self.scaffold, self.tasks)
        self.assertTrue(any(issue.code == "incomplete_prerequisite" for issue in issues))

    def test_mismatched_canonical_reference_is_detected(self) -> None:
        reference = self.workspace / "reference.md"
        reference.write_text(render_task_reference(self.tasks) + "changed\n", encoding="utf-8")
        issues = validate_reference(self.tasks, reference)
        self.assertEqual([issue.code for issue in issues], ["reference_mismatch"])

    def test_validator_and_bootstrap_rejection_leave_inputs_unchanged(self) -> None:
        answers_before = self.answers_path.read_bytes()
        self.assertEqual(self.bootstrap_cli().returncode, 0)
        before = self.tree_digest()
        validation = self.cli("validate.py", "--root", str(self.root), "--check-reference")
        self.assertEqual(validation.returncode, 0, validation.stdout)
        self.assertEqual(before, self.tree_digest())
        self.assertEqual(answers_before, self.answers_path.read_bytes())

    def test_file_root_and_missing_parent_chain_are_rejected(self) -> None:
        self.root = self.workspace / "file-root"
        self.root.write_text("preserve", encoding="utf-8")
        self.write_answers()
        self.assertEqual(self.bootstrap_cli().returncode, 3)
        self.assertEqual(self.root.read_text(encoding="utf-8"), "preserve")

        self.root = self.workspace / "missing-parent" / "vault"
        self.write_answers()
        self.assertEqual(self.bootstrap_cli().returncode, 3)
        self.assertFalse(self.root.parent.exists())

    def test_link_root_is_rejected_when_supported(self) -> None:
        real = self.workspace / "real"
        real.mkdir()
        link = self.workspace / "linked-root"
        try:
            link.symlink_to(real, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is not permitted on this operating system")
        self.root = link
        self.write_answers()
        completed = self.bootstrap_cli()
        self.assertEqual(completed.returncode, 3)
        self.assertIn("unsafe_root", completed.stderr)
        self.assertEqual(list(real.iterdir()), [])

    @unittest.skipUnless(os.name == "nt", "junctions are Windows-specific")
    def test_windows_junction_detection_contract(self) -> None:
        self.skipTest("Python stdlib cannot safely create a directory junction; reparse-point detection is exercised by Windows symlink tests")

    def test_non_ascii_crlf_and_trailing_separator_inputs(self) -> None:
        self.root = self.workspace / "café vault"
        data = self.fixture_answers()
        data["workspace"]["destination"] = str(self.root) + os.sep
        data["primary_purpose"] = "Organize café research"
        self.answers_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\r\n",
        )
        completed = self.cli(
            "bootstrap.py",
            "--answers",
            str(self.answers_path),
            "--root",
            str(self.root) + os.sep,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertFalse(validate_bootstrap(self.root, self.scaffold, self.tasks))

    def tree_digest(self) -> str:
        digest = hashlib.sha256()
        if not self.root.exists():
            return digest.hexdigest()
        for path in sorted(self.root.rglob("*"), key=lambda value: str(value)):
            digest.update(str(path.relative_to(self.root)).encode())
            if path.is_file():
                digest.update(path.read_bytes())
        return digest.hexdigest()


class CommandTests(unittest.TestCase):
    def test_help_commands(self) -> None:
        for relative in ("scripts/bootstrap.py", "scripts/validate.py", "tests/harness.py"):
            with self.subTest(command=relative):
                completed = subprocess.run(
                    [sys.executable, str(PACKAGE / relative), "--help"],
                    check=False,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("usage:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
