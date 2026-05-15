"""Registry-backed routing policy for exploration campaigns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Attempt, Campaign, Evaluation

PRIORITY_WEIGHTS = {
    "P0": 5.0,
    "P1": 3.0,
    "P2": 1.5,
    "P3": 1.0,
}

CATEGORY_ALIASES = {
    "prompt_injection_indirect": "prompt_injection",
    "prompt_injection_direct": "prompt_injection",
    "dos_cost_amplification": "denial_of_service",
}


@dataclass(frozen=True)
class RegistryCategory:
    key: str
    db_category_key: str
    priority: str
    priority_weight: float
    target_endpoints: list[str]
    section_text: str


@dataclass(frozen=True)
class EvaluationSelection:
    evaluation: Evaluation
    metadata: dict[str, Any]


@dataclass(frozen=True)
class _AttemptStats:
    category_counts: dict[str, int]
    evaluation_counts: dict[str, int]
    category_last_attempt_at: dict[str, datetime]


class ThreatRegistry:
    def __init__(self, categories: list[RegistryCategory]) -> None:
        self.categories = categories
        by_db: dict[str, list[RegistryCategory]] = {}
        for category in categories:
            by_db.setdefault(category.db_category_key, []).append(category)
        self._by_db = by_db

    @classmethod
    def load(cls, path: Path | None = None) -> ThreatRegistry:
        registry_path = path or Path(__file__).resolve().parents[1] / "data" / "threat_registry.md"
        if not registry_path.exists():
            return cls([])
        return cls(_parse_registry_categories(registry_path.read_text(encoding="utf-8")))

    def categories_for_db(self, db_category_key: str) -> list[RegistryCategory]:
        return self._by_db.get(db_category_key, [])

    def priority_for_db(self, db_category_key: str) -> tuple[str, float]:
        categories = self.categories_for_db(db_category_key)
        if not categories:
            return "unregistered", 1.0
        strongest = max(categories, key=lambda category: category.priority_weight)
        return strongest.priority, strongest.priority_weight

    def keys_for_db(self, db_category_key: str) -> list[str]:
        return [category.key for category in self.categories_for_db(db_category_key)]

    def context_for_db(self, db_category_key: str, focus_hint: str | None = None) -> list[dict[str, Any]]:
        categories = self.categories_for_db(db_category_key)
        focused = self._focused_categories(categories, focus_hint)
        return [
            {
                "key": category.key,
                "db_category_key": category.db_category_key,
                "priority": category.priority,
                "target_endpoints": category.target_endpoints,
                "section_text": category.section_text,
            }
            for category in focused
        ]

    def db_categories_for_focus(self, focus_hint: str | None) -> set[str]:
        if not focus_hint:
            return set()
        normalized = _normalize_text(focus_hint)
        matches = set()
        for category in self.categories:
            if normalized in _normalize_text(category.key):
                matches.add(category.db_category_key)
        return matches

    @staticmethod
    def _focused_categories(
        categories: list[RegistryCategory],
        focus_hint: str | None,
    ) -> list[RegistryCategory]:
        if not focus_hint:
            return categories
        normalized = _normalize_text(focus_hint)
        focused = [
            category
            for category in categories
            if normalized in _normalize_text(category.key)
        ]
        return focused or categories


class OrchestratorRouter:
    def __init__(self, db: Session, registry: ThreatRegistry | None = None) -> None:
        self.db = db
        self.registry = registry or ThreatRegistry.load()

    def select_evaluation(self, campaign: Campaign, offset: int) -> EvaluationSelection:
        evaluations = self._enabled_evaluations()
        if not evaluations:
            raise ValueError("no enabled evaluations are available for campaign execution")

        stats = self._attempt_stats()
        candidates, focus_match = self._candidate_pool(evaluations, campaign.focus_hint)
        scored = [
            (
                self._score_evaluation(
                    evaluation=evaluation,
                    campaign=campaign,
                    stats=stats,
                    focus_match=focus_match,
                ),
                evaluation,
            )
            for evaluation in candidates
        ]
        scored.sort(
            key=lambda item: (
                -item[0]["score"],
                item[0]["evaluation_attempt_count"],
                item[1].id,
            )
        )
        selected_score, selected = scored[offset % len(scored)] if focus_match == "evaluation_pool" else scored[0]
        return EvaluationSelection(
            evaluation=selected,
            metadata={
                "strategy": "registry_priority_weighted_v1",
                "focus_hint": campaign.focus_hint,
                "focus_match": focus_match,
                "candidate_count": len(candidates),
                "offset": offset,
                "selected_evaluation_id": selected.id,
                "selected_evaluation_key": selected.key,
                "selected_category_key": selected.category.key,
                "selected_registry_categories": self.registry.keys_for_db(selected.category.key),
                "selected_registry_context": self.registry.context_for_db(
                    selected.category.key,
                    campaign.focus_hint,
                ),
                "selection_reason": selected_score["reason"],
                **selected_score,
            },
        )

    def _enabled_evaluations(self) -> list[Evaluation]:
        return list(
            self.db.scalars(
                select(Evaluation)
                .options(selectinload(Evaluation.category))
                .where(Evaluation.enabled.is_(True))
                .order_by(Evaluation.id)
            )
        )

    def _candidate_pool(
        self,
        evaluations: list[Evaluation],
        focus_hint: str | None,
    ) -> tuple[list[Evaluation], str]:
        if not focus_hint:
            return evaluations, "none"

        normalized = _normalize_text(focus_hint)
        exact = [
            evaluation
            for evaluation in evaluations
            if normalized == _normalize_text(evaluation.key)
            or normalized == _normalize_text(evaluation.name)
        ]
        if exact:
            return exact, "evaluation_exact"

        partial = [
            evaluation
            for evaluation in evaluations
            if normalized in _normalize_text(evaluation.key)
            or normalized in _normalize_text(evaluation.name)
        ]
        if partial:
            return partial, "evaluation_pool"

        focused_categories = self.registry.db_categories_for_focus(focus_hint)
        category_matches = [
            evaluation for evaluation in evaluations if evaluation.category.key in focused_categories
        ]
        if category_matches:
            return category_matches, "registry_category"

        direct_category_matches = [
            evaluation
            for evaluation in evaluations
            if normalized in _normalize_text(evaluation.category.key)
            or normalized in _normalize_text(evaluation.category.name)
        ]
        if direct_category_matches:
            return direct_category_matches, "db_category"

        return evaluations, "unmatched"

    def _score_evaluation(
        self,
        *,
        evaluation: Evaluation,
        campaign: Campaign,
        stats: _AttemptStats,
        focus_match: str,
    ) -> dict[str, Any]:
        category_key = evaluation.category.key
        priority, priority_weight = self.registry.priority_for_db(category_key)
        category_attempt_count = stats.category_counts.get(category_key, 0)
        evaluation_attempt_count = stats.evaluation_counts.get(evaluation.key, 0)
        last_attempt_at = stats.category_last_attempt_at.get(category_key)
        staleness_boost = _staleness_boost(last_attempt_at)
        coverage_boost = 1.0 / (1 + category_attempt_count)
        p0_floor_boost = 1.25 if priority == "P0" else 1.0
        focus_boost = 4.0 if focus_match in {"evaluation_exact", "evaluation_pool"} else 1.0
        if focus_match in {"registry_category", "db_category"}:
            focus_boost = 3.0
        score = (
            priority_weight * p0_floor_boost
            + focus_boost
            + staleness_boost
            + coverage_boost
            - (evaluation_attempt_count * 0.15)
        )
        reason = (
            f"priority={priority}, focus={focus_match}, category_attempts={category_attempt_count}, "
            f"evaluation_attempts={evaluation_attempt_count}, staleness_boost={staleness_boost:.2f}"
        )
        if campaign.focus_hint:
            reason = f"{reason}, focus_hint={campaign.focus_hint}"
        return {
            "score": round(score, 4),
            "priority": priority,
            "priority_weight": priority_weight,
            "p0_floor_boost": p0_floor_boost,
            "focus_boost": focus_boost,
            "coverage_boost": round(coverage_boost, 4),
            "staleness_boost": round(staleness_boost, 4),
            "category_attempt_count": category_attempt_count,
            "evaluation_attempt_count": evaluation_attempt_count,
            "category_last_attempt_at": last_attempt_at.isoformat() if last_attempt_at else None,
            "reason": reason,
        }

    def _attempt_stats(self) -> _AttemptStats:
        category_counts: dict[str, int] = {}
        evaluation_counts: dict[str, int] = {}
        category_last_attempt_at: dict[str, datetime] = {}
        attempts = self.db.execute(
            select(Attempt.focus_area, Attempt.vector_key, Attempt.finished_at, Attempt.started_at, Attempt.created_at)
        )
        for category_key, evaluation_key, finished_at, started_at, created_at in attempts:
            if category_key:
                category_counts[category_key] = category_counts.get(category_key, 0) + 1
                timestamp = finished_at or started_at or created_at
                if timestamp is not None:
                    timestamp = _ensure_aware(timestamp)
                    current = category_last_attempt_at.get(category_key)
                    if current is None or timestamp > current:
                        category_last_attempt_at[category_key] = timestamp
            if evaluation_key:
                evaluation_counts[evaluation_key] = evaluation_counts.get(evaluation_key, 0) + 1
        return _AttemptStats(
            category_counts=category_counts,
            evaluation_counts=evaluation_counts,
            category_last_attempt_at=category_last_attempt_at,
        )


def _parse_registry_categories(markdown: str) -> list[RegistryCategory]:
    pattern = re.compile(r"^## category:\s*(?P<key>[a-zA-Z0-9_/-]+)\s*$", re.MULTILINE)
    matches = list(pattern.finditer(markdown))
    categories: list[RegistryCategory] = []
    for index, match in enumerate(matches):
        key = match.group("key")
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        section = markdown[start:end]
        metadata = _extract_yaml_metadata(section)
        priority = str(metadata.get("priority") or "P3").upper()
        priority_weight = PRIORITY_WEIGHTS.get(priority, 1.0)
        target_endpoints = metadata.get("target_endpoints")
        categories.append(
            RegistryCategory(
                key=key,
                db_category_key=CATEGORY_ALIASES.get(key, key),
                priority=priority,
                priority_weight=priority_weight,
                target_endpoints=target_endpoints if isinstance(target_endpoints, list) else [],
                section_text=f"## category: {key}\n{section.strip()}",
            )
        )
    return categories


def _extract_yaml_metadata(section: str) -> dict[str, Any]:
    block_match = re.search(r"```yaml\s*(.*?)```", section, flags=re.DOTALL)
    if block_match is None:
        return {}
    metadata: dict[str, Any] = {}
    for raw_line in block_match.group(1).splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed_value: str | list[str] = value.strip()
        if parsed_value.startswith("[") and parsed_value.endswith("]"):
            parsed_value = [
                item.strip().strip("'\"")
                for item in parsed_value[1:-1].split(",")
                if item.strip()
            ]
        metadata[key.strip()] = parsed_value
    return metadata


def _staleness_boost(last_attempt_at: datetime | None) -> float:
    if last_attempt_at is None:
        return 2.0
    last_attempt_at = _ensure_aware(last_attempt_at)
    age_days = max((datetime.now(UTC) - last_attempt_at).total_seconds() / 86400, 0)
    return min(age_days / 7, 2.0)


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
