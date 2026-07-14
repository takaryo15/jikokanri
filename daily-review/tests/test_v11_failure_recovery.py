from __future__ import annotations

import json

from typer.testing import CliRunner

import daily_review.cli as cli
import daily_review.storage as storage
from daily_review.cli import app
from daily_review.storage import atomic_write_json_data


runner = CliRunner()
DAY = "2026-07-14"


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _payload(item):
    return {
        "schema_version": "1.0", "handoff": {"version": "1.0", "session_id": item["session_id"], "date": DAY, "prompt_hash": item["prompt_hash"]},
        "date": DAY, "raw_text": "院試を進めた。明日は研究を進める。",
        "today": {"main": ["院試を進めた"], "completed": ["院試を進めた"], "partial": [], "not_completed": []},
        "reflection": {"good": [], "problems": [], "causes": [], "change_next": []},
        "tomorrow": {"main": ["研究を進める"], "other_tasks": [], "minimum": ["資料を開く"]}, "journal": [], "unclassified": [],
    }


def test_atomic_write_failure_leaves_original_json_and_no_temp_file(tmp_path, monkeypatch):
    path = tmp_path / "data.json"
    path.write_text('{"before": true}\n', encoding="utf-8")
    monkeypatch.setattr(storage.os, "replace", lambda *_args: (_ for _ in ()).throw(OSError("rename failed")))
    try:
        atomic_write_json_data(path, {"after": True})
    except OSError:
        pass
    else:
        raise AssertionError("atomic write should fail")
    assert path.read_text(encoding="utf-8") == '{"before": true}\n'
    assert not list(tmp_path.glob(".data.json.*.tmp"))


def test_receive_clipboard_failure_offers_file_fallback_without_writing(tmp_path, monkeypatch):
    _init(tmp_path)
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("pbpaste missing")))
    result = runner.invoke(app, ["receive", "--date", DAY, "--clipboard", "--root", str(tmp_path)])
    assert result.exit_code == 3
    assert "クリップボードを読み取れません" in result.output
    assert "daily-review receive --file response.json" in result.output
    assert not (tmp_path / "data" / "inbox" / f"{DAY}.json").exists()


def test_receive_rejects_approved_daily_without_changing_inbox_or_draft(tmp_path):
    _init(tmp_path)
    issued = runner.invoke(app, ["handoff", "--date", DAY, "--root", str(tmp_path)])
    assert issued.exit_code == 0, issued.output
    item = json.loads((tmp_path / "data" / "handoffs" / f"{DAY}.json").read_text(encoding="utf-8"))["handoffs"][0]
    daily = tmp_path / "data" / "daily" / f"{DAY}.json"
    daily.write_text(json.dumps({"date": DAY}), encoding="utf-8")
    result = runner.invoke(app, ["receive", "--json-text", json.dumps(_payload(item), ensure_ascii=False), "--root", str(tmp_path)])
    assert result.exit_code == 2
    assert "すでに存在" in result.output
    assert not (tmp_path / "data" / "inbox" / f"{DAY}.json").exists()
    assert not (tmp_path / "data" / "drafts" / f"{DAY}.json").exists()
