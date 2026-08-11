#!/usr/bin/env python3
"""Validate patent feature support and formal-claim eligibility."""

from __future__ import annotations

import argparse
import json
import re
from collections import deque
from pathlib import Path
from typing import Any


SUPPORT = {"explicit", "inherent", "needs_confirmation", "unsupported"}
FORMAL = {"independent", "dependent"}
CLAIM_ROLES = {"independent", "dependent"}
PLACEHOLDER = re.compile(r"(?:TO\s*CONFIRM|SOURCE_REQUIRED|NOT_SUPPLIED|UNKNOWN|TBD)", re.I)
EXACT_ANCHOR = re.compile(
    r"(?:\bp(?:age)?\.?\s*\d|\bfig(?:ure)?\s*\d|\btable\s*\d|\beq(?:uation)?\s*\d|"
    r"\bsection\s*[A-Za-z0-9]|\bpara(?:graph)?\s*\d|\blines?\s*\d|"
    r"\bcode(?:\s+line)?\s*\d|\bcell\s*[A-Za-z0-9]|\bexperiment\s*[A-Za-z0-9]|"
    r"\brecord\s*[A-Za-z0-9]|\u9875\s*\d|\u56fe\s*\d|\u8868\s*\d|"
    r"\u516c\u5f0f\s*\d|\u7ae0\u8282\s*[A-Za-z0-9]|\u6bb5\s*\d|"
    r"\u884c\s*\d|\u5b9e\u9a8c\s*[A-Za-z0-9]|\u8bb0\u5f55\s*[A-Za-z0-9])",
    re.I,
)
NUMBER = re.compile(r"(?<![A-Za-z0-9_])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?(?![A-Za-z0-9_])")
NUMERIC_FIELDS = (
    "normalized_term",
    "description",
    "verbatim_feature",
    "technical_effect",
    "parameters",
    "parameter_ranges",
    "conditions",
)


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def string_list(value: Any) -> list[str]:
    return [str(item) for item in as_list(value) if item not in (None, "")]


def add_finding(findings: list[dict[str, str]], field: str, message: str, severity: str = "error") -> None:
    findings.append({"severity": severity, "field": field, "message": message})


def index_rows(rows: Any, id_field: str, field: str, findings: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for position, row in enumerate(as_list(rows), 1):
        if not isinstance(row, dict):
            add_finding(findings, f"{field}[{position}]", "row must be an object")
            continue
        row_id = str(row.get(id_field) or "")
        if not row_id:
            add_finding(findings, f"{field}[{position}].{id_field}", "required field missing")
        elif row_id in indexed:
            add_finding(findings, f"{field}[{position}].{id_field}", f"duplicate ID: {row_id}")
        else:
            indexed[row_id] = row
    return indexed


def require_fields(row: dict[str, Any], fields: tuple[str, ...], prefix: str, findings: list[dict[str, str]]) -> None:
    for field in fields:
        if row.get(field) in (None, "", []):
            add_finding(findings, f"{prefix}.{field}", "required field missing")


def check_links(values: Any, known: set[str], field: str, findings: list[dict[str, str]]) -> set[str]:
    links = set(string_list(values))
    unknown = sorted(links - known)
    if unknown:
        add_finding(findings, field, f"unknown IDs: {unknown}")
    return links


def declared_evidence_ids(payload: dict[str, Any]) -> set[str]:
    declared = set(source_ids(payload))
    containers = (
        ("evidence", ("evidence", "records")),
        ("results", ("results", "records")),
        ("artifacts", ("artifacts", "records")),
        ("result_registry", ("results",)),
        ("canonical_result_registry", ("results",)),
        ("artifact_inventory", ("artifacts",)),
        ("data_inventory", ("artifacts",)),
    )
    for field, collection_fields in containers:
        container = payload.get(field)
        rows = container if isinstance(container, list) else []
        if isinstance(container, dict):
            rows = []
            for collection_field in collection_fields:
                if isinstance(container.get(collection_field), list):
                    rows.extend(container[collection_field])
        for row in rows:
            if not isinstance(row, dict):
                continue
            for id_field in ("evidence_id", "result_id", "artifact_id", "source_id"):
                if row.get(id_field) not in (None, ""):
                    declared.add(str(row[id_field]))
    return declared


def claim_dependency_order(claims: dict[str, dict[str, Any]]) -> tuple[list[str], list[str]]:
    indegree = {claim_id: 0 for claim_id in claims}
    downstream = {claim_id: set() for claim_id in claims}
    for claim_id, row in claims.items():
        for parent in set(string_list(row.get("parent_claim_ids"))) & set(claims):
            downstream[parent].add(claim_id)
            indegree[claim_id] += 1
    ready = deque(sorted(claim_id for claim_id, degree in indegree.items() if degree == 0))
    order: list[str] = []
    while ready:
        current = ready.popleft()
        order.append(current)
        for child in sorted(downstream[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    cycle = sorted(set(claims) - set(order))
    return order, cycle


def boolean_block(terms: Any) -> str:
    unique: list[str] = []
    for term in string_list(terms):
        cleaned = " ".join(term.split()).replace('"', "")
        if cleaned and cleaned.casefold() not in {item.casefold() for item in unique}:
            unique.append(cleaned)
    return "(" + " OR ".join(f'"{term}"' for term in unique) + ")" if unique else ""


def invention_maps(payload: dict[str, Any]) -> dict[str, Any]:
    features = {
        str(row.get("feature_id")): row
        for row in as_list(payload.get("features"))
        if isinstance(row, dict) and row.get("feature_id")
    }
    concepts = {
        str(row.get("concept_id")): row
        for row in as_list(payload.get("technical_concepts"))
        if isinstance(row, dict) and row.get("concept_id")
    }
    claims = {
        str(row.get("claim_id")): row
        for row in as_list(payload.get("claim_candidates"))
        if isinstance(row, dict) and row.get("claim_id")
    }
    order, cycle = claim_dependency_order(claims)
    problem_solution_effect = [
        {
            "concept_id": concept_id,
            "problem": row.get("problem"),
            "solution_feature_ids": string_list(row.get("solution_feature_ids")),
            "technical_effect": row.get("technical_effect"),
            "evidence_ids": string_list(row.get("evidence_ids")),
            "discriminating_evidence_ids": string_list(row.get("discriminating_evidence_ids")),
        }
        for concept_id, row in sorted(concepts.items())
    ]
    feature_evidence = [
        {
            "feature_id": feature_id,
            "term": row.get("normalized_term"),
            "support_state": row.get("support_state"),
            "source_ids": string_list(row.get("source_ids")),
            "source_anchors": feature_anchors(row, string_list(row.get("source_ids"))),
            "evidence_ids": string_list(row.get("evidence_ids")) or string_list(row.get("result_ids")),
            "claim_role": row.get("claim_role"),
        }
        for feature_id, row in sorted(features.items())
    ]
    dependencies = [
        {
            "claim_id": claim_id,
            "claim_role": claims[claim_id].get("claim_role"),
            "parent_claim_ids": string_list(claims[claim_id].get("parent_claim_ids")),
            "concept_ids": string_list(claims[claim_id].get("concept_ids")),
            "feature_ids": string_list(claims[claim_id].get("feature_ids")),
        }
        for claim_id in order + cycle
    ]
    query_map: list[dict[str, Any]] = []
    for position, row in enumerate(as_list(payload.get("prior_art_terms")), 1):
        if not isinstance(row, dict):
            continue
        blocks = {
            "problem": boolean_block(row.get("problem_terms")),
            "solution": boolean_block(row.get("solution_terms")),
            "effect": boolean_block(row.get("effect_terms")),
            "synonym": boolean_block(row.get("synonyms")),
        }
        query = " AND ".join(block for key, block in blocks.items() if key in {"problem", "solution", "effect"} and block)
        query_map.append({
            "query_id": row.get("query_id") or f"Q-{position:03d}",
            "concept_ids": string_list(row.get("concept_ids")),
            "target_feature_ids": string_list(row.get("target_feature_ids")),
            "boolean_query": query,
            "synonym_block": blocks["synonym"],
            "classification_hints": string_list(row.get("classification_hints")),
            "date_cutoff": row.get("date_cutoff"),
            "target_sources": string_list(row.get("target_sources")),
            "status": "planned",
        })
    alternatives = []
    for row in as_list(payload.get("alternative_embodiments")):
        if not isinstance(row, dict):
            continue
        alternatives.append({
            **row,
            "discrimination_ready": bool(
                row.get("support_state") in {"explicit", "inherent"}
                and string_list(row.get("discriminating_evidence_ids"))
            ),
        })
    return {
        "problem_solution_effect_map": problem_solution_effect,
        "feature_evidence_map": feature_evidence,
        "claim_dependency_map": dependencies,
        "claim_dependency_order": order,
        "claim_dependency_cycle": cycle,
        "prior_art_query_map": query_map,
        "alternative_embodiment_map": alternatives,
    }


def source_ids(payload: dict[str, Any]) -> set[str]:
    declared: set[str] = set()
    for field in ("sources", "source_inventory"):
        rows = payload.get(field, [])
        if isinstance(rows, dict):
            rows = rows.get("sources", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("source_id") not in (None, ""):
                declared.add(str(row["source_id"]))
    return declared


def anchor_text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("anchor") or value.get("source_anchor") or value.get("locator") or ""
    return str(value or "").strip()


def is_exact_anchor(value: Any) -> bool:
    text = anchor_text(value)
    return bool(text and not PLACEHOLDER.search(text) and EXACT_ANCHOR.search(text))


def feature_anchors(feature: dict[str, Any], feature_sources: list[str]) -> dict[str, Any]:
    anchors: dict[str, Any] = {}
    structured = feature.get("source_anchors", [])
    if isinstance(structured, dict):
        anchors.update({str(key): value for key, value in structured.items()})
    elif isinstance(structured, list):
        for row in structured:
            if isinstance(row, dict) and row.get("source_id") not in (None, ""):
                anchors[str(row["source_id"])] = row
    direct = feature.get("source_anchor")
    if isinstance(direct, dict) and direct.get("source_id") not in (None, ""):
        anchors[str(direct["source_id"])] = direct
    elif direct not in (None, "") and len(feature_sources) == 1:
        anchors[feature_sources[0]] = direct
    return anchors


def numeric_literals(value: Any) -> set[str]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, dict):
        found: set[str] = set()
        for item in value.values():
            found.update(numeric_literals(item))
        return found
    if isinstance(value, (list, tuple)):
        found = set()
        for item in value:
            found.update(numeric_literals(item))
        return found
    return set(NUMBER.findall(str(value)))


def feature_numbers(feature: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for field in NUMERIC_FIELDS:
        if field in feature:
            found.update(numeric_literals(feature[field]))
    return found


def checks_for_feature(payload: dict[str, Any], feature: dict[str, Any], feature_id: str) -> list[dict[str, Any]]:
    checks = feature.get("numeric_invariant_checks", [])
    rows = list(checks) if isinstance(checks, list) else []
    shared = payload.get("source_invariant_checks", [])
    if isinstance(shared, list):
        rows.extend(
            row for row in shared
            if isinstance(row, dict) and str(row.get("feature_id") or "") == feature_id
        )
    return [row for row in rows if isinstance(row, dict)]


def valid_numeric_check(check: dict[str, Any], literal: str, declared: set[str], feature_sources: set[str]) -> bool:
    check_source = str(check.get("source_id") or "")
    source_value = str(check.get("source_value") or "").strip()
    copied_value = str(check.get("copied_value") or "").strip()
    return bool(
        str(check.get("literal") or "") == literal
        and check_source in declared
        and check_source in feature_sources
        and is_exact_anchor(check.get("source_anchor"))
        and str(check.get("status") or "").lower() in {"match", "verified", "passed"}
        and source_value
        and source_value == copied_value
        and literal in numeric_literals(source_value)
        and literal in numeric_literals(copied_value)
    )


def validate(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    features = payload.get("features", [])
    declared_sources = source_ids(payload)
    ids: set[str] = set()
    for index, feature in enumerate(features, 1):
        if not isinstance(feature, dict):
            add_finding(findings, f"features[{index}]", "row must be an object")
            continue
        feature_id = str(feature.get("feature_id", ""))
        if not feature_id or feature_id in ids:
            add_finding(findings, f"features[{index}].feature_id", "missing or duplicate feature ID")
        ids.add(feature_id)
        for field in ("normalized_term", "description", "support_state", "claim_role", "confidentiality"):
            if feature.get(field) in (None, ""):
                add_finding(findings, f"features[{index}].{field}", "required field missing")
        state = feature.get("support_state")
        if state not in SUPPORT:
            add_finding(findings, f"features[{index}].support_state", "invalid support state")
        if feature.get("claim_role") in FORMAL:
            raw_feature_sources = feature.get("source_ids")
            feature_sources = [str(value) for value in raw_feature_sources] if isinstance(raw_feature_sources, list) else []
            if state not in {"explicit", "inherent"} or not feature_sources:
                add_finding(findings, f"features[{index}]", "formal claim feature lacks sufficient sourced support")
            unknown_sources = sorted(set(feature_sources) - declared_sources)
            for source_id in unknown_sources:
                add_finding(findings, f"features[{index}].source_ids", f"formal claim references undeclared source_id: {source_id}")
            anchors = feature_anchors(feature, feature_sources)
            for source_id in feature_sources:
                if not is_exact_anchor(anchors.get(source_id)):
                    add_finding(findings, f"features[{index}].source_anchor", f"formal claim requires an exact non-placeholder source anchor for {source_id}")
            checks = checks_for_feature(payload, feature, feature_id)
            check_sources = {str(check.get("source_id") or "") for check in checks}
            for source_id in sorted(check_sources - declared_sources - {""}):
                add_finding(findings, f"features[{index}].numeric_invariant_checks", f"numeric invariant check references undeclared source_id: {source_id}")
            for literal in sorted(feature_numbers(feature)):
                if not any(valid_numeric_check(check, literal, declared_sources, set(feature_sources)) for check in checks):
                    add_finding(findings, f"features[{index}].numeric_invariant_checks", f"numeric literal {literal} lacks a matching digit-preserving check against a declared source and exact anchor")
            if "TO CONFIRM" in str(feature):
                add_finding(findings, f"features[{index}]", "placeholder cannot appear in formal claim feature")

    v2 = str(payload.get("workflow_version") or "") == "2.0"
    unresolved_alternatives = 0
    if v2:
        evidence_ids = declared_evidence_ids(payload)
        concepts = index_rows(payload.get("technical_concepts"), "concept_id", "technical_concepts", findings)
        claims = index_rows(payload.get("claim_candidates"), "claim_id", "claim_candidates", findings)
        queries = index_rows(payload.get("prior_art_terms"), "query_id", "prior_art_terms", findings)
        alternatives = index_rows(payload.get("alternative_embodiments"), "embodiment_id", "alternative_embodiments", findings)
        if not concepts:
            add_finding(findings, "technical_concepts", "workflow 2.0 requires a problem-solution-effect concept")
        if not claims:
            add_finding(findings, "claim_candidates", "workflow 2.0 requires a claim-dependency drafting outline")
        if not queries:
            add_finding(findings, "prior_art_terms", "workflow 2.0 requires a concept-linked prior-art query plan")
        if not alternatives:
            add_finding(findings, "alternative_embodiments", "workflow 2.0 requires at least one supported alternative or explicit confirmation candidate")

        for concept_id, row in concepts.items():
            prefix = f"technical_concepts[{concept_id}]"
            require_fields(
                row,
                ("problem", "solution_feature_ids", "technical_effect", "evidence_ids", "discriminating_evidence_ids"),
                prefix,
                findings,
            )
            check_links(row.get("solution_feature_ids"), ids, f"{prefix}.solution_feature_ids", findings)
            check_links(row.get("evidence_ids"), evidence_ids, f"{prefix}.evidence_ids", findings)
            check_links(row.get("discriminating_evidence_ids"), evidence_ids, f"{prefix}.discriminating_evidence_ids", findings)

        for claim_id, row in claims.items():
            prefix = f"claim_candidates[{claim_id}]"
            require_fields(row, ("claim_role", "concept_ids", "feature_ids"), prefix, findings)
            if not isinstance(row.get("parent_claim_ids"), list):
                add_finding(findings, f"{prefix}.parent_claim_ids", "parent_claim_ids must be a list; use [] for an independent candidate")
            role = row.get("claim_role")
            if role not in CLAIM_ROLES:
                add_finding(findings, f"{prefix}.claim_role", "invalid claim role")
            parents = check_links(row.get("parent_claim_ids"), set(claims), f"{prefix}.parent_claim_ids", findings)
            linked_features = check_links(row.get("feature_ids"), ids, f"{prefix}.feature_ids", findings)
            check_links(row.get("concept_ids"), set(concepts), f"{prefix}.concept_ids", findings)
            if role == "independent" and parents:
                add_finding(findings, f"{prefix}.parent_claim_ids", "independent candidate cannot have a parent")
            if role == "dependent" and not parents:
                add_finding(findings, f"{prefix}.parent_claim_ids", "dependent candidate requires a parent")
            for feature_id in linked_features:
                feature = next((item for item in features if isinstance(item, dict) and str(item.get("feature_id")) == feature_id), {})
                if feature.get("support_state") not in {"explicit", "inherent"}:
                    add_finding(findings, f"{prefix}.feature_ids", f"claim candidate uses unsupported feature: {feature_id}")
            if role == "dependent" and parents <= set(claims):
                parent_features = {
                    feature_id
                    for parent in parents
                    for feature_id in string_list(claims[parent].get("feature_ids"))
                }
                if linked_features <= parent_features:
                    add_finding(findings, f"{prefix}.feature_ids", "dependent candidate must add at least one feature beyond its direct parent")
        _, cycle = claim_dependency_order(claims)
        if cycle:
            add_finding(findings, "claim_candidates.parent_claim_ids", f"claim dependency cycle: {cycle}")

        for query_id, row in queries.items():
            prefix = f"prior_art_terms[{query_id}]"
            require_fields(
                row,
                ("concept_ids", "target_feature_ids", "problem_terms", "solution_terms", "effect_terms", "date_cutoff", "target_sources"),
                prefix,
                findings,
            )
            check_links(row.get("concept_ids"), set(concepts), f"{prefix}.concept_ids", findings)
            check_links(row.get("target_feature_ids"), ids, f"{prefix}.target_feature_ids", findings)

        for embodiment_id, row in alternatives.items():
            prefix = f"alternative_embodiments[{embodiment_id}]"
            require_fields(
                row,
                ("concept_id", "replaces_feature_ids", "alternative_feature_ids", "target_effect", "support_state"),
                prefix,
                findings,
            )
            for evidence_field in ("evidence_ids", "discriminating_evidence_ids"):
                if not isinstance(row.get(evidence_field), list):
                    add_finding(findings, f"{prefix}.{evidence_field}", f"{evidence_field} must be a list; use [] for an unresolved confirmation candidate")
            if str(row.get("concept_id") or "") not in concepts:
                add_finding(findings, f"{prefix}.concept_id", "unknown concept ID")
            replaced = check_links(row.get("replaces_feature_ids"), ids, f"{prefix}.replaces_feature_ids", findings)
            alternative = check_links(row.get("alternative_feature_ids"), ids, f"{prefix}.alternative_feature_ids", findings)
            if replaced & alternative:
                add_finding(findings, prefix, "replacement and alternative feature sets must differ")
            state = row.get("support_state")
            if state not in SUPPORT:
                add_finding(findings, f"{prefix}.support_state", "invalid support state")
            check_links(row.get("evidence_ids"), evidence_ids, f"{prefix}.evidence_ids", findings)
            discriminating = check_links(
                row.get("discriminating_evidence_ids"),
                evidence_ids,
                f"{prefix}.discriminating_evidence_ids",
                findings,
            )
            if state in {"explicit", "inherent"} and not string_list(row.get("evidence_ids")):
                add_finding(findings, f"{prefix}.evidence_ids", "supported alternative requires effect evidence")
            if state not in {"explicit", "inherent"} or not discriminating:
                unresolved_alternatives += 1
                add_finding(
                    findings,
                    prefix,
                    "alternative remains an inventor confirmation item until supported by discriminating evidence",
                    severity="warning",
                )
            for feature_id in alternative:
                feature = next((item for item in features if isinstance(item, dict) and str(item.get("feature_id")) == feature_id), {})
                feature_sources = string_list(feature.get("source_ids"))
                anchors = feature_anchors(feature, feature_sources)
                if feature.get("support_state") not in {"explicit", "inherent"} or not feature_sources:
                    add_finding(findings, f"{prefix}.alternative_feature_ids", f"alternative feature lacks sourced support: {feature_id}")
                for source_id in feature_sources:
                    if source_id not in declared_sources or not is_exact_anchor(anchors.get(source_id)):
                        add_finding(findings, f"{prefix}.alternative_feature_ids", f"alternative feature lacks declared source and exact anchor: {feature_id}")
        for field in ("novelty_conclusion", "patentability_conclusion", "freedom_to_operate_conclusion"):
            if payload.get(field) not in (None, "", "not_assessed"):
                add_finding(findings, field, "technical map cannot contain a legal conclusion")

    maps = invention_maps(payload)
    errors = [item for item in findings if item["severity"] == "error"]
    return {
        "valid": bool(features) and not errors,
        "invention_map_ready": bool(v2 and features and not errors and unresolved_alternatives == 0),
        "features": len(features),
        "declared_sources": len(declared_sources),
        **maps,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    args = parser.parse_args()
    result = validate(json.loads(args.input.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
