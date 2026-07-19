"""Release-candidate paths assembled from the public CLI commands."""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

import daily_review.cli as cli
import daily_review.handoff as handoff
from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-14"
TEXT = (
    "院試の過去問を解いた。研究は少し進めた。明日は院試を進める。明日は研究を進める。"
)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch):
    monkeypatch.setattr(
        handoff,
        "local_now",
        lambda: datetime(2026, 7, 14, 21, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
    )


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _handoff_payload(item, *, day=DAY):
    return {
        "schema_version": "1.0",
        "handoff": {
            "version": "1.0",
            "session_id": item["session_id"],
            "date": day,
            "prompt_hash": item["prompt_hash"],
        },
        "date": day,
        "raw_text": TEXT,
        "today": {
            "main": ["院試の過去問を解いた"],
            "completed": ["院試の過去問を解いた"],
            "partial": [],
            "not_completed": [],
        },
        "reflection": {
            "good": ["取り組めた"],
            "problems": [],
            "causes": [],
            "change_next": ["朝に始める"],
        },
        "tomorrow": {
            "main": ["院試を進める", "研究を進める"],
            "other_tasks": [],
            "minimum": ["問題文を開く"],
        },
        "journal": [],
        "unclassified": [],
    }


def test_natural_input_and_reflect_resume_e2e(tmp_path, monkeypatch):
    _init(tmp_path)
    assert (
        runner.invoke(
            app, ["input", "--date", DAY, "--text", TEXT, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["organize", "--date", DAY, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["review", "--date", DAY, "--root", str(tmp_path)]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "edit-draft",
                "--date",
                DAY,
                "--set",
                "journal=E2E",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    assert (
        "整理ドラフト: 承認済み"
        in runner.invoke(
            app, ["summary", "--date", DAY, "--root", str(tmp_path)]
        ).output
    )

    resume_root = tmp_path / "resume"
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    paused = runner.invoke(
        app,
        ["reflect", "--date", DAY, "--text", TEXT, "--root", str(resume_root)],
        input="n\n",
    )
    assert paused.exit_code == 0
    resumed = runner.invoke(
        app, ["reflect", "--date", DAY, "--resume", "--yes", "--root", str(resume_root)]
    )
    assert resumed.exit_code == 0, resumed.output


def test_chat_import_and_handoff_receive_e2e_with_safety_rejection(tmp_path):
    chat_root = tmp_path / "chat-import"
    _init(chat_root)
    direct_payload = {
        "schema_version": "1.0",
        "date": DAY,
        "raw_text": TEXT,
        "today": {
            "main": ["院試の過去問を解いた"],
            "completed": ["院試の過去問を解いた"],
            "partial": [],
            "not_completed": [],
        },
        "reflection": {
            "good": ["取り組めた"],
            "problems": [],
            "causes": [],
            "change_next": ["朝に始める"],
        },
        "tomorrow": {
            "main": ["研究を進める"],
            "other_tasks": [],
            "minimum": ["資料を開く"],
        },
        "journal": [],
        "unclassified": [],
    }
    imported = runner.invoke(
        app,
        [
            "chat-import",
            "--json-text",
            json.dumps(direct_payload, ensure_ascii=False),
            "--root",
            str(chat_root),
        ],
    )
    assert imported.exit_code == 0, imported.output
    assert (
        runner.invoke(
            app, ["review", "--date", DAY, "--root", str(chat_root)]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve", "--date", DAY, "--yes", "--root", str(chat_root)]
        ).exit_code
        == 0
    )

    _init(tmp_path)
    assert (
        runner.invoke(
            app, ["chat-prompt", "--date", DAY, "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    issue = runner.invoke(app, ["handoff", "--date", DAY, "--root", str(tmp_path)])
    assert issue.exit_code == 0, issue.output
    item = json.loads(
        (tmp_path / "data" / "handoffs" / f"{DAY}.json").read_text(encoding="utf-8")
    )["handoffs"][0]
    payload = _handoff_payload(item)
    received = runner.invoke(
        app,
        [
            "receive",
            "--json-text",
            json.dumps(payload, ensure_ascii=False),
            "--root",
            str(tmp_path),
        ],
    )
    assert received.exit_code == 0, received.output
    assert (
        runner.invoke(
            app,
            [
                "edit-draft",
                "--date",
                DAY,
                "--set",
                "journal=受信済み",
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["approve", "--date", DAY, "--yes", "--root", str(tmp_path)]
        ).exit_code
        == 0
    )
    duplicate = runner.invoke(
        app,
        [
            "receive",
            "--json-text",
            json.dumps(payload, ensure_ascii=False),
            "--root",
            str(tmp_path),
        ],
    )
    assert duplicate.exit_code == 3
    assert "すでに" in duplicate.output or "承認済み" in duplicate.output
