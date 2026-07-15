from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

import daily_review.cli as cli
import daily_review.handoff as handoff
from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-14"


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(handoff, "local_now", lambda: datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo")))


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _payload(item, *, day=DAY, raw_text="今日は院試を進めた。", **overrides):
    value = {
        "schema_version": "1.0",
        "handoff": {"version": "1.0", "session_id": item["session_id"], "date": day, "prompt_hash": item["prompt_hash"]},
        "date": day,
        "raw_text": raw_text,
        "today": {"main": ["院試を進めた"], "completed": ["院試を進めた"], "partial": [], "not_completed": []},
        "reflection": {"good": ["集中できた"], "problems": [], "causes": [], "change_next": ["朝に始める"]},
        "tomorrow": {"main": ["研究を進める"], "other_tasks": [], "minimum": ["資料を開く"]},
        "journal": [],
        "unclassified": [],
    }
    value.update(overrides)
    return value


def _issue(root, *extra):
    result = runner.invoke(app, ["handoff", "--date", DAY, *extra, "--root", str(root)])
    assert result.exit_code == 0, result.output
    value = json.loads((root / "data" / "handoffs" / f"{DAY}.json").read_text(encoding="utf-8"))
    return value["handoffs"][-1], result


def _receive_args(root, payload, *extra):
    return ["receive", "--date", DAY, "--json-text", json.dumps(payload, ensure_ascii=False), *extra, "--root", str(root)]


def test_handoff_generates_unique_package_metadata_output_and_copy(tmp_path, monkeypatch):
    _init(tmp_path)
    output = tmp_path / "handoff.txt"
    first, result = _issue(tmp_path, "--output", str(output))
    second, _ = _issue(tmp_path)
    assert first["session_id"] != second["session_id"]
    assert first["prompt_hash"].startswith("sha256:")
    assert first["expires_at"].startswith("2026-07-15T05:00:00+09:00")
    assert f"session_id: {first['session_id']}" in result.output
    assert output.read_text(encoding="utf-8").startswith("===== DAILY-REVIEW HANDOFF BEGIN =====")

    copied: dict[str, str] = {}
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: copied.setdefault("text", kwargs["input"]) or SimpleNamespace(stdout=""))
    copied_item, copied_result = _issue(tmp_path, "--copy")
    assert copied_item["session_id"] in copied["text"]
    assert "クリップボードへコピーしました" in copied_result.output


def test_receive_valid_markdown_saves_inbox_draft_and_handoff_state(tmp_path):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    content = "回答です。\n```json\n" + json.dumps(_payload(item), ensure_ascii=False) + "\n```"
    result = runner.invoke(app, ["receive", "--json-text", content, "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    inbox = json.loads((tmp_path / "data" / "inbox" / f"{DAY}.json").read_text(encoding="utf-8"))
    handoffs = json.loads((tmp_path / "data" / "handoffs" / f"{DAY}.json").read_text(encoding="utf-8"))["handoffs"]
    assert inbox["entries"][0]["source"] == "chat_import"
    assert (tmp_path / "data" / "drafts" / f"{DAY}.json").is_file()
    assert handoffs[0]["status"] == "received"
    assert handoffs[0]["import_hash"].startswith("sha256:")
    assert "daily-review chat --date 2026-07-14 --resume" in result.output


def test_receive_rejects_mismatches_missing_cancelled_and_duplicate(tmp_path):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    wrong_session = _payload(item)
    wrong_session["handoff"]["session_id"] = "dr-20260714-other"
    wrong_date = _payload(item)
    wrong_date["handoff"]["date"] = "2026-07-15"
    wrong_hash = _payload(item)
    wrong_hash["handoff"]["prompt_hash"] = "sha256:wrong"
    for payload, text in ((wrong_session, "見つかりません"), (wrong_date, "日付が一致"), (wrong_hash, "prompt_hash")):
        result = runner.invoke(app, _receive_args(tmp_path, payload))
        assert result.exit_code == 3
        assert text in result.output
    assert runner.invoke(app, ["handoff-cancel", "--date", DAY, "--session-id", item["session_id"], "--root", str(tmp_path)]).exit_code == 0
    cancelled = runner.invoke(app, _receive_args(tmp_path, _payload(item)))
    assert cancelled.exit_code == 3
    assert "キャンセル済み" in cancelled.output


def test_receive_expiry_allow_expired_and_force_backup(tmp_path):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    path = tmp_path / "data" / "handoffs" / f"{DAY}.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["handoffs"][0]["expires_at"] = "2020-01-01T05:00:00+09:00"
    path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
    expired = runner.invoke(app, _receive_args(tmp_path, _payload(item)))
    allowed = runner.invoke(app, _receive_args(tmp_path, _payload(item), "--allow-expired"))
    assert expired.exit_code == 3
    assert "期限切れ" in expired.output
    assert allowed.exit_code == 0, allowed.output

    replacement = _payload(item, raw_text="別の回答")
    blocked = runner.invoke(app, _receive_args(tmp_path, replacement, "--allow-expired"))
    forced = runner.invoke(app, _receive_args(tmp_path, replacement, "--allow-expired", "--force"))
    assert blocked.exit_code == 3
    assert forced.exit_code == 0, forced.output
    assert list((tmp_path / "data" / "backups" / "drafts").glob(f"{DAY}_*.json"))


def test_receive_yes_approve_list_cancel_and_home_doctor_release(tmp_path):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    before = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert f"daily-review receive --date {DAY} --clipboard" in before.output
    approved = runner.invoke(app, _receive_args(tmp_path, _payload(item), "--yes"))
    assert approved.exit_code == 0, approved.output
    assert (tmp_path / "data" / "daily" / f"{DAY}.json").is_file()
    listing = runner.invoke(app, ["handoff-list", "--date", DAY, "--json", "--root", str(tmp_path)])
    assert json.loads(listing.output)["handoffs"][0]["status"] == "approved"
    cannot_cancel = runner.invoke(app, ["handoff-cancel", "--date", DAY, "--session-id", item["session_id"], "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    release = runner.invoke(app, ["release-check", "--root", str(tmp_path)])
    assert cannot_cancel.exit_code == 3
    assert "承認済み" in cannot_cancel.output
    assert doctor.exit_code == release.exit_code == 0
    assert "OK   handoffs" in doctor.output
    assert "OK   handoff workflow" in release.output


def test_handoff_list_latest_and_receive_dry_run_do_not_write(tmp_path):
    _init(tmp_path)
    first, _ = _issue(tmp_path)
    second, _ = _issue(tmp_path)
    latest = runner.invoke(app, ["handoff-list", "--date", DAY, "--latest", "--json", "--root", str(tmp_path)])
    dry = runner.invoke(app, _receive_args(tmp_path, _payload(second), "--dry-run"))
    assert json.loads(latest.output)["handoffs"][0]["session_id"] == second["session_id"]
    assert dry.exit_code == 0
    assert not (tmp_path / "data" / "inbox" / f"{DAY}.json").exists()
    assert first["status"] == "issued"


def test_receive_approve_reuses_existing_review_flow(tmp_path, monkeypatch):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    result = runner.invoke(app, _receive_args(tmp_path, _payload(item), "--approve"), input="n\n")
    assert result.exit_code == 0, result.output
    assert "この内容を確定しますか？" in result.output
    assert "未承認ドラフトとして保存しました" in result.output


def test_receive_yes_rejects_a_non_issued_handoff(tmp_path):
    _init(tmp_path)
    item, _ = _issue(tmp_path)
    assert runner.invoke(app, _receive_args(tmp_path, _payload(item))).exit_code == 0
    unsafe = runner.invoke(app, _receive_args(tmp_path, _payload(item, raw_text="別の回答"), "--force", "--yes"))
    assert unsafe.exit_code == 2
    assert "自動承認できません" in unsafe.output
    assert not (tmp_path / "data" / "daily" / f"{DAY}.json").exists()
