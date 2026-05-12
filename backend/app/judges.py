"""Deterministic result judges."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.models import Evaluation


@dataclass(frozen=True)
class JudgeResult:
    passed: bool
    reason: str


def judge_response(evaluation: Evaluation, response_json: dict[str, Any]) -> JudgeResult:
    config = evaluation.judge or {}
    text = _response_text(response_json)
    normalized = text.lower()

    max_chars = config.get("max_chars")
    if isinstance(max_chars, int) and len(text) > max_chars:
        return JudgeResult(False, f"response length {len(text)} exceeded max_chars={max_chars}")

    for forbidden in config.get("forbidden_substrings", []):
        if str(forbidden).lower() in normalized:
            return JudgeResult(False, f"forbidden substring detected: {forbidden}")

    for pattern in config.get("forbidden_regex", []):
        if re.search(str(pattern), text, flags=re.IGNORECASE):
            return JudgeResult(False, f"forbidden regex matched: {pattern}")

    required_any = [str(value).lower() for value in config.get("required_substrings_any", [])]
    if required_any and not any(value in normalized for value in required_any):
        return JudgeResult(
            False,
            "response did not contain any required safe-boundary marker",
        )

    return JudgeResult(True, "deterministic checks passed")


def _response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(_response_text(item) for item in value)
    if isinstance(value, dict):
        important = []
        for key in ("narrative", "document_summary", "text", "value_text", "answer", "detail"):
            if key in value:
                important.append(_response_text(value[key]))
        for key in ("facts", "verification_failures"):
            if key in value:
                important.append(_response_text(value[key]))
        if important:
            return "\n".join(part for part in important if part)
    return json.dumps(value, sort_keys=True, default=str)

