# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only projections for the external producer quality cycle."""

from __future__ import annotations

from typing import TYPE_CHECKING

from weblate.trans.judge import JUDGE_SEATS
from weblate.trans.judge_loop import active_judge_candidate
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    candidate_round,
    compute_decision_revision,
)
from weblate.utils.state import STATE_APPROVED

if TYPE_CHECKING:
    from weblate.auth.models import User


def _seat_results(evidence: list[JudgeVerdict]) -> dict[str, str]:
    by_seat = {row.seat: row for row in evidence}
    return {
        str(seat): (
            "missing"
            if seat not in by_seat
            else "unparsed"
            if by_seat[seat].unparsed
            else "parsed"
        )
        for seat in JUDGE_SEATS
    }


def _issue_categories(evidence: list[JudgeVerdict]) -> list[str]:
    return sorted(
        {
            error["category"]
            for row in evidence
            for error in row.errors
            if isinstance(error, dict) and isinstance(error.get("category"), str)
        }
    )


def project_judge_decision(run_unit: JudgeRunUnit, user: User) -> dict[str, object]:
    """Project one run unit without returning source, target, or judge prompts."""
    unit = run_unit.unit
    if unit is None:
        return {
            "unit_id": run_unit.unit_id_snapshot,
            "run_unit_outcome": run_unit.outcome,
            "severity": run_unit.final_severity or run_unit.initial_severity,
            "seat_results": {str(seat): "missing" for seat in JUDGE_SEATS},
            "disagreement": False,
            "issue_categories": [],
            "candidate_id": None,
            "candidate_verified": False,
            "approval_loss": False,
            "terminology_conflict": False,
            "generation_outcome": "",
            "applicability": "stale",
            "revision": "",
        }

    live_evidence = list(
        JudgeVerdict.objects.filter(
            unit=unit,
            run_id=run_unit.run_id,
            subject=JudgeVerdict.Subject.LIVE,
        ).order_by("seat", "timestamp", "pk")
    )
    representative = run_unit.verdict
    candidate = active_judge_candidate(unit, representative)
    candidate_evidence = (
        candidate_round(unit, candidate.target_list) if candidate else []
    )
    candidate_seats = _seat_results(candidate_evidence)
    candidate_verified = all(
        candidate_seats[str(seat)] == "parsed" for seat in JUDGE_SEATS
    )
    categories = _issue_categories(live_evidence)
    terminology_conflict = "terminology" in categories
    may_apply = user.has_perm("unit.review", unit) and user.has_perm(
        "translation.auto", unit.translation
    )

    if candidate is None:
        applicability = (
            "stale"
            if run_unit.repair_status == JudgeRunUnit.RepairStatus.CANDIDATE_STORED
            else "no_candidate"
        )
    elif not may_apply:
        applicability = "forbidden"
    elif not candidate_verified:
        applicability = "not_verified"
    elif unit.state == STATE_APPROVED or terminology_conflict:
        applicability = "needs_individual_confirmation"
    else:
        applicability = "ready"

    parsed_severities = {row.max_severity for row in live_evidence if not row.unparsed}
    return {
        "unit_id": unit.pk,
        "run_unit_outcome": run_unit.outcome,
        "severity": run_unit.final_severity or run_unit.initial_severity,
        "seat_results": candidate_seats,
        "disagreement": len(parsed_severities) > 1,
        "issue_categories": categories,
        "candidate_id": candidate.pk if candidate is not None else None,
        "candidate_verified": candidate_verified,
        "approval_loss": unit.state == STATE_APPROVED,
        "terminology_conflict": terminology_conflict,
        "generation_outcome": (
            representative.generation_outcome if representative is not None else ""
        ),
        "applicability": applicability,
        "revision": compute_decision_revision(unit),
    }
