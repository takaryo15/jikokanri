"""Safe launchd generation and opt-in scheduler installation."""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .scheduler import load_scheduler_config


LABEL = "com.daily-review.scheduler"


def _executable(explicit: str | None = None) -> str:
    value = explicit or shutil.which("daily-review")
    if not value:
        raise ValueError("daily-review実行ファイルが見つかりません")
    return str(Path(value).resolve())


def plist_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def expected_plist(
    root: Path,
    *,
    executable: str | None = None,
) -> dict[str, Any]:
    config = load_scheduler_config(root)
    return {
        "Label": LABEL,
        "ProgramArguments": [
            _executable(executable),
            "scheduler",
            "run-due",
            "--root",
            str(root.resolve()),
            "--format",
            "json",
        ],
        "StartInterval": config["poll_interval_minutes"] * 60,
        "RunAtLoad": True,
        "WorkingDirectory": str(root.resolve()),
        "StandardOutPath": str((root / "logs" / "scheduler.log").resolve()),
        "StandardErrorPath": str((root / "logs" / "scheduler-error.log").resolve()),
        "EnvironmentVariables": {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
            "PYTHONUNBUFFERED": "1",
        },
        "ProcessType": "Background",
    }


def install_status(
    root: Path,
    *,
    home: Path | None = None,
    executable: str | None = None,
) -> dict[str, Any]:
    path = plist_path(home)
    value = None
    error = None
    if path.exists():
        try:
            value = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException) as exc:
            error = str(exc)
    loaded = False
    if sys.platform == "darwin":
        result = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        loaded = result.returncode == 0
    try:
        expected = expected_plist(root, executable=executable)
    except ValueError:
        expected = None
    return {
        "backend": "launchd",
        "plist_path": str(path),
        "plist_exists": path.exists(),
        "loaded": loaded,
        "matches_expected": value == expected if value and expected else False,
        "plist": value,
        "error": error,
    }


def cron_example(root: Path, *, executable: str | None = None) -> str:
    config = load_scheduler_config(root)
    arguments = expected_plist(root, executable=executable)["ProgramArguments"]
    interval = config["poll_interval_minutes"]
    minute = f"*/{interval}" if interval < 60 else "0"
    return f"{minute} * * * * {shlex.join(arguments)}"


def install_scheduler(
    root: Path,
    *,
    backend: str = "launchd",
    dry_run: bool = False,
    home: Path | None = None,
    executable: str | None = None,
) -> dict[str, Any]:
    if backend == "cron":
        return {
            "status": "preview",
            "backend": "cron",
            "cron": cron_example(root, executable=executable),
            "note": "crontabは変更していません",
        }
    if backend != "launchd":
        raise ValueError("--backendはlaunchdまたはcronにしてください")
    if not dry_run and sys.platform != "darwin":
        raise ValueError("launchdの実登録はmacOSでのみ利用できます")
    plist = expected_plist(root, executable=executable)
    path = plist_path(home)
    result = {
        "status": "dry_run" if dry_run else "installed",
        "backend": "launchd",
        "plist_path": str(path),
        "plist": plist,
    }
    if dry_run:
        return result
    path.parent.mkdir(parents=True, exist_ok=True)
    (root / "logs").mkdir(parents=True, exist_ok=True)
    content = plistlib.dumps(plist, fmt=plistlib.FMT_XML, sort_keys=True)
    previous = path.read_bytes() if path.exists() else None
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    loaded = subprocess.run(
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if loaded.returncode:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(previous)
        raise OSError(f"launchctl bootstrapに失敗しました: {loaded.stderr.strip()}")
    result["launchd"] = install_status(root, home=home, executable=executable)
    return result


def uninstall_scheduler(
    *,
    dry_run: bool = False,
    home: Path | None = None,
) -> dict[str, Any]:
    path = plist_path(home)
    result = {
        "status": "dry_run" if dry_run else "uninstalled",
        "backend": "launchd",
        "plist_path": str(path),
        "data_removed": False,
    }
    if dry_run:
        return result
    if sys.platform != "darwin":
        raise ValueError("launchdの解除はmacOSでのみ利用できます")
    subprocess.run(
        ["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    path.unlink(missing_ok=True)
    return result
