from __future__ import annotations

import hashlib
import json

from typer.testing import CliRunner

from daily_review.chat_schema import validate_payload
from daily_review.cli import app


runner = CliRunner()


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v10_data_migration_preserves_daily_weekly_monthly_and_logs(tmp_path):
    files = {
        "data/daily/2026-07-10.json": {"date": "2026-07-10", "raw_log": "v1.0"},
        "data/weekly/2026-07-07_2026-07-13.json": {
            "start_date": "2026-07-07",
            "end_date": "2026-07-13",
        },
        "data/monthly/2026-07.json": {"month": "2026-07"},
    }
    hashes = {}
    for relative, payload in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        hashes[relative] = _sha256(path)
    log = tmp_path / "logs" / "2026-07-10.md"
    log.parent.mkdir(parents=True)
    log.write_text("legacy log\n", encoding="utf-8")
    hashes["logs/2026-07-10.md"] = _sha256(log)

    first = runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)])
    second = runner.invoke(app, ["migrate", "--yes", "--root", str(tmp_path)])
    assert first.exit_code == second.exit_code == 0
    assert "変更はありません" in second.output
    for relative, digest in hashes.items():
        assert _sha256(tmp_path / relative) == digest
    history = json.loads(
        (tmp_path / "data" / "migrations.json").read_text(encoding="utf-8")
    )
    assert [item["id"] for item in history["migrations"]].count("v1.1-base") == 1


def test_schema_accepts_handoff_and_legacy_payload_without_handoff():
    payload = {
        "schema_version": "1.0",
        "date": "2026-07-14",
        "raw_text": "原文",
        "today": {"main": [], "completed": [], "partial": [], "not_completed": []},
        "reflection": {"good": [], "problems": [], "causes": [], "change_next": []},
        "tomorrow": {"main": [], "other_tasks": [], "minimum": []},
        "journal": [],
        "unclassified": [],
    }
    validated, warnings = validate_payload(payload)
    assert not warnings and "handoff" not in validated
    payload["handoff"] = {
        "version": "1.0",
        "session_id": "dr-20260714-test",
        "date": "2026-07-14",
        "prompt_hash": "sha256:test",
    }
    validated, _ = validate_payload(payload)
    assert validated["handoff"]["session_id"] == "dr-20260714-test"
