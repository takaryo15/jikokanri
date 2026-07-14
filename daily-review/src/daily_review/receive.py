"""Preparation of untrusted ChatGPT responses before importing them."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .chat_schema import extract_json, validate_payload
from .handoff import validate_response


def prepare_receive(
    root: Path,
    content: str,
    *,
    requested_day: str | None,
    allow_expired: bool,
    force: bool,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Extract, validate, and bind a response to an issued handoff."""
    payload, warnings = validate_payload(extract_json(content))
    manifest, item, handoff, content_hash = validate_response(
        root,
        payload,
        requested_day=requested_day,
        allow_expired=allow_expired,
        force=force,
    )
    return payload, warnings, manifest, item, handoff, content_hash
