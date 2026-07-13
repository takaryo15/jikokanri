from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()


def _write_daily(tmp_path, day, proposal=None, final=None):
    path = tmp_path / "data" / "daily"
    path.mkdir(parents=True, exist_ok=True)
    payload = {
        "date": day,
        "created_at": "2026-07-13T22:00:00+09:00",
        "updated_at": "2026-07-13T22:00:00+09:00",
    }
    if proposal:
        payload["tomorrow_plan_proposal"] = proposal
    if final:
        payload["tomorrow_plan_final"] = final
    (path / f"{day}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _plan(status="approved"):
    return {
        "status": status,
        "target_date": "2026-07-14",
        "main": ["院試"],
        "tasks": [{"area": "院試", "task": "過去問", "priority": 1, "minimum_line": "問題文だけ読む"}],
        "one_change_tomorrow": "朝イチで過去問を開く",
        "approved_at": "2026-07-13T22:30:00+09:00" if status == "approved" else None,
    }


def test_today_searches_by_target_date_not_source_date(tmp_path):
    _write_daily(tmp_path, "2026-07-13", final=_plan())
    result = runner.invoke(app, ["today", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "今日の指示書｜2026-07-14" in result.output
    assert "過去問" in result.output


def test_today_reports_pending_when_only_proposal_exists(tmp_path):
    _write_daily(tmp_path, "2026-07-13", proposal=_plan(status="pending_review"))
    result = runner.invoke(app, ["today", "--date", "2026-07-14", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "提案版のみ" in result.output
    assert "未承認" in result.output
