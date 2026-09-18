from __future__ import annotations

import json
import html
import os
import re
import stat
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import unquote


PACKAGE_ROOT = Path(os.path.abspath(__file__)).parent.parent
ASSET_ROOT = PACKAGE_ROOT / "assets" / "vault"
MANIFEST_ROOT = PACKAGE_ROOT / "manifests"
TASK_STATES = frozenset(
    {"not_started", "in_progress", "blocked", "complete", "deferred", "not_applicable"}
)
GENERATORS = frozenset({"scope", "answers", "checklist"})
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
TASK_ID_RE = re.compile(r"S\d{2}")
FRONTMATTER_LINE_RE = re.compile(r"^([a-z_]+): (.+)$")
WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL", *{f"COM{i}" for i in range(1, 10)}, *{f"LPT{i}" for i in range(1, 10)}}
)
SUPPORTED_OPERATIONS = frozenset(
    {
        "read supplied file",
        "read approved source",
        "search approved source",
        "retain a local evidence copy",
        "retain references only",
    }
)


class KitError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class CapabilityStatus(str, Enum):
    PLANNED = "planned"
    AVAILABLE_UNVERIFIED = "available_unverified"
    VERIFIED = "verified"
    BLOCKED = "blocked"


class RecoveryDecision(str, Enum):
    DEFERRED = "deferred"
    VERIFY_LATER = "verify_later"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True)
class SourcePlan:
    source_and_purpose: str
    account_or_location: str
    in_scope: str
    out_of_scope: str
    access_method: str
    allowed_operations: tuple[str, ...]
    capability_status: CapabilityStatus
    limits_and_retention: str


@dataclass(frozen=True)
class ConfirmedAnswers:
    primary_purpose: str
    useful_first_result: str
    review_preference: str
    destination: Path
    authorize_create_if_missing: bool
    authorize_local_setup: bool
    sources: tuple[SourcePlan, ...]
    exclusions: tuple[str, ...]
    external_actions: str
    recovery_decision: RecoveryDecision
    confirmed: bool
    confirmed_on: date


@dataclass(frozen=True)
class ScaffoldFile:
    path: PurePosixPath
    asset: PurePosixPath | None
    generated: str | None


@dataclass(frozen=True)
class ScaffoldManifest:
    directories: tuple[PurePosixPath, ...]
    files: tuple[ScaffoldFile, ...]


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    depends_on: tuple[str, ...]
    conditional_dependencies: tuple["ConditionalDependency", ...]
    completion_evidence: str


@dataclass(frozen=True)
class ConditionalDependency:
    task_id: str
    condition: str
    display: str


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    path: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise KitError("invalid_schema", f"{context} must be a JSON object with string keys")
    return value


def _keys(value: Mapping[str, object], expected: set[str], context: str) -> None:
    missing = sorted(expected - value.keys())
    unknown = sorted(value.keys() - expected)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing {missing}")
        if unknown:
            details.append(f"unknown {unknown}")
        raise KitError("invalid_schema", f"{context}: {', '.join(details)}")


def _text(value: object, context: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KitError("invalid_schema", f"{context} must be a non-empty string")
    if CONTROL_RE.search(value):
        raise KitError("invalid_schema", f"{context} must not contain control characters or newlines")
    return value.strip()


def _bool(value: object, context: str) -> bool:
    if not isinstance(value, bool):
        raise KitError("invalid_schema", f"{context} must be true or false")
    return value


def _string_list(value: object, context: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise KitError("invalid_schema", f"{context} must be a JSON array")
    result = tuple(_text(item, f"{context}[{index}]") for index, item in enumerate(value))
    if nonempty and not result:
        raise KitError("invalid_schema", f"{context} must contain at least one value")
    return result


def _reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise KitError("duplicate_key", f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path, context: str) -> object:
    try:
        return json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_pairs
        )
    except FileNotFoundError as exc:
        raise KitError("missing_input", f"{context} not found: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise KitError("invalid_json", f"cannot read {context} {path}: {exc}") from exc


def absolute_without_link_resolution(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _reject_nonlocal_root_text(value: str, context: str) -> None:
    windows = PureWindowsPath(value)
    if value.startswith(("\\\\", "//")):
        raise KitError("unsafe_root", f"{context} must not be a UNC/network path")
    if windows.drive and not windows.root:
        raise KitError("unsafe_root", f"{context} must not be Windows drive-relative")
    if os.name != "nt" and windows.drive:
        raise KitError("unsafe_root", f"{context} must use native absolute-path syntax")


def parse_answers(path: Path) -> ConfirmedAnswers:
    raw = _object(read_json(path, "answers file"), "answers")
    expected = {
        "schema_version", "primary_purpose", "useful_first_result", "review_preference",
        "workspace", "sources", "exclusions", "external_actions", "recovery",
        "confirmed", "confirmed_on",
    }
    _keys(raw, expected, "answers")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise KitError("unsupported_schema", "answers.schema_version must be 1")

    workspace = _object(raw["workspace"], "answers.workspace")
    _keys(
        workspace,
        {"destination", "authorize_create_if_missing", "authorize_local_setup"},
        "answers.workspace",
    )
    destination_text = _text(workspace["destination"], "answers.workspace.destination")
    _reject_nonlocal_root_text(destination_text, "answers.workspace.destination")
    destination = Path(destination_text).expanduser()
    if not destination.is_absolute():
        destination = absolute_without_link_resolution(path.parent / destination)
    else:
        destination = absolute_without_link_resolution(destination)

    source_values = raw["sources"]
    if not isinstance(source_values, list) or not source_values:
        raise KitError("invalid_schema", "answers.sources must contain at least one source plan")
    sources: list[SourcePlan] = []
    source_keys = {
        "source_and_purpose", "account_or_location", "in_scope", "out_of_scope",
        "access_method", "allowed_operations", "capability_status", "limits_and_retention",
    }
    for index, item in enumerate(source_values):
        source = _object(item, f"answers.sources[{index}]")
        _keys(source, source_keys, f"answers.sources[{index}]")
        try:
            capability = CapabilityStatus(_text(
                source["capability_status"], f"answers.sources[{index}].capability_status"
            ))
        except ValueError as exc:
            raise KitError(
                "invalid_schema",
                f"answers.sources[{index}].capability_status is unsupported",
            ) from exc
        if capability not in {CapabilityStatus.PLANNED, CapabilityStatus.AVAILABLE_UNVERIFIED}:
            raise KitError(
                "invalid_schema",
                "bootstrap source capability must be planned or available_unverified; S04 establishes verified/blocked evidence",
            )
        operations = _string_list(
            source["allowed_operations"], "allowed_operations", nonempty=True
        )
        unsupported_operations = sorted(set(operations) - SUPPORTED_OPERATIONS)
        if unsupported_operations:
            raise KitError(
                "conflicting_authorization",
                f"unsupported bootstrap operations: {unsupported_operations}",
            )
        if not set(operations) & {
            "read supplied file", "read approved source", "search approved source"
        }:
            raise KitError(
                "invalid_schema",
                "each source plan must include a supported bounded read or search operation",
            )
        sources.append(SourcePlan(
            source_and_purpose=_text(source["source_and_purpose"], "source_and_purpose"),
            account_or_location=_text(source["account_or_location"], "account_or_location"),
            in_scope=_text(source["in_scope"], "in_scope"),
            out_of_scope=_text(source["out_of_scope"], "out_of_scope"),
            access_method=_text(source["access_method"], "access_method"),
            allowed_operations=operations,
            capability_status=capability,
            limits_and_retention=_text(source["limits_and_retention"], "limits_and_retention"),
        ))

    external_actions = _text(raw["external_actions"], "answers.external_actions")
    if external_actions != "unauthorized":
        raise KitError(
            "conflicting_authorization",
            "bootstrap supports only external_actions='unauthorized'",
        )
    recovery_raw = _object(raw["recovery"], "answers.recovery")
    _keys(recovery_raw, {"decision"}, "answers.recovery")
    try:
        recovery = RecoveryDecision(_text(recovery_raw["decision"], "answers.recovery.decision"))
    except ValueError as exc:
        raise KitError("invalid_schema", "answers.recovery.decision is unsupported") from exc
    confirmed = _bool(raw["confirmed"], "answers.confirmed")
    if not confirmed:
        raise KitError("consent_required", "answers.confirmed must be true; a sample is not consent")
    try:
        confirmed_on = date.fromisoformat(_text(raw["confirmed_on"], "answers.confirmed_on"))
    except ValueError as exc:
        raise KitError("invalid_schema", "answers.confirmed_on must be YYYY-MM-DD") from exc
    local_setup = _bool(workspace["authorize_local_setup"], "authorize_local_setup")
    if not local_setup:
        raise KitError("consent_required", "authorize_local_setup must be true")
    return ConfirmedAnswers(
        primary_purpose=_text(raw["primary_purpose"], "answers.primary_purpose"),
        useful_first_result=_text(raw["useful_first_result"], "answers.useful_first_result"),
        review_preference=_text(raw["review_preference"], "answers.review_preference"),
        destination=destination,
        authorize_create_if_missing=_bool(
            workspace["authorize_create_if_missing"], "authorize_create_if_missing"
        ),
        authorize_local_setup=local_setup,
        sources=tuple(sources),
        exclusions=_string_list(raw["exclusions"], "answers.exclusions", nonempty=True),
        external_actions=external_actions,
        recovery_decision=recovery,
        confirmed=confirmed,
        confirmed_on=confirmed_on,
    )


def _safe_relative(raw: object, context: str) -> PurePosixPath:
    if not isinstance(raw, str) or raw != raw.strip():
        raise KitError("unsafe_manifest_path", f"{context} must not have surrounding whitespace")
    text = _text(raw, context)
    if "\\" in text or ":" in text or text.startswith("/"):
        raise KitError("unsafe_manifest_path", f"{context} contains unsafe path syntax: {text}")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or not path.parts
        or text == "."
        or text != str(path)
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.endswith((".", " ")) for part in path.parts)
        or any(part.split(".", 1)[0].upper() in WINDOWS_RESERVED for part in path.parts)
    ):
        raise KitError("unsafe_manifest_path", f"{context} must be a normalized relative path: {text}")
    return path


def load_scaffold(path: Path | None = None) -> ScaffoldManifest:
    manifest_path = path or MANIFEST_ROOT / "scaffold.json"
    raw = _object(read_json(manifest_path, "scaffold manifest"), "scaffold")
    _keys(raw, {"schema_version", "directories", "files"}, "scaffold")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise KitError("unsupported_schema", "scaffold.schema_version must be 1")
    if not isinstance(raw["directories"], list) or not isinstance(raw["files"], list):
        raise KitError("invalid_schema", "scaffold directories and files must be arrays")
    directories = tuple(
        _safe_relative(value, f"scaffold.directories[{index}]")
        for index, value in enumerate(raw["directories"])
    )
    files: list[ScaffoldFile] = []
    for index, value in enumerate(raw["files"]):
        item = _object(value, f"scaffold.files[{index}]")
        if set(item) not in ({"path", "asset"}, {"path", "generated"}):
            raise KitError(
                "invalid_schema",
                f"scaffold.files[{index}] requires exactly path+asset or path+generated",
            )
        relative = _safe_relative(item["path"], f"scaffold.files[{index}].path")
        asset = (
            _safe_relative(item["asset"], f"scaffold.files[{index}].asset")
            if "asset" in item else None
        )
        generated = _text(item["generated"], "generated") if "generated" in item else None
        if generated is not None and generated not in GENERATORS:
            raise KitError("invalid_schema", f"unknown scaffold generator: {generated}")
        files.append(ScaffoldFile(relative, asset, generated))
    all_paths = [str(item) for item in directories] + [str(item.path) for item in files]
    folded = [value.casefold() for value in all_paths]
    if len(folded) != len(set(folded)):
        raise KitError("invalid_schema", "scaffold paths must be unique under case-insensitive comparison")
    if len(directories) != 17 or len(files) != 20:
        raise KitError("invalid_schema", "scaffold must define exactly 17 directories and 20 files")
    directory_indexes = {str(value).casefold(): index for index, value in enumerate(directories)}
    file_paths = {str(item.path).casefold() for item in files}
    for index, directory in enumerate(directories):
        parent = str(directory.parent)
        if parent != ".":
            parent_index = directory_indexes.get(parent.casefold())
            if parent_index is None or parent_index >= index:
                raise KitError(
                    "invalid_schema",
                    f"directory ancestor must be declared earlier: {directory}",
                )
        for ancestor in directory.parents:
            if str(ancestor) != "." and str(ancestor).casefold() in file_paths:
                raise KitError("invalid_schema", f"file conflicts with directory ancestor: {directory}")
    for item in files:
        parent = str(item.path.parent)
        if parent != "." and parent.casefold() not in directory_indexes:
            raise KitError("invalid_schema", f"file parent directory is undeclared: {item.path}")
        if str(item.path).casefold() in directory_indexes:
            raise KitError("invalid_schema", f"path is both file and directory: {item.path}")
    for item in files:
        if item.asset is not None:
            asset_path = ASSET_ROOT.joinpath(*item.asset.parts)
            if not _safe_existing_descendant(ASSET_ROOT, asset_path, require_file=True):
                raise KitError("missing_asset", f"safe regular asset not found: {item.asset}")
    return ScaffoldManifest(directories, tuple(files))


def load_tasks(path: Path | None = None) -> tuple[Task, ...]:
    manifest_path = path or MANIFEST_ROOT / "tasks.json"
    raw = _object(read_json(manifest_path, "task manifest"), "task manifest")
    _keys(raw, {"schema_version", "tasks"}, "task manifest")
    if (
        type(raw["schema_version"]) is not int
        or raw["schema_version"] != 1
        or not isinstance(raw["tasks"], list)
    ):
        raise KitError("invalid_schema", "task manifest must use schema_version 1 and a tasks array")
    tasks: list[Task] = []
    expected_keys = {"id", "title", "depends_on", "conditional_dependencies", "completion_evidence"}
    for index, value in enumerate(raw["tasks"]):
        item = _object(value, f"tasks[{index}]")
        _keys(item, expected_keys, f"tasks[{index}]")
        task_id = _text(item["id"], "task id")
        if not TASK_ID_RE.fullmatch(task_id):
            raise KitError("invalid_schema", f"invalid task id: {task_id}")
        conditional_raw = item["conditional_dependencies"]
        if not isinstance(conditional_raw, list):
            raise KitError("invalid_schema", "conditional_dependencies must be an array")
        conditional: list[ConditionalDependency] = []
        for condition_index, condition_value in enumerate(conditional_raw):
            condition = _object(condition_value, f"tasks[{index}].conditional_dependencies[{condition_index}]")
            _keys(condition, {"task_id", "condition", "display"}, "conditional dependency")
            conditional.append(ConditionalDependency(
                _text(condition["task_id"], "conditional task_id"),
                _text(condition["condition"], "conditional condition"),
                _text(condition["display"], "conditional display"),
            ))
        if any(
            dependency.condition not in {"recovery_required", "automated_mode"}
            for dependency in conditional
        ):
            raise KitError("invalid_schema", f"{task_id} has an unsupported conditional gate")
        if len({dependency.condition for dependency in conditional}) != len(conditional):
            raise KitError("invalid_schema", f"{task_id} repeats a conditional gate")
        tasks.append(Task(
            task_id,
            _text(item["title"], "task title"),
            _string_list(item["depends_on"], "depends_on"),
            tuple(conditional),
            _text(item["completion_evidence"], "completion_evidence"),
        ))
    expected_ids = [f"S{number:02}" for number in range(1, 17)]
    if [task.id for task in tasks] != expected_ids:
        raise KitError("invalid_schema", "task manifest must contain ordered IDs S01 through S16")
    known: set[str] = set()
    for task in tasks:
        all_dependencies = task.depends_on + tuple(
            dependency.task_id for dependency in task.conditional_dependencies
        )
        if any(dependency not in known for dependency in all_dependencies):
            raise KitError("invalid_schema", f"{task.id} has unknown or forward prerequisite")
        known.add(task.id)
    return tuple(tasks)


def display_dependencies(task: Task) -> str:
    parts = [", ".join(task.depends_on) if task.depends_on else "None"]
    parts.extend(dependency.display for dependency in task.conditional_dependencies)
    return "; ".join(parts)


def render_task_reference(tasks: Sequence[Task]) -> str:
    lines = [
        "# Canonical setup checklist",
        "",
        "Generated from `manifests/tasks.json`; do not edit this reference by hand.",
        "",
        "| ID | Setup task | Depends on | Completion evidence required |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {task.id} | {task.title} | {display_dependencies(task)} | {task.completion_evidence} |"
        for task in tasks
    )
    return "\n".join(lines) + "\n"


def _markdown_cell(value: object) -> str:
    encoded = html.escape(json.dumps(value, ensure_ascii=True), quote=True)
    for character, entity in (
        ("`", "&#96;"),
        ("|", "&#124;"),
        ("[", "&#91;"),
        ("]", "&#93;"),
        ("(", "&#40;"),
        (")", "&#41;"),
        ("\\", "&#92;"),
    ):
        encoded = encoded.replace(character, entity)
    return encoded


def render_answers(answers: ConfirmedAnswers) -> str:
    values = [
        ("Primary purpose", answers.primary_purpose, "S01"),
        ("Useful first result", answers.useful_first_result, "S01"),
        ("Review preference", answers.review_preference, "S01"),
        ("Authorized destination", str(answers.destination), "S02"),
        ("Local setup authorized", answers.authorize_local_setup, "S02"),
        ("Create root if missing", answers.authorize_create_if_missing, "S02"),
        ("Exclusions", list(answers.exclusions), "S02"),
        ("External actions", answers.external_actions, "S02"),
        ("Recovery decision", answers.recovery_decision.value, "S02"),
    ]
    lines = [
        "# Confirmed setup answers",
        "",
        "Values are JSON-encoded and HTML-escaped so user text cannot create Markdown structure.",
        "",
        "| Question | Confirmed answer | Date | Applicable step | Unresolved decisions |",
        "| --- | --- | --- | --- | --- |",
    ]
    for question, value, step in values:
        lines.append(
            f"| {question} | {_markdown_cell(value)} | {answers.confirmed_on.isoformat()} | {step} | None |"
        )
    lines.append("")
    lines.append("Source scope decisions are recorded in [scope.md](scope.md), not duplicated here.")
    return "\n".join(lines) + "\n"


def render_scope(answers: ConfirmedAnswers) -> str:
    lines = [
        "# Approved setup scope",
        "",
        f"Confirmed on: {answers.confirmed_on.isoformat()}",
        "",
        "Planned locations below are inert records. They are not instructions to read or fetch.",
        "",
    ]
    for number, source in enumerate(answers.sources, 1):
        fields: tuple[tuple[str, object], ...] = (
            ("Source and purpose", source.source_and_purpose),
            ("Account/location", source.account_or_location),
            ("In scope", source.in_scope),
            ("Out of scope", source.out_of_scope),
            ("Access method", source.access_method),
            ("Permitted operations", list(source.allowed_operations)),
            ("Capability status", source.capability_status.value),
            ("Limits and retention", source.limits_and_retention),
        )
        lines.extend([
            f"## Source {number}",
            "",
            "| Field | Confirmed plan |",
            "| --- | --- |",
        ])
        lines.extend(f"| {label} | {_markdown_cell(value)} |" for label, value in fields)
        lines.append("")
    lines.extend([
        "## Global boundaries",
        "",
        f"- Exclusions: {_markdown_cell(list(answers.exclusions))}",
        "- External actions: unauthorized.",
        "- Source systems remain unchanged.",
        "- Credentials must not be stored in this vault.",
        f"- Recovery decision: {answers.recovery_decision.value}.",
        "- Automation: not enabled.",
    ])
    return "\n".join(lines) + "\n"


def render_checklist(
    tasks: Sequence[Task],
    answers: ConfirmedAnswers,
    *,
    s03_complete: bool,
    created_at: datetime,
) -> str:
    lines = [
        "# Setup checklist",
        "",
        (
            "Current step: S04 (eligible but not authorized or executed by bootstrap)."
            if s03_complete
            else "Current step: S03 (structure written; post-write validation pending)."
        ),
        "",
        "| ID | Setup task | Depends on | Completion evidence required | State | Evidence observed | Outstanding decisions | Next permitted action |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for task in tasks:
        if task.id == "S01":
            state = "complete"
            observed = (
                f"Confirmed purpose, useful first result, and review preference on "
                f"{answers.confirmed_on.isoformat()}."
            )
            decisions = "None"
            action = "Completed"
        elif task.id == "S02":
            state = "complete"
            observed = (
                f"Confirmed exact destination, local creation permission, per-source boundaries, "
                f"retention, exclusions, external-action gate, and recovery decision on "
                f"{answers.confirmed_on.isoformat()}. Root preflight passed."
            )
            decisions = "None"
            action = "Completed"
        elif task.id == "S03":
            if s03_complete:
                state = "complete"
                observed = (
                    "Bootstrap rechecked the empty-root precondition, exclusively created the "
                    "manifest-defined 17 directories and 20 files, and passed post-write structural "
                    f"validation at {created_at.isoformat().replace('+00:00', 'Z')}."
                )
                decisions = "None"
                action = "Completed"
            else:
                state = "in_progress"
                observed = "Manifest-defined structure was written; post-write validation has not completed."
                decisions = "Validation result pending."
                action = "Run bootstrap structural validation; do not claim S03 complete yet."
        elif task.id == "S13" and answers.recovery_decision is RecoveryDecision.DEFERRED:
            state = "deferred"
            observed = "User explicitly deferred recovery verification during S02."
            decisions = "Choose and authorize a separate restore test location before resuming."
            action = "No recovery action is permitted until the deferral is revised."
        elif task.id == "S13" and answers.recovery_decision is RecoveryDecision.NOT_REQUIRED:
            state = "not_applicable"
            observed = "User selected the no-recovery-verification branch during S02."
            decisions = "Reopen S13 if recovery verification later becomes required."
            action = "No action on the current branch."
        else:
            state = "not_started"
            observed = "None"
            decisions = "Authorization and task-specific evidence remain outstanding."
            incomplete = [dependency for dependency in task.depends_on if dependency not in {"S01", "S02", "S03"}]
            action = (
                f"Complete prerequisites: {', '.join(incomplete)}."
                if incomplete
                else "Obtain explicit authorization and execute this bounded task."
            )
        lines.append(
            f"| {task.id} | {task.title} | {display_dependencies(task)} | {task.completion_evidence} | "
            f"{state} | {observed} | {decisions} | {action} |"
        )
    lines.extend([
        "",
        f"Recovery status: {answers.recovery_decision.value}; recovery has not been verified.",
        "",
        "Automation status: not enabled.",
    ])
    return "\n".join(lines) + "\n"


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as exc:
        raise KitError("path_inspection_failed", f"cannot inspect {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _safe_existing_descendant(root: Path, candidate: Path, *, require_file: bool = False) -> bool:
    root = absolute_without_link_resolution(root)
    candidate = absolute_without_link_resolution(candidate)
    if candidate != root and root not in candidate.parents:
        raise KitError("unsafe_path", f"path escapes allowed root: {candidate}")
    _assert_safe_existing_components(root)
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if not os.path.lexists(current):
            return False
        if _is_reparse(current):
            raise KitError("unsafe_path", f"path contains a link or reparse point: {current}")
    if require_file:
        return candidate.is_file()
    return candidate.exists()


def _assert_safe_existing_components(path: Path) -> None:
    if not path.is_absolute():
        raise KitError("unsafe_root", "root must be absolute before safety checks")
    current = Path(path.anchor)
    if not current.exists():
        raise KitError("unsafe_root", f"root anchor does not exist: {current}")
    if _is_reparse(current):
        raise KitError("unsafe_root", f"root path contains a reparse point: {current}")
    for part in path.parts[1:]:
        current /= part
        if not os.path.lexists(current):
            break
        if _is_reparse(current):
            raise KitError("unsafe_root", f"root path contains a symlink or reparse point: {current}")


def preflight_root(root: Path, answers: ConfirmedAnswers) -> None:
    _reject_nonlocal_root_text(str(root), "vault root")
    if absolute_without_link_resolution(root) != answers.destination:
        raise KitError("destination_mismatch", "CLI root must exactly match answers.workspace.destination")
    package = absolute_without_link_resolution(PACKAGE_ROOT)
    resolved = absolute_without_link_resolution(root)
    if resolved == package or package in resolved.parents:
        raise KitError("unsafe_root", "the package or a package subdirectory cannot be a vault root")
    _assert_safe_existing_components(root)
    if root.exists():
        if not root.is_dir() or _is_reparse(root):
            raise KitError("unsafe_root", "vault root must be a real directory, not a file or link")
        try:
            first = next(root.iterdir(), None)
        except OSError as exc:
            raise KitError("path_inspection_failed", f"cannot inspect root: {exc}") from exc
        if first is not None:
            raise KitError("nonempty_root", f"vault root is not empty; first entry: {first.name}")
    else:
        if not answers.authorize_create_if_missing:
            raise KitError("consent_required", "root is missing and creation was not authorized")
        if not root.parent.exists() or not root.parent.is_dir() or _is_reparse(root.parent):
            raise KitError(
                "unsafe_root",
                "missing root may be created only when its immediate real-directory parent exists",
            )


def _asset_bytes(item: ScaffoldFile, created: date) -> bytes:
    if item.asset is None:
        raise KitError("invalid_operation", f"asset requested for generated file {item.path}")
    source = ASSET_ROOT.joinpath(*item.asset.parts)
    try:
        if not _safe_existing_descendant(ASSET_ROOT, source, require_file=True):
            raise KitError("asset_read_failed", f"asset is not a safe regular file: {item.asset}")
        text = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError, KitError) as exc:
        raise KitError("asset_read_failed", f"cannot read asset {item.asset}: {exc}") from exc
    return (
        text.replace("{{CREATED_DATE}}", created.isoformat())
        .replace("{{CREATED_COMPACT}}", created.strftime("%Y%m%d"))
        .encode("utf-8")
    )


def _exclusive_write(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)


def render_completed_log(created_at: datetime, initial: bytes) -> bytes:
    timestamp = created_at.isoformat().replace("+00:00", "Z")
    row = (
        f"| {timestamp} | bootstrap | complete | None | 17 directories and 20 files | "
        "S03 structure created and post-write validation passed. | S04 requires separate authorization. |\n"
    )
    return initial + row.encode("utf-8")


def _replace_created_unchanged(
    root: Path,
    path: Path,
    initial: bytes,
    replacement: bytes,
    created_paths: set[str],
    relative: str,
) -> None:
    if relative not in created_paths:
        raise KitError("finalization_conflict", f"refusing to replace a file not created now: {relative}")
    temporary = path.with_name(f".{path.name}.bootstrap-finalize.tmp")
    try:
        if not _safe_existing_descendant(root, path, require_file=True):
            raise KitError("finalization_conflict", f"created file is not a safe regular file: {relative}")
        if path.read_bytes() != initial:
            raise KitError("finalization_conflict", f"created file changed before finalization: {relative}")
        if not _safe_existing_descendant(root, path.parent):
            raise KitError("finalization_conflict", f"created file parent became unsafe: {relative}")
        with temporary.open("xb") as stream:
            stream.write(replacement)
            stream.flush()
            os.fsync(stream.fileno())
        if (
            not _safe_existing_descendant(root, path, require_file=True)
            or path.read_bytes() != initial
            or not _safe_existing_descendant(root, temporary, require_file=True)
        ):
            raise KitError("finalization_conflict", f"created file changed before replace: {relative}")
        os.replace(temporary, path)
    except (OSError, KitError) as exc:
        cleanup_note = ""
        try:
            if os.path.lexists(temporary):
                if (
                    _safe_existing_descendant(root, temporary, require_file=True)
                    and temporary.read_bytes() == replacement
                ):
                    temporary.unlink()
                else:
                    cleanup_note = "; temporary file preserved because ownership/content was not proven"
        except (OSError, KitError) as cleanup_exc:
            cleanup_note = f"; temporary cleanup unreconciled: {cleanup_exc}"
        raise KitError(
            "finalization_failed",
            f"could not finalize {relative}: {exc}{cleanup_note}",
        ) from exc


def _reconcile_finalization(
    root: Path,
    created_paths: set[str],
    states: Sequence[tuple[str, Path, bytes, bytes]],
) -> str:
    results: list[str] = []
    unreconciled: list[str] = []
    for relative, path, pending, complete in states:
        try:
            if relative not in created_paths:
                unreconciled.append(f"{relative}: not created by this invocation")
                continue
            if not _safe_existing_descendant(root, path, require_file=True):
                unreconciled.append(f"{relative}: unsafe or missing")
                continue
            current = path.read_bytes()
            if current == pending:
                results.append(f"{relative}=already_pending")
            elif current == complete:
                _replace_created_unchanged(
                    root, path, complete, pending, created_paths, relative
                )
                results.append(f"{relative}=reverted_to_pending")
            else:
                unreconciled.append(f"{relative}: concurrent change preserved")
        except (OSError, KitError) as exc:
            unreconciled.append(f"{relative}: {exc}")
    return f"reconciliation [{', '.join(results)}]; unreconciled [{'; '.join(unreconciled)}]"


def create_vault(
    root: Path,
    answers: ConfirmedAnswers,
    scaffold: ScaffoldManifest,
    tasks: Sequence[Task],
    *,
    writer: Callable[[Path, bytes], None] = _exclusive_write,
    validator: Callable[
        [Path, ScaffoldManifest, Sequence[Task], str], list[ValidationIssue]
    ] | None = None,
) -> list[str]:
    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    generated = {
        "scope": render_scope(answers),
        "answers": render_answers(answers),
        "checklist": render_checklist(
            tasks, answers, s03_complete=False, created_at=created_at
        ),
    }
    file_contents = tuple(
        (
            item,
            generated[item.generated].encode("utf-8")
            if item.generated is not None
            else _asset_bytes(item, created_at.date()),
        )
        for item in scaffold.files
    )
    preflight_root(root, answers)
    created: list[str] = []
    try:
        if not root.exists():
            root.mkdir()
            created.append(".")
        preflight_root(root, answers)
        for relative in scaffold.directories:
            target = root.joinpath(*relative.parts)
            target.mkdir()
            created.append(f"{relative}/")
        for item, content in file_contents:
            target = root.joinpath(*item.path.parts)
            writer(target, content)
            created.append(str(item.path))
        run_validation = validator or (
            lambda check_root, check_scaffold, check_tasks, phase:
            validate_bootstrap(check_root, check_scaffold, check_tasks, phase=phase)
        )
        pending_issues = run_validation(root, scaffold, tasks, "pending")
        if pending_issues:
            raise KitError(
                "post_write_validation",
                f"pending S03 validation reported {[issue.as_dict() for issue in pending_issues]}",
            )
        initial_by_path = {str(item.path): content for item, content in file_contents}
        created_set = set(created)
        checklist_relative = "_meta/setup-checklist.md"
        log_relative = "log.md"
        completed_checklist = render_checklist(
            tasks, answers, s03_complete=True, created_at=created_at
        ).encode("utf-8")
        completed_log = render_completed_log(created_at, initial_by_path[log_relative])
        finalization_states = (
            (
                checklist_relative,
                root / "_meta" / "setup-checklist.md",
                initial_by_path[checklist_relative],
                completed_checklist,
            ),
            (
                log_relative,
                root / "log.md",
                initial_by_path[log_relative],
                completed_log,
            ),
        )
        try:
            _replace_created_unchanged(
                root,
                root / "_meta" / "setup-checklist.md",
                initial_by_path[checklist_relative],
                completed_checklist,
                created_set,
                checklist_relative,
            )
            _replace_created_unchanged(
                root,
                root / "log.md",
                initial_by_path[log_relative],
                completed_log,
                created_set,
                log_relative,
            )
            final_issues = run_validation(root, scaffold, tasks, "complete")
            if final_issues:
                raise KitError(
                    "post_write_validation",
                    f"final S03 validation reported {[issue.as_dict() for issue in final_issues]}",
                )
        except (OSError, KitError) as final_exc:
            reconciliation = _reconcile_finalization(
                root, created_set, finalization_states
            )
            code = (
                final_exc.code
                if isinstance(final_exc, KitError)
                and final_exc.code in {"post_write_validation", "finalization_failed"}
                else "post_write_validation"
            )
            message = final_exc.message if isinstance(final_exc, KitError) else str(final_exc)
            raise KitError(code, f"{message}; {reconciliation}") from final_exc
    except (OSError, KitError) as exc:
        message = exc.message if isinstance(exc, KitError) else str(exc)
        if isinstance(exc, KitError) and exc.code in {
            "post_write_validation",
            "finalization_failed",
        }:
            raise KitError(
                exc.code,
                f"bootstrap stopped after creating {created!r}; existing bytes were preserved; error: {message}",
            ) from exc
        raise KitError(
            "partial_write",
            f"bootstrap stopped after creating {created!r}; existing bytes were preserved; error: {message}",
        ) from exc
    return created


def _walk_without_links(root: Path) -> tuple[set[str], set[str], list[ValidationIssue]]:
    directories: set[str] = set()
    files: set[str] = set()
    issues: list[ValidationIssue] = []

    def visit(directory: Path, prefix: PurePosixPath | None = None) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            issues.append(ValidationIssue("unreadable_path", str(prefix or "."), str(exc)))
            return
        for entry in entries:
            relative = PurePosixPath(entry.name) if prefix is None else prefix / entry.name
            relative_text = str(relative)
            try:
                if entry.is_symlink():
                    issues.append(ValidationIssue("unsafe_link", relative_text, "links are not followed"))
                elif entry.is_dir(follow_symlinks=False):
                    path = Path(entry.path)
                    if _is_reparse(path):
                        issues.append(ValidationIssue("unsafe_link", relative_text, "reparse point is not followed"))
                    else:
                        directories.add(relative_text)
                        visit(path, relative)
                elif entry.is_file(follow_symlinks=False):
                    files.add(relative_text)
                else:
                    issues.append(ValidationIssue("unsupported_entry", relative_text, "not a regular file or directory"))
            except OSError as exc:
                issues.append(ValidationIssue("unreadable_path", relative_text, str(exc)))

    visit(root)
    return directories, files, issues


def _read_text(root: Path, relative: str, issues: list[ValidationIssue]) -> str | None:
    path = root.joinpath(*PurePosixPath(relative).parts)
    try:
        if not _safe_existing_descendant(root, path, require_file=True):
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError, KitError) as exc:
        issues.append(ValidationIssue("unreadable_file", relative, str(exc)))
        return None


def _parse_frontmatter(text: str, relative: str, issues: list[ValidationIssue]) -> dict[str, object] | None:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        issues.append(ValidationIssue("frontmatter", relative, "missing opening ---"))
        return None
    try:
        end = lines.index("---", 1)
    except ValueError:
        issues.append(ValidationIssue("frontmatter", relative, "missing closing ---"))
        return None
    values: dict[str, object] = {}
    for line in lines[1:end]:
        match = FRONTMATTER_LINE_RE.fullmatch(line)
        if match is None:
            issues.append(ValidationIssue(
                "frontmatter",
                relative,
                "only one-line JSON-compatible scalar/list values are supported",
            ))
            return None
        key, raw_value = match.groups()
        if key in values:
            issues.append(ValidationIssue("frontmatter", relative, f"duplicate key: {key}"))
            return None
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            if raw_value == "null":
                value = None
            else:
                issues.append(ValidationIssue(
                    "frontmatter", relative, f"unsupported value for {key}: {raw_value}"
                ))
                return None
        if isinstance(value, dict):
            issues.append(ValidationIssue(
                "frontmatter", relative, f"object values are not supported for {key}"
            ))
            return None
        values[key] = value
    return values


TEMPLATE_FIELDS: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "inbox": ({"id","title","kind","status","created","updated","tags"}, ("Summary","Triage","Related","Sources")),
    "entity": ({"id","title","kind","status","created","updated","tags","entity_type"}, ("Summary","Key facts","Related","Sources")),
    "concept": ({"id","title","kind","status","created","updated","tags"}, ("Summary","Meaning and use","Limitations","Related","Sources")),
    "project": ({"id","title","kind","status","created","updated","tags","outcome","target_state","target_date"}, ("Summary","Success criteria","Current state","Next actions","Decisions","Related","Sources")),
    "area": ({"id","title","kind","status","created","updated","tags"}, ("Summary","Responsibility and standards","Current focus","Related","Sources")),
    "resource": ({"id","title","kind","status","created","updated","tags"}, ("Summary","Key points","Limitations","Related","Sources")),
    "moc": ({"id","title","kind","status","created","updated","tags"}, ("Summary","Navigation")),
}


def _validate_templates(root: Path, issues: list[ValidationIssue]) -> None:
    for kind, (required, headings) in TEMPLATE_FIELDS.items():
        relative = f"_meta/templates/{kind}.md"
        text = _read_text(root, relative, issues)
        if text is None:
            continue
        values = _parse_frontmatter(text, relative, issues)
        if values is None:
            continue
        allowed = required | {"aliases"}
        missing = sorted(required - values.keys())
        extra = sorted(values.keys() - allowed)
        if missing or extra:
            issues.append(ValidationIssue(
                "template_schema", relative, f"missing={missing}, extra={extra}"
            ))
        if values.get("kind") != kind:
            issues.append(ValidationIssue("template_schema", relative, "kind does not match template"))
        expected_id = f"{kind}-<YYYYMMDD>-<slug>"
        tags = values.get("tags")
        aliases = values.get("aliases", [])
        if (
            values.get("id") != expected_id
            or values.get("title") != "<title>"
            or values.get("status") != "draft"
            or values.get("created") != "<YYYY-MM-DD>"
            or values.get("updated") != "<YYYY-MM-DD>"
            or not isinstance(tags, list)
            or any(
                not isinstance(tag, str)
                or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tag) is None
                for tag in tags
            )
            or not isinstance(aliases, list)
            or any(not isinstance(alias, str) for alias in aliases)
        ):
            issues.append(ValidationIssue(
                "template_schema",
                relative,
                "ID/title/date placeholders, draft status, tags, or aliases are invalid",
            ))
        if kind == "entity" and values.get("entity_type") != "<person|team|product|organization>":
            issues.append(ValidationIssue("template_schema", relative, "entity_type enum placeholder is invalid"))
        if kind == "project" and (
            not isinstance(values.get("outcome"), str)
            or values.get("target_state") != "unknown"
            or values.get("target_date") is not None
        ):
            issues.append(ValidationIssue(
                "template_schema",
                relative,
                "project unknown target_state requires target_date null and a string outcome",
            ))
        first_heading = next(
            (line[2:] for line in text.splitlines() if line.startswith("# ")), None
        )
        if first_heading != values.get("title"):
            issues.append(ValidationIssue(
                "template_title", relative, "frontmatter title must match the first heading"
            ))
        expected_headings = [f"## {heading}" for heading in headings]
        actual_headings = [line for line in text.splitlines() if line.startswith("## ")]
        if actual_headings != expected_headings:
            issues.append(ValidationIssue(
                "template_headings", relative, f"expected {expected_headings}, found {actual_headings}"
            ))


def _validate_moc(root: Path, issues: list[ValidationIssue]) -> None:
    relative = "_meta/MOCs/knowledge-map.md"
    text = _read_text(root, relative, issues)
    if text is None:
        return
    values = _parse_frontmatter(text, relative, issues)
    if values is None:
        return
    required = {"id","title","kind","status","created","updated","tags"}
    allowed = required | {"aliases"}
    moc_id = values.get("id")
    tags = values.get("tags")
    aliases = values.get("aliases", [])
    if (
        required - values.keys()
        or values.keys() - allowed
        or values.get("kind") != "moc"
        or values.get("status") != "active"
        or not isinstance(tags, list)
        or any(
            not isinstance(tag, str)
            or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tag) is None
            for tag in tags
        )
        or not isinstance(aliases, list)
        or any(not isinstance(alias, str) for alias in aliases)
        or not isinstance(moc_id, str)
        or re.fullmatch(r"moc-\d{8}-[a-z0-9]+(?:-[a-z0-9]+)*", moc_id) is None
    ):
        issues.append(ValidationIssue("moc_schema", relative, "live MOC schema/status is invalid"))
    lines = text.splitlines()
    title = values.get("title")
    first_heading = next((line[2:] for line in lines if line.startswith("# ")), None)
    if title != first_heading:
        issues.append(ValidationIssue("moc_title", relative, "frontmatter title must match first heading"))
    for key in ("created", "updated"):
        try:
            if not isinstance(values.get(key), str):
                raise ValueError
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", values[key]) is None:
                raise ValueError
            date.fromisoformat(values[key])
        except ValueError:
            issues.append(ValidationIssue("moc_date", relative, f"{key} must be a quoted YYYY-MM-DD"))


def _table_data_rows(text: str, header: str) -> list[str] | None:
    lines = text.splitlines()
    try:
        index = lines.index(header)
    except ValueError:
        return None
    rows: list[str] = []
    for line in lines[index + 2:]:
        if not line.startswith("|"):
            break
        rows.append(line)
    return rows


def _validate_bootstrap_tables(
    root: Path, issues: list[ValidationIssue], *, phase: str
) -> None:
    cases = {
        "_meta/sources.md": "| Source ID | Upstream identity | Version | Origin | Source date | Captured at (UTC) | Capture or reference | Representation | SHA-256 | Limitations |",
        "_meta/processing.md": "| Source ID | State | Intended outputs | Completed pages | Remaining work | Failure reason | Updated at (UTC) |",
    }
    for relative, header in cases.items():
        text = _read_text(root, relative, issues)
        if text is None:
            continue
        rows = _table_data_rows(text, header)
        if rows is None:
            issues.append(ValidationIssue("table_header", relative, "required table header missing"))
        elif rows:
            issues.append(ValidationIssue("bootstrap_not_empty", relative, "bootstrap table has data rows"))
    log_relative = "log.md"
    log_header = "| UTC time | Operation | Result | Source IDs | Changed paths | Summary | Unresolved issues |"
    log_text = _read_text(root, log_relative, issues)
    if log_text is not None:
        rows = _table_data_rows(log_text, log_header)
        if rows is None:
            issues.append(ValidationIssue("table_header", log_relative, "required table header missing"))
        elif phase == "pending" and rows:
            issues.append(ValidationIssue("bootstrap_log", log_relative, "pending bootstrap must not claim completion"))
        elif phase == "complete":
            if len(rows) != 1:
                issues.append(ValidationIssue("bootstrap_log", log_relative, "completed bootstrap requires exactly one operation row"))
            elif "| bootstrap | complete |" not in rows[0] or "post-write validation passed" not in rows[0]:
                issues.append(ValidationIssue("bootstrap_log", log_relative, "bootstrap completion row is malformed"))
            else:
                cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
                try:
                    timestamp = datetime.fromisoformat(cells[0].replace("Z", "+00:00"))
                    if timestamp.tzinfo is None or timestamp.utcoffset() != timezone.utc.utcoffset(timestamp):
                        raise ValueError
                except (ValueError, IndexError):
                    issues.append(ValidationIssue("bootstrap_log", log_relative, "bootstrap timestamp must be ISO UTC"))


CHECKLIST_HEADER = "| ID | Setup task | Depends on | Completion evidence required | State | Evidence observed | Outstanding decisions | Next permitted action |"


def _parse_checklist(
    text: str, relative: str, issues: list[ValidationIssue]
) -> dict[str, tuple[str, str, str, str, str, str, str]]:
    lines = text.splitlines()
    if lines.count(CHECKLIST_HEADER) != 1:
        issues.append(ValidationIssue("checklist_header", relative, "exact checklist header missing or duplicated"))
        return {}
    header_index = lines.index(CHECKLIST_HEADER)
    if header_index + 1 >= len(lines) or lines[header_index + 1] != "| --- | --- | --- | --- | --- | --- | --- | --- |":
        issues.append(ValidationIssue("checklist_header", relative, "checklist separator is malformed"))
        return {}
    result: dict[str, tuple[str, str, str, str, str, str, str]] = {}
    parsed_indexes: set[int] = set()
    for line_index, line in enumerate(lines[header_index + 2:], header_index + 2):
        if not line.startswith("|"):
            break
        parsed_indexes.add(line_index)
        cells = [cell.strip() for cell in line[1:-1].split("|")]
        if len(cells) != 8:
            issues.append(ValidationIssue("checklist_row", relative, f"row must have exactly 8 cells: {line}"))
            continue
        task_id = cells[0]
        if task_id in result:
            issues.append(ValidationIssue("checklist_task", relative, f"duplicate task ID: {task_id}"))
            continue
        result[task_id] = (
            cells[1],
            cells[2],
            cells[3],
            cells[4],
            cells[5],
            cells[6],
            cells[7],
        )
    for line_index, line in enumerate(lines):
        if line_index not in parsed_indexes and re.match(r"^\|\s*S\d", line):
            issues.append(ValidationIssue(
                "checklist_row", relative, f"task-like row appears outside the canonical table: {line}"
            ))
    return result


def _validate_checklist(
    root: Path, tasks: Sequence[Task], issues: list[ValidationIssue], *, phase: str
) -> None:
    relative = "_meta/setup-checklist.md"
    text = _read_text(root, relative, issues)
    if text is None:
        return
    rows = _parse_checklist(text, relative, issues)
    expected_ids = {task.id for task in tasks}
    extra_ids = sorted(set(rows) - expected_ids)
    if extra_ids:
        issues.append(ValidationIssue("checklist_task", relative, f"unexpected task IDs: {extra_ids}"))
    for task in tasks:
        row = rows.get(task.id)
        if row is None:
            issues.append(ValidationIssue("checklist_task", relative, f"missing {task.id}"))
            continue
        title, dependencies, evidence, state_value, observed, _decisions, _action = row
        if (title, dependencies, evidence) != (
            task.title, display_dependencies(task), task.completion_evidence
        ):
            issues.append(ValidationIssue(
                "checklist_definition", relative, f"{task.id} differs from tasks.json"
            ))
        if state_value not in TASK_STATES:
            issues.append(ValidationIssue("checklist_state", relative, f"{task.id} has invalid state"))
        if state_value == "complete" and observed in {"", "None"}:
            issues.append(ValidationIssue("checklist_evidence", relative, f"{task.id} completion evidence is empty"))
    for task in tasks:
        row = rows.get(task.id)
        if row is None or row[3] != "complete":
            continue
        incomplete = [
            dependency for dependency in task.depends_on
            if dependency not in rows or rows[dependency][3] != "complete"
        ]
        if incomplete:
            issues.append(ValidationIssue(
                "incomplete_prerequisite",
                relative,
                f"{task.id} is complete while prerequisites are incomplete: {incomplete}",
            ))
    for task_id in ("S01", "S02"):
        if task_id in rows and rows[task_id][3] != "complete":
            issues.append(ValidationIssue("bootstrap_state", relative, f"{task_id} must be complete"))
    expected_s03 = "complete" if phase == "complete" else "in_progress"
    if "S03" in rows and rows["S03"][3] != expected_s03:
        issues.append(ValidationIssue("bootstrap_state", relative, f"S03 must be {expected_s03}"))
    for task in tasks[3:]:
        if task.id in rows and rows[task.id][3] == "complete":
            issues.append(ValidationIssue(
                "bootstrap_state", relative, f"{task.id} cannot be complete at bootstrap"
            ))
    if "Automation status: not enabled." not in text:
        issues.append(ValidationIssue("automation_state", relative, "automation must be explicitly not enabled"))
    if "recovery has not been verified." not in text:
        issues.append(ValidationIssue("recovery_state", relative, "recovery must be explicitly unverified"))


LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def _validate_links(root: Path, files: Iterable[str], issues: list[ValidationIssue]) -> None:
    for relative in files:
        if not relative.endswith(".md"):
            continue
        text = _read_text(root, relative, issues)
        if text is None:
            continue
        parent = PurePosixPath(relative).parent
        for link in LINK_RE.findall(text):
            if re.match(r"^https?://", link, re.IGNORECASE) or link.startswith("#") or link.startswith("mailto:"):
                continue
            clean = link.split("#", 1)[0]
            for _ in range(4):
                decoded = unquote(clean)
                if decoded == clean:
                    break
                clean = decoded
            if (
                CONTROL_RE.search(clean) is not None
                or clean.startswith(("/", "\\"))
                or "\\" in clean
                or ":" in clean
                or PurePosixPath(clean).is_absolute()
            ):
                issues.append(ValidationIssue("unsafe_link", relative, f"unsafe local link: {link}"))
                continue
            destination = parent / PurePosixPath(clean)
            normalized_parts: list[str] = []
            unsafe = False
            for part in destination.parts:
                if part == "..":
                    if not normalized_parts:
                        unsafe = True
                        break
                    normalized_parts.pop()
                elif part not in {"", "."}:
                    normalized_parts.append(part)
            if unsafe:
                issues.append(ValidationIssue("unsafe_link", relative, f"link escapes root: {link}"))
                continue
            target = root.joinpath(*normalized_parts)
            try:
                if not _safe_existing_descendant(root, target):
                    issues.append(ValidationIssue("broken_link", relative, f"unresolved link: {link}"))
            except (KitError, OSError, ValueError) as exc:
                message = exc.message if isinstance(exc, KitError) else str(exc)
                issues.append(ValidationIssue("broken_link", relative, message))


def validate_bootstrap(
    root: Path,
    scaffold: ScaffoldManifest,
    tasks: Sequence[Task],
    *,
    phase: str = "complete",
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if phase not in {"pending", "complete"}:
        return [ValidationIssue("invalid_phase", ".", f"unsupported validation phase: {phase}")]
    try:
        _assert_safe_existing_components(root)
    except KitError as exc:
        return [ValidationIssue("invalid_root", ".", exc.message)]
    if not root.exists() or not root.is_dir() or root.is_symlink() or _is_reparse(root):
        return [ValidationIssue("invalid_root", ".", "root must be an existing real directory")]
    directories, files, walk_issues = _walk_without_links(root)
    issues.extend(walk_issues)
    expected_directories = {str(path) for path in scaffold.directories}
    expected_files = {str(item.path) for item in scaffold.files}
    for relative in sorted(expected_directories - directories):
        issues.append(ValidationIssue("missing_directory", relative, "required directory missing"))
    for relative in sorted(expected_files - files):
        issues.append(ValidationIssue("missing_file", relative, "required file missing"))
    for relative in sorted(directories - expected_directories):
        issues.append(ValidationIssue("extra_directory", relative, "unexpected bootstrap directory"))
    for relative in sorted(files - expected_files):
        issues.append(ValidationIssue("extra_file", relative, "unexpected bootstrap file"))
    _validate_templates(root, issues)
    _validate_moc(root, issues)
    _validate_bootstrap_tables(root, issues, phase=phase)
    _validate_checklist(root, tasks, issues, phase=phase)
    _validate_links(root, files, issues)
    scope = _read_text(root, "_meta/scope.md", issues)
    if scope is not None:
        if "- External actions: unauthorized." not in scope:
            issues.append(ValidationIssue("scope_boundary", "_meta/scope.md", "external actions are not denied"))
        if "- Automation: not enabled." not in scope:
            issues.append(ValidationIssue("automation_state", "_meta/scope.md", "automation is not disabled"))
    return issues


def validate_reference(tasks: Sequence[Task], reference_path: Path) -> list[ValidationIssue]:
    try:
        actual = reference_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return [ValidationIssue("reference_read", str(reference_path), str(exc))]
    expected = render_task_reference(tasks)
    if actual != expected:
        return [ValidationIssue(
            "reference_mismatch",
            str(reference_path),
            "reference differs from manifests/tasks.json; run scripts/render_reference.py",
        )]
    return []
