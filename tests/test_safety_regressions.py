from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


PACKAGE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PACKAGE / "scripts"))

import kit  # noqa: E402
from kit import (  # noqa: E402
    KitError,
    ValidationIssue,
    create_vault,
    load_scaffold,
    load_tasks,
    parse_answers,
    validate_bootstrap,
)


class SafetyRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="second-brain-safety-")
        self.workspace = Path(self.temporary.name).resolve()
        self.root = self.workspace / "vault"
        self.answers_path = self.workspace / "answers.json"
        self.scaffold = load_scaffold()
        self.tasks = load_tasks()
        self.write_answers()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def answer_data(self) -> dict[str, object]:
        data = json.loads(
            (PACKAGE / "fixtures" / "sample-answers.json").read_text(encoding="utf-8")
        )
        data["workspace"]["destination"] = str(self.root)
        data["confirmed"] = True
        return data

    def write_answers(self, transform=None) -> None:
        data = self.answer_data()
        if transform:
            transform(data)
        self.answers_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def bootstrap(self) -> None:
        create_vault(
            self.root,
            parse_answers(self.answers_path),
            self.scaffold,
            self.tasks,
        )

    def manifest_copy(self) -> dict[str, object]:
        return json.loads(
            (PACKAGE / "manifests" / "scaffold.json").read_text(encoding="utf-8")
        )

    def write_manifest(self, data: dict[str, object], name: str = "manifest.json") -> Path:
        path = self.workspace / name
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_noncanonical_and_windows_unsafe_manifest_paths_fail_before_root(self) -> None:
        unsafe_values = (
            ".",
            "C:escape",
            "x:y",
            "a\\b",
            "/absolute",
            "../escape",
            "a//b",
            "a/./b",
            "CON",
            "name.",
            "name ",
            " raw",
        )
        for index, unsafe in enumerate(unsafe_values):
            with self.subTest(path=unsafe):
                manifest = self.manifest_copy()
                manifest["directories"][0] = unsafe
                with self.assertRaises(KitError):
                    load_scaffold(self.write_manifest(manifest, f"unsafe-{index}.json"))
                self.assertFalse(self.root.exists())

    def test_casefold_collisions_and_ancestor_contracts_are_rejected(self) -> None:
        mutations = []

        def casefold_directory(data):
            data["directories"][1] = "RAW"

        def casefold_file(data):
            data["files"][-1]["path"] = "agents.md"

        def child_before_parent(data):
            data["directories"][0], data["directories"][1] = (
                data["directories"][1],
                data["directories"][0],
            )

        def missing_file_parent(data):
            data["files"][-1]["path"] = "undeclared/file.md"

        mutations.extend((casefold_directory, casefold_file, child_before_parent, missing_file_parent))
        for index, mutation in enumerate(mutations):
            with self.subTest(case=index):
                manifest = self.manifest_copy()
                mutation(manifest)
                with self.assertRaises(KitError):
                    load_scaffold(self.write_manifest(manifest, f"shape-{index}.json"))
                self.assertFalse(self.root.exists())

    def test_asset_safety_failure_prevents_asset_read(self) -> None:
        original = Path.read_text

        def guarded(path: Path, *args, **kwargs):
            if str(path).startswith(str(kit.ASSET_ROOT)):
                raise AssertionError("asset bytes must not be read after a failed component check")
            return original(path, *args, **kwargs)

        with patch.object(kit, "_safe_existing_descendant", return_value=False), patch.object(
            Path, "read_text", guarded
        ):
            with self.assertRaises(KitError):
                load_scaffold()
        self.assertFalse(self.root.exists())

    def test_duplicate_json_keys_and_noninteger_schema_versions_are_rejected(self) -> None:
        raw = self.answers_path.read_text(encoding="utf-8")
        duplicate = raw.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1)
        self.answers_path.write_text(duplicate, encoding="utf-8")
        with self.assertRaisesRegex(KitError, "duplicate JSON key"):
            parse_answers(self.answers_path)
        for value in (True, 1.0):
            self.write_answers(lambda data, value=value: data.update({"schema_version": value}))
            with self.assertRaises(KitError):
                parse_answers(self.answers_path)

    def test_windows_drive_relative_and_unc_answer_roots_are_rejected_cross_platform(self) -> None:
        for destination in ("C:escape", "\\\\server\\share\\vault", "//server/share/vault"):
            with self.subTest(destination=destination):
                self.write_answers(
                    lambda data, destination=destination:
                    data["workspace"].update({"destination": destination})
                )
                with self.assertRaises(KitError):
                    parse_answers(self.answers_path)

    def test_manifest_schema_versions_require_integer_one(self) -> None:
        scaffold = self.manifest_copy()
        scaffold["schema_version"] = True
        with self.assertRaises(KitError):
            load_scaffold(self.write_manifest(scaffold, "bool-scaffold.json"))
        tasks = json.loads(
            (PACKAGE / "manifests" / "tasks.json").read_text(encoding="utf-8")
        )
        tasks["schema_version"] = 1.0
        task_path = self.workspace / "float-tasks.json"
        task_path.write_text(json.dumps(tasks), encoding="utf-8")
        with self.assertRaises(KitError):
            load_tasks(task_path)

    def test_mutating_operations_and_unproved_capability_states_are_rejected(self) -> None:
        for field, value in (("allowed_operations", ["send"]), ("capability_status", "verified"), ("capability_status", "blocked")):
            with self.subTest(field=field, value=value):
                self.write_answers(lambda data, field=field, value=value: data["sources"][0].update({field: value}))
                with self.assertRaises(KitError):
                    parse_answers(self.answers_path)
                self.assertFalse(self.root.exists())
        self.write_answers(lambda data: data["sources"][0].update(
            {"allowed_operations": ["retain references only"]}
        ))
        with self.assertRaises(KitError):
            parse_answers(self.answers_path)

    def test_untrusted_markdown_is_inert_in_answer_table(self) -> None:
        payload = '`code` | <b>tag</b> [outside](../../escape)'
        self.write_answers(lambda data: data.update({"primary_purpose": payload}))
        self.bootstrap()
        text = (self.root / "_meta" / "setup-answers.md").read_text(encoding="utf-8")
        self.assertNotIn(payload, text)
        self.assertIn("&#96;", text)
        self.assertIn("&#124;", text)
        self.assertIn("&lt;b&gt;", text)
        self.assertIn("&#91;outside&#93;&#40;", text)
        self.assertEqual([line for line in text.splitlines() if line.startswith("#")], ["# Confirmed setup answers"])

    def test_creation_timestamp_differs_from_historical_approval_and_log_is_true(self) -> None:
        before = datetime.now(timezone.utc).date()
        self.bootstrap()
        after = datetime.now(timezone.utc).date()
        moc = (self.root / "_meta" / "MOCs" / "knowledge-map.md").read_text(encoding="utf-8")
        created = next(line.split('"')[1] for line in moc.splitlines() if line.startswith("created: "))
        self.assertIn(datetime.fromisoformat(created).date(), {before, after})
        self.assertNotEqual(created, "2026-01-01")
        log = (self.root / "log.md").read_text(encoding="utf-8")
        self.assertEqual(log.count("| bootstrap | complete |"), 1)
        self.assertIn("post-write validation passed", log)

    def test_failed_post_write_validation_leaves_s03_in_progress_and_empty_log(self) -> None:
        def fail_validation(root, scaffold, tasks, phase):
            return [ValidationIssue("synthetic", ".", "forced failure")]

        with self.assertRaises(KitError) as raised:
            create_vault(
                self.root,
                parse_answers(self.answers_path),
                self.scaffold,
                self.tasks,
                validator=fail_validation,
            )
        self.assertEqual(raised.exception.code, "post_write_validation")
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        log = (self.root / "log.md").read_text(encoding="utf-8")
        s03 = next(line for line in checklist.splitlines() if line.startswith("| S03 |"))
        self.assertIn("| in_progress |", s03)
        self.assertNotIn("post-write structural validation passed", checklist)
        self.assertNotIn("| bootstrap | complete |", log)

    def test_finalization_refuses_changed_created_file(self) -> None:
        calls = 0

        def mutate_after_pending(root, scaffold, tasks, phase):
            nonlocal calls
            calls += 1
            if phase == "pending":
                checklist = root / "_meta" / "setup-checklist.md"
                checklist.write_bytes(checklist.read_bytes() + b"\nexternal change\n")
            return []

        with self.assertRaises(KitError) as raised:
            create_vault(
                self.root,
                parse_answers(self.answers_path),
                self.scaffold,
                self.tasks,
                validator=mutate_after_pending,
            )
        self.assertEqual(raised.exception.code, "finalization_failed")
        self.assertEqual(calls, 1)
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        self.assertIn("| in_progress |", next(line for line in checklist.splitlines() if line.startswith("| S03 |")))
        self.assertNotIn("post-write structural validation passed", checklist)

    def test_recovery_branch_is_modeled(self) -> None:
        self.bootstrap()
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        self.assertIn("| S13 |", checklist)
        self.assertIn("| deferred |", next(line for line in checklist.splitlines() if line.startswith("| S13 |")))

        second = self.workspace / "second"
        self.root = second
        self.write_answers(lambda data: data["recovery"].update({"decision": "not_required"}))
        self.bootstrap()
        checklist = (second / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        self.assertIn("| not_applicable |", next(line for line in checklist.splitlines() if line.startswith("| S13 |")))

    def test_checklist_duplicate_extra_header_cells_and_empty_evidence_are_reported(self) -> None:
        self.bootstrap()
        checklist = self.root / "_meta" / "setup-checklist.md"
        original = checklist.read_text(encoding="utf-8")
        s01 = next(line for line in original.splitlines() if line.startswith("| S01 |"))
        s99 = s01.replace("| S01 |", "| S99 |", 1)
        malformed = original.replace(s01, s01 + "\n" + s01 + "\n" + s99)
        malformed = malformed.replace(
            "Confirmed purpose, useful first result, and review preference on 2026-01-01.",
            "None",
        )
        checklist.write_text(malformed, encoding="utf-8")
        codes = {issue.code for issue in validate_bootstrap(self.root, self.scaffold, self.tasks)}
        self.assertIn("checklist_task", codes)
        self.assertIn("checklist_evidence", codes)

        checklist.write_text(original.replace(
            "| ID | Setup task | Depends on | Completion evidence required | State | Evidence observed | Outstanding decisions | Next permitted action |",
            "| wrong header |",
        ), encoding="utf-8")
        codes = {issue.code for issue in validate_bootstrap(self.root, self.scaffold, self.tasks)}
        self.assertIn("checklist_header", codes)

        checklist.write_text(original.replace(s01, s01.removesuffix("|") + "| extra |"), encoding="utf-8")
        codes = {issue.code for issue in validate_bootstrap(self.root, self.scaffold, self.tasks)}
        self.assertIn("checklist_row", codes)

        checklist.write_text(original + "\n" + s01 + "\n", encoding="utf-8")
        codes = {issue.code for issue in validate_bootstrap(self.root, self.scaffold, self.tasks)}
        self.assertIn("checklist_row", codes)

    def test_final_validation_failure_reverts_completion_claims(self) -> None:
        def fail_final(root, scaffold, tasks, phase):
            if phase == "complete":
                return [ValidationIssue("synthetic", ".", "final failure")]
            return []

        with self.assertRaises(KitError) as raised:
            create_vault(
                self.root,
                parse_answers(self.answers_path),
                self.scaffold,
                self.tasks,
                validator=fail_final,
            )
        self.assertEqual(raised.exception.code, "post_write_validation")
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        log = (self.root / "log.md").read_text(encoding="utf-8")
        self.assertIn("| in_progress |", next(line for line in checklist.splitlines() if line.startswith("| S03 |")))
        self.assertNotIn("| bootstrap | complete |", log)

    def test_log_finalization_failure_reconciles_completed_checklist(self) -> None:
        original_replace = kit._replace_created_unchanged

        def fail_log(root, path, initial, replacement, created_paths, relative):
            if relative == "log.md":
                raise KitError("finalization_failed", "synthetic log finalization failure")
            return original_replace(
                root, path, initial, replacement, created_paths, relative
            )

        with patch.object(kit, "_replace_created_unchanged", side_effect=fail_log):
            with self.assertRaises(KitError) as raised:
                create_vault(
                    self.root,
                    parse_answers(self.answers_path),
                    self.scaffold,
                    self.tasks,
                )
        self.assertEqual(raised.exception.code, "finalization_failed")
        self.assertIn("_meta/setup-checklist.md=reverted_to_pending", raised.exception.message)
        self.assertIn("log.md=already_pending", raised.exception.message)
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        log = (self.root / "log.md").read_text(encoding="utf-8")
        self.assertIn("| in_progress |", next(line for line in checklist.splitlines() if line.startswith("| S03 |")))
        self.assertNotIn("| bootstrap | complete |", log)

    def test_validator_oserror_reconciles_both_final_records(self) -> None:
        def raise_on_final(root, scaffold, tasks, phase):
            if phase == "complete":
                raise OSError("synthetic validator I/O failure")
            return []

        with self.assertRaises(KitError) as raised:
            create_vault(
                self.root,
                parse_answers(self.answers_path),
                self.scaffold,
                self.tasks,
                validator=raise_on_final,
            )
        self.assertEqual(raised.exception.code, "post_write_validation")
        self.assertIn("_meta/setup-checklist.md=reverted_to_pending", raised.exception.message)
        self.assertIn("log.md=reverted_to_pending", raised.exception.message)
        checklist = (self.root / "_meta" / "setup-checklist.md").read_text(encoding="utf-8")
        log = (self.root / "log.md").read_text(encoding="utf-8")
        self.assertIn("| in_progress |", next(line for line in checklist.splitlines() if line.startswith("| S03 |")))
        self.assertNotIn("| bootstrap | complete |", log)

    def test_finalization_rejects_static_link_ancestor_before_read(self) -> None:
        root = self.workspace / "replace-root"
        outside = self.workspace / "replace-outside"
        root.mkdir()
        outside.mkdir()
        target = outside / "record.md"
        target.write_bytes(b"outside")
        linked = root / "linked"
        try:
            linked.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("directory symlink creation is not permitted")
        candidate = linked / "record.md"
        original_read = Path.read_bytes

        def guarded(path: Path):
            if path == candidate:
                raise AssertionError("unsafe linked candidate was read")
            return original_read(path)

        with patch.object(Path, "read_bytes", guarded):
            with self.assertRaises(KitError):
                kit._replace_created_unchanged(
                    root,
                    candidate,
                    b"outside",
                    b"replacement",
                    {"linked/record.md"},
                    "linked/record.md",
                )
        self.assertEqual(target.read_bytes(), b"outside")

    def test_frontmatter_duplicate_and_type_relationship_defects_are_reported(self) -> None:
        self.bootstrap()
        template = self.root / "_meta" / "templates" / "project.md"
        original = template.read_text(encoding="utf-8")
        variants = (
            original.replace('title: "<title>"', 'title: "<title>"\ntitle: "<title>"'),
            original.replace("tags: []", 'tags: ["Bad Tag"]'),
            original.replace("target_date: null", 'target_date: "2026-01-01"'),
            original.replace('status: "draft"', 'status: {"bad": true}'),
            original.replace('id: "project-<YYYYMMDD>-<slug>"', 'id: "p-<YYYYMMDD>-<slug>"'),
        )
        for index, variant in enumerate(variants):
            with self.subTest(case=index):
                template.write_text(variant, encoding="utf-8")
                codes = {issue.code for issue in validate_bootstrap(self.root, self.scaffold, self.tasks)}
                self.assertTrue({"frontmatter", "template_schema"} & codes)
                template.write_text(original, encoding="utf-8")

    def test_unsafe_links_are_rejected_without_reading_outside(self) -> None:
        self.bootstrap()
        outside = self.workspace / "outside.md"
        outside.write_text("do not read", encoding="utf-8")
        readme = self.root / "README.md"
        readme.write_text(
            readme.read_text(encoding="utf-8")
            + "\n[absolute](/outside.md)\n[backslash](..\\outside.md)\n"
            + "[encoded](%252e%252e/outside.md)\n[file](file:///outside.md)\n"
            + "[nul](safe%00name.md)\n",
            encoding="utf-8",
        )
        original = Path.read_text

        def guarded(path: Path, *args, **kwargs):
            if path == outside:
                raise AssertionError("validator read outside target")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            issues = validate_bootstrap(self.root, self.scaffold, self.tasks)
        self.assertGreaterEqual(sum(issue.code == "unsafe_link" for issue in issues), 5)

    def test_directory_symlink_and_ancestor_alias_are_not_followed(self) -> None:
        self.bootstrap()
        outside = self.workspace / "outside"
        outside.mkdir()
        (outside / "templates").mkdir()
        target = outside / "templates" / "project.md"
        target.write_text("do not read", encoding="utf-8")
        meta = self.root / "_meta"
        saved_meta = self.workspace / "saved-meta"
        meta.rename(saved_meta)
        try:
            meta.symlink_to(outside, target_is_directory=True)
        except OSError:
            saved_meta.rename(meta)
            self.skipTest("directory symlink creation is not permitted")
        original = Path.read_text

        def guarded(path: Path, *args, **kwargs):
            if str(path).startswith(str(outside)):
                raise AssertionError("validator followed directory link")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            issues = validate_bootstrap(self.root, self.scaffold, self.tasks)
        self.assertTrue(any(issue.code == "unsafe_link" for issue in issues))

        alias = self.workspace / "alias"
        try:
            alias.symlink_to(self.workspace, target_is_directory=True)
        except OSError:
            self.skipTest("ancestor symlink creation is not permitted")
        with patch.object(Path, "read_text", side_effect=AssertionError("root alias must fail before reads")):
            alias_issues = validate_bootstrap(alias / "vault", self.scaffold, self.tasks)
        self.assertEqual(alias_issues[0].code, "invalid_root")


if __name__ == "__main__":
    unittest.main()
