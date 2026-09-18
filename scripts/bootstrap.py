#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from kit import (
    KitError,
    absolute_without_link_resolution,
    create_vault,
    load_scaffold,
    load_tasks,
    parse_answers,
    validate_bootstrap,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create the fixed second-brain starter scaffold after validating confirmed answers."
    )
    result.add_argument("--answers", required=True, type=Path, help="confirmed JSON answers file")
    result.add_argument("--root", required=True, type=Path, help="authorized destination root")
    result.add_argument(
        "--manifest", type=Path, help="alternate scaffold manifest (primarily for validation testing)"
    )
    result.add_argument("--json", action="store_true", help="write the result as JSON")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        answers = parse_answers(absolute_without_link_resolution(args.answers))
        scaffold = load_scaffold(
            absolute_without_link_resolution(args.manifest) if args.manifest else None
        )
        tasks = load_tasks()
        root = absolute_without_link_resolution(args.root)
        created = create_vault(root, answers, scaffold, tasks)
        issues = validate_bootstrap(root, scaffold, tasks)
        if issues:
            payload = {"ok": False, "code": "post_write_validation", "issues": [i.as_dict() for i in issues]}
            print(json.dumps(payload, indent=2), file=sys.stderr)
            return 4
        payload = {"ok": True, "root": str(root), "created_count": len(created), "next_step": "S04"}
        print(json.dumps(payload, indent=2) if args.json else (
            f"Bootstrap complete: {root}\nCreated 17 directories and 20 files.\n"
            "Stopped after S03. Next eligible step: S04 (requires separate authorization)."
        ))
        return 0
    except KitError as exc:
        payload = {"ok": False, "code": exc.code, "message": exc.message}
        print(json.dumps(payload, indent=2) if args.json else f"ERROR [{exc.code}]: {exc.message}", file=sys.stderr)
        return 4 if exc.code in {"post_write_validation", "finalization_failed"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
