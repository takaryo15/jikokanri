from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app
from daily_review.session import save_prompt_session


runner = CliRunner()
DAY = "2026-07-14"


def _payload(day: str = DAY) -> dict:
    return {
        "schema_version": "1.0",
        "date": day,
        "raw_text": "今日は院試を進めた。明日は研究を進める。",
        "today": {
            "main": ["院試を進めた"],
            "completed": ["院試を進めた"],
            "partial": [],
            "not_completed": [],
        },
        "reflection": {
            "good": ["集中できた"],
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


def _init(root):
    assert runner.invoke(app, ["init", "--root", str(root)]).exit_code == 0


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _source_file(root, payload=None):
    path = root / "chat.json"
    path.write_text(
        json.dumps(payload or _payload(), ensure_ascii=False), encoding="utf-8"
    )
    return path


def test_chat_prompt_only_is_read_only_and_includes_context(tmp_path):
    _init(tmp_path)
    previous = {
        "date": "2026-07-13",
        "tomorrow_plan_final": {
            "target_date": DAY,
            "main": ["院試の過去問を2問解く"],
            "tasks": [
                {
                    "id": "task-1",
                    "area": "院試",
                    "task": "院試の過去問を2問解く",
                    "minimum_line": "問題を1問開く",
                }
            ],
        },
        "task_results": [],
    }
    _write_json(tmp_path / "data" / "daily" / "2026-07-13.json", previous)
    _write_json(
        tmp_path / "data" / "daily" / "2026-07-15.json",
        {
            "date": "2026-07-15",
            "tomorrow_plan_proposal": {"tasks": [{"minimum_line": "翌日の最低ライン"}]},
        },
    )
    result = runner.invoke(
        app, ["chat", "--date", DAY, "--prompt-only", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert f"対象日: {DAY}" in result.output
    assert "1. 院試" in result.output
    assert "前日からの引き継ぎ:" in result.output
    assert "今日の未完了タスク:" in result.output
    assert "今週の最低ライン:" in result.output
    assert not list((tmp_path / "data" / "sessions").glob("*.json"))


def test_chat_prompt_copy_and_copy_failure_keep_display(tmp_path, monkeypatch):
    _init(tmp_path)
    copied: dict[str, str] = {}
    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: copied.setdefault("prompt", kwargs["input"])
        or SimpleNamespace(stdout=""),
    )
    copied_result = runner.invoke(
        app,
        [
            "chat",
            "--date",
            DAY,
            "--prompt-only",
            "--copy-prompt",
            "--root",
            str(tmp_path),
        ],
    )
    assert copied_result.exit_code == 0
    assert copied["prompt"]
    assert "コピーしました" in copied_result.output

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("clipboard")),
    )
    failed = runner.invoke(
        app,
        [
            "chat",
            "--date",
            DAY,
            "--prompt-only",
            "--copy-prompt",
            "--root",
            str(tmp_path),
        ],
    )
    assert failed.exit_code == 0
    assert "WARN: コピーできなかった" in failed.output
    assert "schema_version" in failed.output


def test_chat_import_only_file_json_text_and_stdin_create_drafts_and_sessions(
    tmp_path, monkeypatch
):
    for name, args in (
        ("file", ["--file", str(_source_file(tmp_path, _payload()))]),
        (
            "text",
            ["--json-text", json.dumps(_payload("2026-07-15"), ensure_ascii=False)],
        ),
    ):
        root = tmp_path / name
        _init(root)
        if name == "file":
            source = _source_file(root)
            args = ["--file", str(source)]
        result = runner.invoke(
            app,
            [
                "chat",
                "--import-only",
                "--date",
                "2026-07-14" if name == "file" else "2026-07-15",
                *args,
                "--root",
                str(root),
            ],
        )
        assert result.exit_code == 0, result.output
        assert (
            root
            / "data"
            / "drafts"
            / f"{'2026-07-14' if name == 'file' else '2026-07-15'}.json"
        ).is_file()
        session = json.loads(
            next((root / "data" / "sessions").glob("*.json")).read_text(
                encoding="utf-8"
            )
        )
        assert session["status"] == "draft"

    stdin_root = tmp_path / "stdin"
    _init(stdin_root)
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: True)
    stdin = runner.invoke(
        app,
        ["chat", "--import-only", "--date", "2026-07-14", "--root", str(stdin_root)],
        input=json.dumps(_payload(), ensure_ascii=False),
    )
    assert stdin.exit_code == 0, stdin.output
    assert (stdin_root / "data" / "drafts" / f"{DAY}.json").is_file()


def test_chat_interactive_paste_retains_draft_and_resume_can_continue(
    tmp_path, monkeypatch
):
    _init(tmp_path)
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    entered = json.dumps(_payload(), ensure_ascii=False, indent=2)
    result = runner.invoke(
        app,
        ["chat", "--date", DAY, "--root", str(tmp_path)],
        input=f"n\np\n{entered}\n__END__\nn\n",
    )
    assert result.exit_code == 0, result.output
    assert "未承認ドラフトとして保存しました" in result.output
    resume = runner.invoke(
        app, ["chat", "--date", DAY, "--resume", "--root", str(tmp_path)], input="n\n"
    )
    assert resume.exit_code == 0, resume.output
    assert "この内容を確定しますか？" in resume.output


def test_chat_detects_unapproved_draft_and_new_choice_never_overwrites_without_import(
    tmp_path, monkeypatch
):
    _init(tmp_path)
    source = _source_file(tmp_path)
    assert (
        runner.invoke(
            app,
            [
                "chat",
                "--date",
                DAY,
                "--import-only",
                "--file",
                str(source),
                "--root",
                str(tmp_path),
            ],
        ).exit_code
        == 0
    )
    original = (tmp_path / "data" / "drafts" / f"{DAY}.json").read_text(
        encoding="utf-8"
    )
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    result = runner.invoke(
        app, ["chat", "--date", DAY, "--root", str(tmp_path)], input="n\nn\nq\n"
    )
    assert result.exit_code == 0, result.output
    assert "未承認のドラフトがあります" in result.output
    assert (tmp_path / "data" / "drafts" / f"{DAY}.json").read_text(
        encoding="utf-8"
    ) == original


def test_chat_yes_approval_completion_and_daily_only_protection(tmp_path):
    _init(tmp_path)
    source = _source_file(tmp_path)
    approved = runner.invoke(
        app,
        [
            "chat",
            "--date",
            DAY,
            "--import-only",
            "--file",
            str(source),
            "--yes",
            "--root",
            str(tmp_path),
        ],
    )
    assert approved.exit_code == 0, approved.output
    assert "今日の振り返りを保存しました" in approved.output
    assert (tmp_path / "data" / "daily" / f"{DAY}.json").is_file()
    completed = runner.invoke(app, ["chat", "--date", DAY, "--root", str(tmp_path)])
    assert completed.exit_code == 0
    assert "すでに完了" in completed.output

    inconsistent = tmp_path / "inconsistent"
    _init(inconsistent)
    _write_json(inconsistent / "data" / "daily" / f"{DAY}.json", {"date": DAY})
    bad = runner.invoke(app, ["chat", "--date", DAY, "--root", str(inconsistent)])
    assert bad.exit_code == 3
    assert "承認状態を確認できません" in bad.output


def test_chat_sessions_home_doctor_and_release_check(tmp_path):
    _init(tmp_path)
    save_prompt_session(tmp_path, DAY, "prompt")
    home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    release = runner.invoke(app, ["release-check", "--root", str(tmp_path)])
    assert home.exit_code == doctor.exit_code == release.exit_code == 0
    assert f"daily-review chat --date {DAY} --import-only --clipboard" in home.output
    assert "状態: ChatGPTからのJSON待ち" in home.output
    assert "OK   chat sessions" in doctor.output
    assert "OK   priorities config" in doctor.output
    assert "OK   chat workflow" in release.output


def test_chat_corrupt_session_does_not_modify_daily_data(tmp_path):
    _init(tmp_path)
    session = tmp_path / "data" / "sessions" / f"{DAY}.json"
    session.write_text("{", encoding="utf-8")
    source = _source_file(tmp_path)
    result = runner.invoke(
        app,
        [
            "chat",
            "--date",
            DAY,
            "--import-only",
            "--file",
            str(source),
            "--root",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert session.read_text(encoding="utf-8") == "{"
    assert not (tmp_path / "data" / "daily" / f"{DAY}.json").exists()
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert "chat sessionを読み込めません" in doctor.output
