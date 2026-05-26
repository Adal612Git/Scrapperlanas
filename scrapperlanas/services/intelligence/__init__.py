from __future__ import annotations

from .deduplication import compare_accounts
from .evidence import collect_evidence
from .next_best_action import recommend_next_best_action
from .outreach import compose_outreach
from .risk import assess_risk
from .scoring import score_v2
from .state_machine import available_transitions, transition_opportunity

__all__ = [
    "assess_risk",
    "available_transitions",
    "collect_evidence",
    "compare_accounts",
    "compose_outreach",
    "recommend_next_best_action",
    "score_v2",
    "transition_opportunity",
]
