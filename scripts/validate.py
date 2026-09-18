#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kit import (
    KitError,
    PACKAGE_ROOT,
    absolute_without_link_resolution,
    load_scaffold,
    load_tasks,
    validate_bootstrap,
    validate_reference,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Read-only validation for a freshly bootstrapped or manually produced starter root."
    )
    result.add_argument("--root", type=Path, help="starter root to evaluate")
    result.add_argument(
        "--check-reference",
        action="store_true",
        help="verify references/setup-checklist.md matches manifests/tasks.json",
    )
    result.add_argument("--json", action="store_true", help="write a JSON report")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.root is None and not args.check_reference:
        parser().error("provide --root and/or --check-reference")
    try:
        scaffold = load_scaffold()
        tasks = load_tasks()
        issues = []
        if args.root is not None:
            issues.extend(validate_bootstrap(
                absolute_without_link_resolution(args.root), scaffold, tasks
            ))
        if args.check_reference:
            issues.extend(validate_reference(tasks, PACKAGE_ROOT / "references" / "setup-checklist.md"))
    except KitError as exc:
        print(f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 3
    payload = {"ok": not issues, "mode": "bootstrap-only", "issues": [i.as_dict() for i in issues]}
    if args.json:
        print(json.dumps(payload, indent=2))
    elif issues:
        print(f"Bootstrap validation failed with {len(issues)} issue(s):")
        for issue in issues:
            print(f"- [{issue.code}] {issue.path}: {issue.message}")
    else:
        print("Bootstrap validation passed. This does not validate an evolved or mature vault.")
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
