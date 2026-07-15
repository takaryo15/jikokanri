from __future__ import annotations

import json

from typer.testing import CliRunner

from daily_review.cli import app


runner = CliRunner()
DAY = "2026-07-15"


def _request():
    return {
        "version": "1",
        "request_id": "req-cli",
        "idempotency_key": "cli-review",
        "mode": "preview",
        "effective_date": DAY,
        "source": "test",
        "commands": [
            {"type": "create_task", "payload": {"title": "CLIタスク", "due_date": DAY}}
        ],
    }


def test_execute_file_stdin_pretty_commit_and_json_only(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(_request(), ensure_ascii=False), encoding="utf-8")
    preview = runner.invoke(
        app,
        ["api", "execute", "--input", str(path), "--pretty", "--root", str(tmp_path)],
    )
    assert preview.exit_code == 0
    preview_json = json.loads(preview.stdout)
    assert preview_json["status"] == "preview_ready"
    assert preview.stdout.lstrip().startswith("{") and preview.stdout.rstrip().endswith(
        "}"
    )

    commit_payload = _request() | {
        "mode": "commit",
        "confirmation_token": preview_json["confirmation_token"],
    }
    commit = runner.invoke(
        app,
        ["api", "execute", "--stdin", "--root", str(tmp_path)],
        input=json.dumps(commit_payload, ensure_ascii=False),
    )
    assert commit.exit_code == 0 and json.loads(commit.stdout)["status"] == "committed"
    assert (
        len(
            json.loads((tmp_path / "data/api/tasks.json").read_text(encoding="utf-8"))[
                "tasks"
            ]
        )
        == 1
    )


def test_invalid_json_empty_stdin_conflict_and_exit_codes(tmp_path):
    invalid = runner.invoke(
        app, ["api", "execute", "--stdin", "--root", str(tmp_path)], input="{"
    )
    empty = runner.invoke(
        app, ["api", "execute", "--stdin", "--root", str(tmp_path)], input=""
    )
    assert invalid.exit_code == empty.exit_code == 2
    assert json.loads(invalid.stdout)["errors"][0]["code"] == "INVALID_REQUEST"

    request = _request()
    first = runner.invoke(
        app,
        ["api", "execute", "--stdin", "--root", str(tmp_path)],
        input=json.dumps(request),
    )
    assert first.exit_code == 0
    changed = request | {
        "commands": [{"type": "create_task", "payload": {"title": "変更"}}]
    }
    conflict = runner.invoke(
        app,
        ["api", "execute", "--stdin", "--root", str(tmp_path)],
        input=json.dumps(changed),
    )
    assert conflict.exit_code == 4
    assert json.loads(conflict.stdout)["errors"][0]["code"] == "IDEMPOTENCY_CONFLICT"


def test_schema_history_and_command_schema(tmp_path):
    request_schema = runner.invoke(
        app, ["api", "schema", "--type", "request", "--compact"]
    )
    response_schema = runner.invoke(
        app, ["api", "schema", "--type", "response", "--compact"]
    )
    command_schema = runner.invoke(
        app, ["api", "schema", "--command", "create_daily_review", "--compact"]
    )
    for result in (request_schema, response_schema, command_schema):
        assert result.exit_code == 0
        assert isinstance(json.loads(result.stdout), dict)
    assert "commands" in json.loads(request_schema.stdout)["properties"]

    runner.invoke(
        app,
        ["api", "execute", "--stdin", "--root", str(tmp_path)],
        input=json.dumps(_request()),
    )
    history = runner.invoke(
        app, ["api", "history", "--date", DAY, "--pretty", "--root", str(tmp_path)]
    )
    records = json.loads(history.stdout)["records"]
    assert history.exit_code == 0 and records
    assert "raw_input" not in records[0]
    assert records == sorted(
        records, key=lambda item: (item["executed_at"], item["audit_id"])
    )


def test_parse_only_preview_commit_and_unclassified_preserved(tmp_path):
    text = "分類不能\n今日できたこと\n- 開発\n明日やること\n- 院試\n最低限\n- 1問"
    parsed = runner.invoke(
        app, ["parse", "review", "--text", text, "--date", DAY, "--pretty"]
    )
    assert parsed.exit_code == 0
    assert json.loads(parsed.stdout)["normalized"]["done"] == ["開発"]

    preview = runner.invoke(
        app,
        [
            "parse",
            "review",
            "--text",
            text,
            "--date",
            DAY,
            "--preview",
            "--idempotency-key",
            "parse-cli",
            "--root",
            str(tmp_path),
        ],
    )
    preview_json = json.loads(preview.stdout)
    assert preview.exit_code == 0 and not (tmp_path / f"data/daily/{DAY}.json").exists()
    commit = runner.invoke(
        app,
        [
            "parse",
            "review",
            "--text",
            text,
            "--date",
            DAY,
            "--commit",
            "--idempotency-key",
            "parse-cli",
            "--confirmation-token",
            preview_json["confirmation_token"],
            "--root",
            str(tmp_path),
        ],
    )
    assert commit.exit_code == 0
    entry = json.loads(
        (tmp_path / f"data/daily/{DAY}.json").read_text(encoding="utf-8")
    )
    assert entry["api_review"]["unclassified"] == ["分類不能"]
    assert entry["raw_log"] == text
