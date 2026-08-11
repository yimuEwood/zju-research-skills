#!/usr/bin/env python3
"""Assess search coverage and marginal yield from an offline search-round ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EXPANSION_STRATEGIES = {
    "backward_chaining",
    "forward_chaining",
    "related_records",
    "seed_expansion",
    "author_keyword_expansion",
}


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def assess(payload: dict[str, Any]) -> dict[str, Any]:
    rounds = payload.get("rounds", [])
    if not isinstance(rounds, list):
        raise ValueError("rounds must be a list")

    required_concepts = set(_strings(payload.get("required_concepts")))
    required_sources = set(_strings(payload.get("required_source_families")))
    low_yield_max = int(payload.get("low_yield_max_new_eligible", 1))
    low_yield_rounds = max(1, int(payload.get("required_consecutive_low_yield_rounds", 2)))

    seen_records: set[str] = set()
    seen_eligible: set[str] = set()
    covered_concepts: set[str] = set()
    covered_sources: set[str] = set()
    round_reports: list[dict[str, Any]] = []
    expansion_seen = False

    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            raise ValueError(f"rounds[{index}] must be an object")
        round_id = str(item.get("round_id") or f"ROUND-{index}")
        records = set(_strings(item.get("record_ids")))
        eligible = set(_strings(item.get("eligible_ids")))
        if not eligible <= records:
            raise ValueError(f"{round_id}: eligible_ids must be a subset of record_ids")
        new_records = records - seen_records
        new_eligible = eligible - seen_eligible
        seen_records.update(records)
        seen_eligible.update(eligible)
        covered_concepts.update(_strings(item.get("concepts_covered")))
        source_family = str(item.get("source_family") or "").strip()
        if source_family:
            covered_sources.add(source_family)
        strategy = str(item.get("strategy") or "").strip()
        expansion_seen = expansion_seen or strategy in EXPANSION_STRATEGIES
        round_reports.append(
            {
                "round_id": round_id,
                "strategy": strategy or "not_reported",
                "source_family": source_family or "not_reported",
                "retrieved": len(records),
                "eligible": len(eligible),
                "new_records": len(new_records),
                "new_eligible": len(new_eligible),
                "cumulative_records": len(seen_records),
                "cumulative_eligible": len(seen_eligible),
                "marginal_eligible_fraction": round(len(new_eligible) / max(1, len(eligible)), 4),
            }
        )

    missing_concepts = sorted(required_concepts - covered_concepts)
    missing_sources = sorted(required_sources - covered_sources)
    open_critical_gaps = sorted(
        str(item.get("gap_id") or "unidentified_gap")
        for item in payload.get("evidence_gaps", [])
        if isinstance(item, dict)
        and str(item.get("status") or "open") == "open"
        and str(item.get("severity") or "").lower() == "critical"
    )
    recent = round_reports[-low_yield_rounds:]
    yield_plateau = len(recent) == low_yield_rounds and all(
        item["new_eligible"] <= low_yield_max for item in recent
    )
    prerequisites = {
        "concept_coverage_complete": not missing_concepts,
        "source_family_coverage_complete": not missing_sources,
        "citation_or_seed_expansion_completed": expansion_seen,
        "no_open_critical_evidence_gap": not open_critical_gaps,
        "consecutive_low_yield_rounds": yield_plateau,
    }
    stop_recommended = bool(rounds) and all(prerequisites.values())

    if missing_concepts:
        next_action = "run_gap_targeted_query_for_missing_concepts"
    elif missing_sources:
        next_action = "search_missing_source_family"
    elif not expansion_seen:
        next_action = "run_backward_and_forward_citation_chasing_from_included_seeds"
    elif open_critical_gaps:
        next_action = "run_evidence_gap_targeted_search"
    elif not yield_plateau:
        next_action = "run_one_more_orthogonal_query_round"
    else:
        next_action = "freeze_search_set_and_handoff_to_fulltext_screening"

    return {
        "rounds": round_reports,
        "coverage": {
            "required_concepts": sorted(required_concepts),
            "covered_concepts": sorted(covered_concepts),
            "missing_concepts": missing_concepts,
            "required_source_families": sorted(required_sources),
            "covered_source_families": sorted(covered_sources),
            "missing_source_families": missing_sources,
        },
        "saturation": {
            "stop_recommended": stop_recommended,
            "prerequisites": prerequisites,
            "open_critical_gap_ids": open_critical_gaps,
            "rule": (
                f"coverage complete + expansion completed + no critical gap + "
                f"{low_yield_rounds} consecutive rounds with <= {low_yield_max} new eligible record(s)"
            ),
            "next_action": next_action,
        },
        "totals": {"unique_records": len(seen_records), "unique_eligible": len(seen_eligible)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = assess(json.loads(args.input.read_text(encoding="utf-8-sig")))
    body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(body, encoding="utf-8")
    else:
        print(body, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
