#!/usr/bin/env python3
"""Select a deterministic constrained heuristic portfolio of discriminating experiments."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import math
from pathlib import Path
from typing import Any


FEASIBILITY_STATUSES = {"eligible", "ineligible", "uncertain"}
ETHICS_STATUSES = {"approved", "not_required", "pending", "rejected", "unknown"}


def _load_validator():
    path = Path(__file__).with_name("validate_hypothesis_set.py")
    spec = importlib.util.spec_from_file_location("validate_hypothesis_set", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load hypothesis validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pairs(experiment: dict[str, Any]) -> set[tuple[str, str]]:
    predictions = experiment.get("predicted_outcomes", {})
    covered: set[tuple[str, str]] = set()
    for left, right in itertools.combinations(sorted(map(str, experiment["hypothesis_ids"])), 2):
        if predictions.get(left) not in (None, "", "unknown") and predictions.get(right) not in (None, "", "unknown"):
            left_value = json.dumps(predictions[left], sort_keys=True, ensure_ascii=False)
            right_value = json.dumps(predictions[right], sort_keys=True, ensure_ascii=False)
            if left_value != right_value:
                covered.add((left, right))
    return covered


def _id_list(value: Any, field: str, experiment_id: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{experiment_id} {field} must be a list")
    result = [str(item).strip() for item in value]
    if any(not item for item in result) or len(result) != len(set(result)):
        raise ValueError(f"{experiment_id} {field} must contain unique non-empty experiment IDs")
    return result


def _eligibility(item: dict[str, Any], experiment_id: str) -> tuple[bool, dict[str, Any]]:
    if not isinstance(item.get("eligible"), bool):
        raise ValueError(f"{experiment_id} eligible must be explicitly true or false")
    feasibility = str(item.get("feasibility_status") or "").strip().lower()
    ethics = str(item.get("ethics_status") or "").strip().lower()
    if feasibility not in FEASIBILITY_STATUSES:
        raise ValueError(f"{experiment_id} feasibility_status must be eligible, ineligible, or uncertain")
    if ethics not in ETHICS_STATUSES:
        raise ValueError(f"{experiment_id} ethics_status is invalid")
    effective = item["eligible"] and feasibility == "eligible" and ethics in {"approved", "not_required"}
    if item["eligible"] and not effective:
        raise ValueError(
            f"{experiment_id} cannot be eligible while feasibility_status={feasibility} and ethics_status={ethics}"
        )
    reasons = []
    if not item["eligible"]:
        reasons.append("declared_ineligible")
    if feasibility != "eligible":
        reasons.append(f"feasibility_{feasibility}")
    if ethics not in {"approved", "not_required"}:
        reasons.append(f"ethics_{ethics}")
    return effective, {
        "declared_eligible": item["eligible"],
        "feasibility_status": feasibility,
        "ethics_status": ethics,
        "effective_eligible": effective,
        "reasons": reasons,
    }


def select(payload: dict[str, Any], budget: float) -> dict[str, Any]:
    report = _load_validator().validate(payload)
    if not report["valid"]:
        raise ValueError("hypothesis plan is invalid: " + json.dumps(report["findings"], ensure_ascii=False))
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("budget must be a finite positive number")
    cost_unit = str(payload.get("cost_unit") or "").strip()
    if not cost_unit:
        raise ValueError("cost_unit is required so all experiment costs and the budget are comparable")

    experiments = payload["experiments"]
    hypothesis_ids = [str(item["hypothesis_id"]) for item in payload["hypotheses"]]
    if len(hypothesis_ids) != len(set(hypothesis_ids)):
        raise ValueError("hypothesis_id values must be unique")
    experiment_ids = [str(item["experiment_id"]) for item in experiments]
    if len(experiment_ids) != len(set(experiment_ids)):
        raise ValueError("experiment_id values must be unique")
    items = {str(item["experiment_id"]): item for item in experiments}
    known_ids = set(items)

    costs: dict[str, float] = {}
    pair_map: dict[str, set[tuple[str, str]]] = {}
    dependencies: dict[str, set[str]] = {}
    exclusions: dict[str, set[str]] = {experiment_id: set() for experiment_id in items}
    eligibility: dict[str, dict[str, Any]] = {}
    effective_eligible: dict[str, bool] = {}
    for experiment_id, item in items.items():
        item_unit = str(item.get("cost_unit") or cost_unit).strip()
        if item_unit != cost_unit:
            raise ValueError(f"{experiment_id} cost_unit {item_unit!r} does not match portfolio cost_unit {cost_unit!r}")
        if item.get("cost") in (None, ""):
            raise ValueError(f"{experiment_id} requires a comparable numeric cost")
        try:
            cost = float(item["cost"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{experiment_id} cost must be numeric") from exc
        if not math.isfinite(cost) or cost <= 0:
            raise ValueError(f"{experiment_id} cost must be finite and positive")
        costs[experiment_id] = cost
        pair_map[experiment_id] = _pairs(item)
        dependencies[experiment_id] = set(_id_list(item.get("depends_on"), "depends_on", experiment_id))
        direct_exclusions = set(_id_list(item.get("mutually_exclusive_with"), "mutually_exclusive_with", experiment_id))
        unknown = (dependencies[experiment_id] | direct_exclusions) - known_ids
        if unknown:
            raise ValueError(f"{experiment_id} references unknown experiment IDs: {sorted(unknown)}")
        if experiment_id in dependencies[experiment_id] or experiment_id in direct_exclusions:
            raise ValueError(f"{experiment_id} cannot depend on or exclude itself")
        exclusions[experiment_id].update(direct_exclusions)
        effective_eligible[experiment_id], eligibility[experiment_id] = _eligibility(item, experiment_id)
    for left, excluded_ids in list(exclusions.items()):
        for right in excluded_ids:
            exclusions[right].add(left)

    required = set(_id_list(payload.get("required_experiment_ids"), "required_experiment_ids", "portfolio"))
    required.update(experiment_id for experiment_id, item in items.items() if item.get("required") is True)
    unknown_required = required - known_ids
    if unknown_required:
        raise ValueError(f"required_experiment_ids contains unknown IDs: {sorted(unknown_required)}")

    closure_cache: dict[str, set[str]] = {}

    def dependency_closure(experiment_id: str, trail: tuple[str, ...] = ()) -> set[str]:
        if experiment_id in closure_cache:
            return set(closure_cache[experiment_id])
        if experiment_id in trail:
            cycle = " -> ".join((*trail, experiment_id))
            raise ValueError(f"dependency cycle detected: {cycle}")
        closure = {experiment_id}
        for dependency in sorted(dependencies[experiment_id]):
            closure |= dependency_closure(dependency, (*trail, experiment_id))
        closure_cache[experiment_id] = set(closure)
        return closure

    for experiment_id in sorted(items):
        dependency_closure(experiment_id)

    def conflict_in(group: set[str]) -> tuple[str, str] | None:
        for left in sorted(group):
            for right in sorted(exclusions[left] & group):
                if left < right:
                    return left, right
        return None

    required_closure: set[str] = set()
    for experiment_id in sorted(required):
        required_closure |= dependency_closure(experiment_id)
    ineligible_required = sorted(item for item in required_closure if not effective_eligible[item])
    if ineligible_required:
        raise ValueError(f"required experiments or their dependencies are ineligible: {ineligible_required}")
    required_conflict = conflict_in(required_closure)
    if required_conflict:
        raise ValueError(f"required experiment set is mutually exclusive: {list(required_conflict)}")
    required_cost = sum(costs[item] for item in required_closure)
    if required_cost > budget + 1e-12:
        raise ValueError(
            f"required experiments and dependencies cost {required_cost:g} {cost_unit}, exceeding budget {budget:g} {cost_unit}"
        )

    all_ids = sorted(hypothesis_ids)
    all_pairs = set(itertools.combinations(all_ids, 2))
    uncovered = set(all_pairs)
    selected_ids: set[str] = set()
    selected: list[dict[str, Any]] = []
    spent = 0.0

    def add_experiment(experiment_id: str, selection_reason: str) -> None:
        nonlocal spent
        new_pairs = pair_map[experiment_id] & uncovered
        spent += costs[experiment_id]
        uncovered.difference_update(new_pairs)
        selected_ids.add(experiment_id)
        selected.append({
            "experiment_id": experiment_id,
            "selection_reason": selection_reason,
            "cost": costs[experiment_id],
            "cost_unit": cost_unit,
            "newly_covered_pairs": [list(pair) for pair in sorted(new_pairs)],
            "cumulative_cost": spent,
            "depends_on": sorted(dependencies[experiment_id]),
        })

    def add_bundle(bundle: set[str], focal: str | None, reason: str) -> None:
        pending = set(bundle) - selected_ids
        while pending:
            ready = sorted(item for item in pending if dependencies[item] <= selected_ids)
            if not ready:
                raise ValueError("internal error: no dependency-respecting order for selected bundle")
            for experiment_id in ready:
                if experiment_id in pending:
                    item_reason = reason if experiment_id == focal or focal is None else f"dependency_for:{focal}"
                    add_experiment(experiment_id, item_reason)
                    pending.remove(experiment_id)

    add_bundle(required_closure, None, "required_or_required_dependency")

    skipped: dict[str, set[str]] = {experiment_id: set() for experiment_id in items}
    while True:
        candidates: list[tuple[str, set[str], set[tuple[str, str]], float, int, bool, float]] = []
        for experiment_id, item in items.items():
            if experiment_id in selected_ids:
                continue
            bundle = dependency_closure(experiment_id) - selected_ids
            ineligible = sorted(candidate for candidate in bundle if not effective_eligible[candidate])
            if ineligible:
                skipped[experiment_id].add("ineligible_dependency_or_focal:" + ",".join(ineligible))
                continue
            if conflict_in(bundle):
                skipped[experiment_id].add("mutual_exclusion_within_dependency_bundle")
                continue
            selected_conflicts = sorted(
                candidate for candidate in bundle
                if exclusions[candidate] & selected_ids
            )
            if selected_conflicts:
                skipped[experiment_id].add("mutual_exclusion_with_selected:" + ",".join(selected_conflicts))
                continue
            bundle_cost = sum(costs[candidate] for candidate in bundle)
            if spent + bundle_cost > budget + 1e-12:
                skipped[experiment_id].add("budget")
                continue
            new_pairs: set[tuple[str, str]] = set()
            for candidate in bundle:
                new_pairs |= pair_map[candidate] & uncovered
            if not new_pairs:
                skipped[experiment_id].add("no_new_pair_coverage")
                continue
            candidates.append((
                experiment_id,
                bundle,
                new_pairs,
                len(new_pairs) / bundle_cost,
                len(new_pairs),
                bool(item.get("orthogonal_measurement")),
                bundle_cost,
            ))
        if not candidates:
            break
        focal, bundle, _, _, _, _, _ = sorted(
            candidates,
            key=lambda candidate: (
                -candidate[3], -candidate[4], -int(candidate[5]), candidate[6], candidate[0]
            ),
        )[0]
        add_bundle(bundle, focal, "greedy_pair_coverage_per_incremental_cost")

    ineligible_report = [
        {"experiment_id": experiment_id, **eligibility[experiment_id]}
        for experiment_id in sorted(items)
        if not effective_eligible[experiment_id]
    ]
    skipped_report = [
        {"experiment_id": experiment_id, "reasons": sorted(reasons)}
        for experiment_id, reasons in sorted(skipped.items())
        if experiment_id not in selected_ids and reasons
    ]
    return {
        "schema_version": "2.0",
        "budget": budget,
        "cost_unit": cost_unit,
        "cost_spent": spent,
        "budget_remaining": budget - spent,
        "required_experiment_ids": sorted(required),
        "required_dependency_closure": sorted(required_closure),
        "selected": selected,
        "covered_pairs": len(all_pairs) - len(uncovered),
        "total_pairs": len(all_pairs),
        "coverage_fraction": (len(all_pairs) - len(uncovered)) / max(1, len(all_pairs)),
        "uncovered_pairs": [list(pair) for pair in sorted(uncovered)],
        "ineligible_experiments": ineligible_report,
        "unselected_experiments": skipped_report,
        "constraint_semantics": {
            "dependencies": "Selecting an experiment also selects its transitive eligible dependencies.",
            "mutual_exclusion": "A declared exclusion is treated symmetrically.",
            "required": "Required experiments and their dependencies are selected before heuristic optimization.",
            "eligibility": "Selection requires eligible=true, feasibility_status=eligible, and ethics_status approved or not_required.",
        },
        "selection_rule": "Constrained greedy new pair-discrimination coverage per incremental bundle cost; ties prefer broader coverage, focal orthogonality, lower bundle cost, then stable experiment ID.",
        "optimization_note": "This is a deterministic feasibility-aware heuristic, not a proof of global optimality or scientific value. Compare alternatives when constraints or priorities are decision-sensitive.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--budget", required=True, type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = select(json.loads(args.input.read_text(encoding="utf-8")), args.budget)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
