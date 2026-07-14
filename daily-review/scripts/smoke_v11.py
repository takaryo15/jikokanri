#!/usr/bin/env python3
"""Run the v1.1 handoff path in an isolated, disposable workspace."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


DAY = "2026-07-14"


def run(root: Path, *args: str) -> None:
    command = ["daily-review", *args, "--root", str(root)]
    print("$", " ".join(command), flush=True)
    completed = subprocess.run(command, text=True, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    requested = os.environ.get("DAILY_REVIEW_ROOT")
    if requested:
        root = Path(requested).expanduser().resolve()
        if root.exists():
            raise SystemExit(f"ERROR: smoke test root already exists: {root}")
        root.mkdir(parents=True)
    else:
        root = Path(tempfile.mkdtemp(prefix="daily-review-v11-smoke-"))
    try:
        run(root, "init")
        run(root, "migrate", "--yes")
        run(root, "handoff", "--date", DAY)
        handoff_path = root / "data" / "handoffs" / f"{DAY}.json"
        item = json.loads(handoff_path.read_text(encoding="utf-8"))["handoffs"][0]
        response = {
            "schema_version": "1.0",
            "handoff": {"version": "1.0", "session_id": item["session_id"], "date": DAY, "prompt_hash": item["prompt_hash"]},
            "date": DAY,
            "raw_text": "院試の過去問を解いた。明日は研究を進める。",
            "today": {"main": ["院試の過去問を解いた"], "completed": ["院試の過去問を解いた"], "partial": [], "not_completed": []},
            "reflection": {"good": ["集中できた"], "problems": [], "causes": [], "change_next": ["朝に始める"]},
            "tomorrow": {"main": ["研究を進める"], "other_tasks": [], "minimum": ["資料を開く"]},
            "journal": [],
            "unclassified": [],
        }
        response_path = root / "response.json"
        response_path.write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
        run(root, "receive", "--file", str(response_path), "--yes")
        run(root, "summary", "--date", DAY)
        run(root, "doctor")
        run(root, "release-check")
        run(root, "v11-check")
        print("daily-review v1.1 smoke test: OK")
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
