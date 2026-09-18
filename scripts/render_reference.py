#!/usr/bin/env python3
from pathlib import Path

from kit import PACKAGE_ROOT, load_tasks, render_task_reference


def main() -> int:
    target = PACKAGE_ROOT / "references" / "setup-checklist.md"
    target.write_text(render_task_reference(load_tasks()), encoding="utf-8", newline="\n")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
