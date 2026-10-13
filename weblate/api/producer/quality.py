# Copyright © HCGameLoc
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Read-only projections for the external producer quality cycle."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import TYPE_CHECKING

from weblate.checks.glossary import GLOSSARY_CHECK_ID, evaluate_glossary_terms
from weblate.checks.models import Check
from weblate.trans.judge import JUDGE_SEATS
from weblate.trans.judge_loop import active_judge_candidate
from weblate.trans.models.judge import (
    JudgeRunUnit,
    JudgeVerdict,
    candidate_round,
    compute_decision_revision,
    compute_target_hash,
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


def _digest(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _variants(units: list) -> list[dict[str, object]]:
    grouped: dict[str, list[int]] = defaultdict(list)
    for unit in units:
        grouped[compute_target_hash(unit.get_target_plurals())].append(unit.pk)
    return [
        {"target_hash": target_hash, "unit_ids": sorted(unit_ids)}
        for target_hash, unit_ids in sorted(grouped.items())
    ]


def _glossary_strength(unit) -> tuple[bool, bool]:
    hard: set[str] = set()
    advisory: set[str] = set()
    sources = unit.get_source_plurals()
    targets = unit.get_target_plurals()
    source = sources[0]
    pair_hard, pair_advisory = evaluate_glossary_terms(unit, source, targets[0])
    hard |= pair_hard
    advisory |= pair_advisory
    if len(sources) > 1:
        source = sources[1]
    for target in targets[1:]:
        pair_hard, pair_advisory = evaluate_glossary_terms(unit, source, target)
        hard |= pair_hard
        advisory |= pair_advisory
    return bool(hard), bool(advisory - hard)


def project_quality_issues(
    *, project, language: str, kinds: list[str], user
) -> dict[str, object]:
    """Return structured active repeat/glossary issues without translation text."""
    names = {
        "repeat-drift": "repeat-drift",
        "glossary": GLOSSARY_CHECK_ID,
    }
    requested_names = [names[kind] for kind in kinds]
    checks = (
        Check.objects.filter_access(user)
        .filter(
            dismissed=False,
            name__in=requested_names,
            unit__translation__component__project=project,
            unit__translation__language__code=language,
        )
        .select_related(
            "unit",
            "unit__translation",
            "unit__translation__component",
            "unit__source_unit",
        )
        .order_by("unit_id", "name")
    )
    repeat_groups: dict[tuple[str, int], list] = defaultdict(list)
    glossary_units = []
    for check in checks:
        if check.name == "repeat-drift":
            repeat_groups[check.unit.source, check.unit.translation.plural_id].append(
                check.unit
            )
        else:
            glossary_units.append(check.unit)

    issues: list[dict[str, object]] = []
    for (source, plural_id), units in sorted(repeat_groups.items()):
        approved_variants = {
            compute_target_hash(unit.get_target_plurals())
            for unit in units
            if unit.state == STATE_APPROVED
        }
        approved_conflict = len(approved_variants) > 1
        issues.append(
            {
                "group_id": f"repeat:{_digest([project.pk, language, plural_id, source])}",
                "kind": "repeat-drift",
                "hard": False,
                "advisory": True,
                "unit_ids": sorted(unit.pk for unit in units),
                "revisions": {
                    str(unit.pk): compute_decision_revision(unit) for unit in units
                },
                "variants": _variants(units),
                "term_mode": "",
                "approved_conflict": approved_conflict,
                "auto_repair_eligible": not approved_conflict,
            }
        )
    for unit in glossary_units:
        hard, advisory = _glossary_strength(unit)
        issues.append(
            {
                "group_id": f"glossary:{unit.pk}",
                "kind": "glossary",
                "hard": hard,
                "advisory": advisory or not hard,
                "unit_ids": [unit.pk],
                "revisions": {str(unit.pk): compute_decision_revision(unit)},
                "variants": _variants([unit]),
                "term_mode": (
                    "mixed" if hard and advisory else "hard" if hard else "inflectable"
                ),
                "approved_conflict": False,
                "auto_repair_eligible": hard and unit.state != STATE_APPROVED,
            }
        )
    issues.sort(key=lambda issue: (str(issue["kind"]), str(issue["group_id"])))
    return {"snapshot_hash": _digest(issues), "issues": issues}


def quality_recheck_snapshot(items: list[dict[str, object]]) -> str:
    """Hash the complete, ordered result of a bounded recheck request."""
    return _digest(sorted(items, key=lambda item: int(item["unit_id"])))
