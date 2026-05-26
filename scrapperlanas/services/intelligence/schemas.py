from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class RiskAssessment:
    scam_risk: int
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ScoreV2Result:
    grade: str
    numeric_score: int
    components: dict[str, int]
    reasons: list[str]
    warnings: list[str]
    suggested_next_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NextBestAction:
    action_type: str
    label: str
    description: str
    priority: int
    confidence: int
    reason: str
    required_fields: list[str] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)
    suggested_payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutreachDraftV2:
    tone: str
    subject: str
    body: str
    personalization_bullets: list[str]
    risks: list[str]
    suggested_cta: str
    language: str
    confidence: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CopilotModeResult:
    mode: str
    verdict: str
    reasons: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    blocking_conditions: list[str] = field(default_factory=list)
    draft: dict[str, Any] | None = None
    checklist: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DedupeAssessment:
    duplicate_confidence: float
    recommended_action: str
    reasons: list[str]
    conflicting_fields: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TransitionResult:
    allowed: bool
    previous_state: str
    next_state: str
    guard_result: str
    reason: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
