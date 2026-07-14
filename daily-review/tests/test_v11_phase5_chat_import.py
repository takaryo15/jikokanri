from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

import daily_review.cli as cli
from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-14"


def _payload(**overrides):
    value = {
        "schema_version": "1.0",
        "date": DAY,
        "raw_text": "今日は院試を2問進めた。\n明日は研究も進める。",
        "today": {"main": ["院試の過去問を2問解く"], "completed": ["院試を2問進めた"], "partial": [], "not_completed": []},
        "reflection": {"good": ["院試を進めた"], "problems": [], "causes": [], "change_next": ["朝に過去問を開く"]},
        "tomorrow": {"main": ["研究を進める"], "other_tasks": [], "minimum": ["資料を開く"]},
        "journal": ["集中して取り組めた"],
        "unclassified": [],
    }
    value.update(overrides)
    return value


def _args(root, payload, *extra):
    return ["chat-import", "--json-text", json.dumps(payload, ensure_ascii=False), *extra, "--root", str(root)]


def _draft(root):
    return json.loads((root / "data" / "drafts" / f"{DAY}.json").read_text(encoding="utf-8"))


def _inbox(root):
    return json.loads((root / "data" / "inbox" / f"{DAY}.json").read_text(encoding="utf-8"))


def test_chat_import_saves_raw_text_and_explicit_draft_mapping(tmp_path):
    result = runner.invoke(app, _args(tmp_path, _payload()))
    assert result.exit_code == 0, result.output
    assert "ChatGPTの構造化入力を取り込みました" in result.output
    entry = _inbox(tmp_path)["entries"][0]
    assert entry["source"] == "chat_import"
    assert entry["raw_text"] == _payload()["raw_text"]
    draft = _draft(tmp_path)
    assert draft["parser_version"] == "chat-schema-1.0"
    assert draft["import_source"] == "chat_import"
    assert draft["import_hash"].startswith("sha256:")
    assert draft["today"]["main_candidates"] == _payload()["today"]["main"]
    assert draft["tomorrow"]["minimum_candidates"] == _payload()["tomorrow"]["minimum"]


def test_chat_import_accepts_one_fenced_json_and_preserves_multiline_japanese(tmp_path):
    raw = "研究はO VIIとO VIIIを確認した。\n明日は院試を進める。"
    payload = _payload(raw_text=raw)
    content = "ここまで整理しました。\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```\n"
    result = runner.invoke(app, ["chat-import", "--json-text", content, "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert _inbox(tmp_path)["entries"][0]["raw_text"] == raw


def test_chat_import_dry_run_and_output_json_do_not_write(tmp_path):
    result = runner.invoke(app, _args(tmp_path, _payload(), "--dry-run", "--output-json"))
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["dry_run"] is True
    assert output["input_saved"] is False
    assert not (tmp_path / "data").exists()


def test_chat_import_rejects_invalid_schema_and_multiple_json_candidates(tmp_path):
    invalid = _payload(today={"main": ["a", "b", "c", "d"], "completed": [], "partial": [], "not_completed": []})
    invalid_result = runner.invoke(app, _args(tmp_path, invalid))
    multi = runner.invoke(app, ["chat-import", "--json-text", json.dumps(_payload()) + "\n" + json.dumps(_payload()), "--root", str(tmp_path)])
    assert invalid_result.exit_code == multi.exit_code == 3
    assert "Mainは最大3件" in invalid_result.output
    assert "JSON候補が複数" in multi.output
    assert not (tmp_path / "data").exists()


def test_chat_import_rejects_invalid_json_version_required_types_and_values(tmp_path):
    no_source = runner.invoke(app, ["chat-import", "--root", str(tmp_path)])
    bad_json = runner.invoke(app, ["chat-import", "--json-text", "{", "--root", str(tmp_path)])
    unsupported = runner.invoke(app, _args(tmp_path, _payload(schema_version="2.0")))
    missing = _payload()
    del missing["journal"]
    missing_result = runner.invoke(app, _args(tmp_path, missing))
    wrong_type = runner.invoke(app, _args(tmp_path, _payload(journal="日記")))
    blank = runner.invoke(app, _args(tmp_path, _payload(journal=["  "])))
    duplicate = runner.invoke(app, _args(tmp_path, _payload(journal=["同じ", "同じ"])))
    assert no_source.exit_code == 2
    assert "ChatGPT連携用JSONを指定してください" in no_source.output
    assert bad_json.exit_code == unsupported.exit_code == missing_result.exit_code == wrong_type.exit_code == blank.exit_code == duplicate.exit_code == 3
    assert "JSONを抽出できません" in bad_json.output
    assert "未対応のschema_versionです: 2.0" in unsupported.output
    assert "必須フィールド" in missing_result.output
    assert "文字列の配列" in wrong_type.output
    assert "空白だけ" in blank.output
    assert "重複値" in duplicate.output


def test_chat_import_rejects_date_mismatch_large_input_and_duplicate_import(tmp_path):
    mismatch = runner.invoke(app, _args(tmp_path, _payload(), "--date", "2026-07-15"))
    huge = runner.invoke(app, _args(tmp_path / "huge", _payload(raw_text="あ" * 100_001)))
    first = runner.invoke(app, _args(tmp_path / "duplicate", _payload()))
    second = runner.invoke(app, _args(tmp_path / "duplicate", _payload()))
    assert mismatch.exit_code == 2
    assert "日付が一致しません" in mismatch.output
    assert huge.exit_code == 3
    assert "上限" in huge.output
    assert first.exit_code == 0
    assert second.exit_code == 2
    assert "すでに取り込み済み" in second.output


def test_chat_import_warnings_are_not_errors_but_block_yes(tmp_path):
    value = _payload(tommorow={"main": []})
    normal = runner.invoke(app, _args(tmp_path, value))
    assert normal.exit_code == 0, normal.output
    assert "WARN: unknown field: tommorow" in normal.output

    other = tmp_path / "yes"
    yes = runner.invoke(app, _args(other, value, "--yes"))
    assert yes.exit_code == 2
    assert "自動承認できません" in yes.output
    assert _draft(other)["status"] == "draft"


def test_chat_import_force_backs_up_unapproved_draft_and_never_replaces_daily(tmp_path):
    first = runner.invoke(app, _args(tmp_path, _payload()))
    replacement = _payload(raw_text="別の原文", today={"main": ["研究"], "completed": [], "partial": ["研究を少し進めた"], "not_completed": []})
    blocked = runner.invoke(app, _args(tmp_path, replacement))
    forced = runner.invoke(app, _args(tmp_path, replacement, "--force"))
    assert first.exit_code == 0
    assert blocked.exit_code == 2
    assert forced.exit_code == 0, forced.output
    assert list((tmp_path / "data" / "backups" / "drafts").glob(f"{DAY}_*.json"))
    assert _inbox(tmp_path)["entries"][0]["raw_text"] == _payload()["raw_text"]
    assert _inbox(tmp_path)["entries"][1]["raw_text"] == "別の原文"


def test_chat_import_yes_approves_only_safe_payload(tmp_path):
    result = runner.invoke(app, _args(tmp_path, _payload(), "--yes"))
    assert result.exit_code == 0, result.output
    assert _draft(tmp_path)["status"] == "approved"
    assert (tmp_path / "data" / "daily" / f"{DAY}.json").is_file()


def test_chat_import_yes_rejects_unclassified_and_approved_draft_cannot_be_replaced(tmp_path):
    unclassified = runner.invoke(app, _args(tmp_path / "unclassified", _payload(unclassified=["判断不能"]), "--yes"))
    assert unclassified.exit_code == 2
    assert "未分類" in unclassified.output

    approved_root = tmp_path / "approved"
    assert runner.invoke(app, _args(approved_root, _payload(), "--yes")).exit_code == 0
    rejected = runner.invoke(app, _args(approved_root, _payload(raw_text="別の原文"), "--force"))
    assert rejected.exit_code == 2
    assert "承認済み" in rejected.output


def test_chat_import_approve_reuses_resume_confirmation_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    result = runner.invoke(app, _args(tmp_path, _payload(), "--approve"), input="n\n")
    assert result.exit_code == 0, result.output
    assert "この内容を確定しますか？" in result.output
    assert "確定せず終了しました" in result.output
    assert _draft(tmp_path)["status"] == "draft"


def test_chat_import_stdin_file_and_corrupt_inbox_are_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: True)
    stdin = runner.invoke(app, ["chat-import", "--root", str(tmp_path)], input=json.dumps(_payload(), ensure_ascii=False))
    assert stdin.exit_code == 0, stdin.output

    monkeypatch.setattr(cli, "_stdin_is_piped", lambda: False)
    source = tmp_path / "input.json"
    source.write_text(json.dumps(_payload(raw_text="別日内容"), ensure_ascii=False), encoding="utf-8")
    corrupt_root = tmp_path / "corrupt"
    inbox = corrupt_root / "data" / "inbox" / f"{DAY}.json"
    inbox.parent.mkdir(parents=True)
    inbox.write_text("{", encoding="utf-8")
    corrupt = runner.invoke(app, ["chat-import", "--file", str(source), "--root", str(corrupt_root)])
    assert corrupt.exit_code == 4
    assert inbox.read_text(encoding="utf-8") == "{"


def test_chat_prompt_doctor_and_home_surface_chat_import(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    prompt = runner.invoke(app, ["chat-prompt", "--root", str(tmp_path)])
    doctor = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    home = runner.invoke(app, ["home", "--date", DAY, "--root", str(tmp_path)])
    assert prompt.exit_code == doctor.exit_code == home.exit_code == 0
    assert '"schema_version": "1.0"' in prompt.output
    assert "OK   chat import schema" in doctor.output
    assert "OK   chat import prompt" in doctor.output
    assert f"daily-review chat --date {DAY}" in home.output


def test_doctor_reports_chat_prompt_schema_mismatch(tmp_path):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    prompt = tmp_path / "templates" / "chat_import_prompt.md"
    prompt.write_text("schema_version: 2.0", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "chat import promptのschema_versionまたは必須変数が不足しています" in result.output


def test_chat_prompt_date_and_clipboard_and_file_dry_run(tmp_path, monkeypatch):
    assert runner.invoke(app, ["init", "--root", str(tmp_path)]).exit_code == 0
    dated = runner.invoke(app, ["chat-prompt", "--date", DAY, "--root", str(tmp_path)])
    copied: dict[str, str] = {}

    def copy_prompt(*args, **kwargs):
        copied["text"] = kwargs["input"]
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(cli.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(cli.subprocess, "run", copy_prompt)
    copied_result = runner.invoke(app, ["chat-prompt", "--clipboard", "--root", str(tmp_path)])
    source = tmp_path / "reflection.json"
    source.write_text(json.dumps(_payload(), ensure_ascii=False), encoding="utf-8")
    file_dry = runner.invoke(app, ["chat-import", "--file", str(source), "--dry-run", "--root", str(tmp_path)])
    assert dated.exit_code == copied_result.exit_code == file_dry.exit_code == 0
    assert DAY in dated.output
    assert copied["text"]
    assert "クリップボードへコピーしました" in copied_result.output
    assert "保存は行いませんでした" in file_dry.output
