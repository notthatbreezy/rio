#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


PACKAGE = Path(__file__).resolve().parent.parent


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run an isolated fictional bootstrap, or validate manually generated output."
    )
    result.add_argument(
        "--manual-root",
        type=Path,
        help="validate an existing assistant-produced starter root instead of generating a fixture",
    )
    return result


def run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, text=True, capture_output=True)
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.manual_root is not None:
        run([
            sys.executable,
            str(PACKAGE / "scripts" / "validate.py"),
            "--root",
            str(args.manual_root),
            "--check-reference",
        ])
        return 0

    with tempfile.TemporaryDirectory(prefix="second-brain-kit-") as temporary:
        # Test infrastructure may expose a temporary base through an OS alias
        # (for example /var -> /private/var). Canonicalize only this harness-owned root.
        workspace = Path(temporary).resolve()
        root = workspace / "sample root with spaces"
        answers_path = workspace / "confirmed answers.json"
        fixture = json.loads(
            (PACKAGE / "fixtures" / "sample-answers.json").read_text(encoding="utf-8")
        )
        fixture["workspace"]["destination"] = str(root)
        fixture["confirmed"] = True
        answers_path.write_text(json.dumps(fixture, indent=2), encoding="utf-8")
        run([
            sys.executable,
            str(PACKAGE / "scripts" / "bootstrap.py"),
            "--answers",
            str(answers_path),
            "--root",
            str(root),
            "--json",
        ])
        run([
            sys.executable,
            str(PACKAGE / "scripts" / "validate.py"),
            "--root",
            str(root),
            "--check-reference",
            "--json",
        ])
    print("Harness passed; its temporary fixture tree was removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
