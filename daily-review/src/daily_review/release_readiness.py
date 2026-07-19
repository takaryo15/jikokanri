"""Read-only v1.3 release readiness checks."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import __version__
from .archive import create_backup, verify_backup
from .configuration import validate_config
from .doctor import run_doctor
from .migration import (
    TARGET_SCHEMA_VERSION,
    V13_FINAL_MIGRATION_ID,
    apply_migration,
    is_migrated,
)
from .recovery import preview_restore
from .scheduler import load_scheduler_config
from .storage import atomic_write_json_data, init_workspace


REQUIRED_COMMANDS = {
    "home",
    "close-day",
    "tasks",
    "weekly",
    "monthly",
    "backup",
    "restore",
    "rollover",
    "scheduler",
    "flow",
    "doctor",
    "release-check",
    "config",
    "api",
    "setup",
    "onboarding",
    "migrate",
}

REQUIRED_DOCS = (
    "README.md",
    "CHANGELOG.md",
    "RELEASE_CHECKLIST.md",
    "docs/getting-started.md",
    "docs/daily-workflow.md",
    "docs/chatgpt-integration.md",
    "docs/tasks-and-rollover.md",
    "docs/weekly-monthly-reports.md",
    "docs/backup-and-restore.md",
    "docs/scheduler.md",
    "docs/configuration.md",
    "docs/migration-v1.0-to-v1.3.md",
    "docs/troubleshooting.md",
    "docs/security.md",
    "docs/releases/v1.3.0.md",
)


def _git(source_root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def collect_release_readiness(
    source_root: Path,
    runtime_root: Path,
    *,
    command_names: set[str],
    metadata_version: str | None,
) -> dict[str, Any]:
    checks: list[dict[str, str]] = []

    def add(name: str, ok: bool, detail: str = "", *, warning: bool = False) -> None:
        checks.append(
            {
                "name": name,
                "level": "OK" if ok else "WARN" if warning else "ERROR",
                "detail": detail,
            }
        )

    add("package version", __version__ == "1.3.0", __version__)
    add("package metadata", metadata_version == __version__, str(metadata_version))
    add("Python version", sys.version_info >= (3, 11), sys.version.split()[0])
    add("CLI entry point", (source_root / "src/daily_review/cli.py").is_file())
    missing_commands = sorted(REQUIRED_COMMANDS - command_names)
    add("主要CLI", not missing_commands, ", ".join(missing_commands))
    add("migration schema", TARGET_SCHEMA_VERSION == "1.3")
    add("migration definition", V13_FINAL_MIGRATION_ID == "v1.3-final")
    for module in (
        "archive.py",
        "recovery.py",
        "rollover.py",
        "operation_lock.py",
        "command_api.py",
        "scheduler.py",
        "scheduler_install.py",
        "operational_flows.py",
        "configuration.py",
    ):
        add(f"module {module}", (source_root / "src/daily_review" / module).is_file())
    capability_files = {
        "chat workflow": "chat_workflow.py",
        "handoff workflow": "handoff.py",
        "goal commands": "goals.py",
        "weekly and monthly evaluations": "evaluation.py",
        "backup and restore": "recovery.py",
        "rollover": "rollover.py",
        "scheduler": "scheduler.py",
        "Command API": "command_api.py",
    }
    for name, module in capability_files.items():
        add(name, (source_root / "src/daily_review" / module).is_file())
    missing_docs = [
        name for name in REQUIRED_DOCS if not (source_root / name).is_file()
    ]
    add("documentation", not missing_docs, ", ".join(missing_docs))
    gitignore = source_root / ".gitignore"
    ignored = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
    required_ignores = ("data/", "logs/", "backups/", "config/app.json", "exports/")
    add(
        "runtime data ignored",
        all(item in ignored for item in required_ignores),
    )
    tracked = _git(
        source_root,
        "ls-files",
        "data",
        "logs",
        "backups",
        "runtime",
        "state",
    )
    add("Git tracked runtime data", not tracked.stdout.strip(), tracked.stdout.strip())
    status = _git(source_root, "status", "--porcelain")
    add(
        "git working tree",
        not status.stdout.strip(),
        "未commitの変更があります" if status.stdout.strip() else "",
        warning=True,
    )
    branch = _git(source_root, "branch", "--show-current").stdout.strip()
    add("git branch", branch == "main", branch, warning=branch != "main")
    tags = _git(source_root, "tag", "--list", "v1.3.0").stdout.strip()
    add(
        "v1.3.0 tag",
        bool(tags),
        "tagは最終検証後に作成します" if not tags else tags,
        warning=True,
    )

    config_result = validate_config(runtime_root)
    add(
        "config",
        config_result["valid"],
        "; ".join(config_result["errors"]),
    )
    try:
        scheduler = load_scheduler_config(runtime_root)
    except (OSError, ValueError) as exc:
        add("scheduler", False, str(exc))
    else:
        add(
            "scheduler",
            True,
            "enabled" if scheduler["enabled"] else "disabled (safe default)",
        )
    initialized = (runtime_root / "templates").is_dir() or (
        runtime_root / "data"
    ).is_dir()
    if initialized:
        report = run_doctor(runtime_root)
        doctor_errors = [item for item in report["issues"] if item["level"] == "ERROR"]
        add(
            "doctor",
            not doctor_errors,
            "; ".join(item["message"] for item in doctor_errors),
        )
    else:
        add(
            "doctor",
            True,
            "runtime未初期化のためpackage capabilityのみ確認",
        )

    # Exercise release-critical paths in an isolated workspace. This verifies
    # behavior without changing the user's runtime data.
    try:
        with tempfile.TemporaryDirectory(prefix="daily-review-release-check-") as name:
            smoke_root = Path(name) / "source"
            restore_root = Path(name) / "restore"
            init_workspace(smoke_root)
            init_workspace(restore_root)
            atomic_write_json_data(
                smoke_root / "data/daily/2026-07-14.json",
                {"date": "2026-07-14", "main": [], "minimum_line": "休む"},
            )
            secret = smoke_root / "config/.env"
            secret.write_text(
                "FAKE_RELEASE_CHECK_SECRET=not-a-secret\n", encoding="utf-8"
            )
            backup_path, _ = create_backup(
                smoke_root,
                Path(name) / "release-check.zip",
                manual=True,
            )
            verified = verify_backup(backup_path)
            members = {item["path"] for item in verified["manifest"].get("files", [])}
            add(
                "backup verify",
                verified["valid"] and "config/.env" not in members,
                f"{verified['file_count']} files",
            )
            preview = preview_restore(restore_root, backup_path)
            add(
                "restore preview",
                preview["status"] in {"preview_ready", "conflict"},
                preview["status"],
            )
            apply_migration(smoke_root)
            add("migration apply", is_migrated(smoke_root))
            smoke_doctor = run_doctor(smoke_root)
            smoke_errors = [
                item for item in smoke_doctor["issues"] if item["level"] == "ERROR"
            ]
            add(
                "isolated doctor",
                not smoke_errors,
                "; ".join(item["message"] for item in smoke_errors),
            )
    except (OSError, ValueError) as exc:
        add("isolated safety smoke", False, str(exc))

    errors = [item for item in checks if item["level"] == "ERROR"]
    warnings = [item for item in checks if item["level"] == "WARN"]
    return {
        "version": __version__,
        "result": "READY" if not errors else "NOT READY",
        "checks": checks,
        "errors": len(errors),
        "warnings": len(warnings),
    }
