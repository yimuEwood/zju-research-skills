#!/usr/bin/env python3
"""Audit structured claim atoms against source-anchored evidence facts."""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ORACLE_ID = "claim_atom_support_v1"
BAD_ANCHOR = re.compile(r"^(?:unknown|not[_ -]?supplied|n/?a|tbd)?$", re.I)


def _norm(value: Any) -> str:
    return " ".join(str(value if value is not None else "").casefold().split())


def _number(value: Any) -> Decimal | None:
    try:
        if isinstance(value, bool) or value in (None, ""):
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _same_value(left: Any, right: Any) -> bool:
    left_number, right_number = _number(left), _number(right)
    if left_number is not None or right_number is not None:
        return left_number is not None and right_number is not None and left_number == right_number
    return _norm(left) == _norm(right)


def _fact_matches(atom: dict[str, Any], fact: dict[str, Any]) -> bool:
    return bool(
        _norm(atom.get("key"))
        and _norm(atom.get("key")) == _norm(fact.get("key"))
        and _same_value(atom.get("value"), fact.get("value"))
        and _norm(atom.get("unit")) == _norm(fact.get("unit"))
    )


def audit(payload: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    fact_rows = payload.get("evidence_facts")
    claim_rows = payload.get("claims")
    if not isinstance(fact_rows, list) or not fact_rows:
        findings.append({"field": "evidence_facts", "message": "a non-empty evidence_facts list is required"})
        fact_rows = []
    if not isinstance(claim_rows, list) or not claim_rows:
        findings.append({"field": "claims", "message": "a non-empty claims list is required"})
        claim_rows = []

    facts: dict[str, dict[str, Any]] = {}
    for index, fact in enumerate(fact_rows):
        prefix = f"evidence_facts[{index}]"
        if not isinstance(fact, dict):
            findings.append({"field": prefix, "message": "fact must be an object"})
            continue
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id or fact_id in facts:
            findings.append({"field": f"{prefix}.fact_id", "message": "fact_id must be unique and non-empty"})
            continue
        for field in ("key", "value", "source_id", "source_anchor"):
            if fact.get(field) in (None, ""):
                findings.append({"field": f"{prefix}.{field}", "message": "required field missing"})
        if BAD_ANCHOR.match(str(fact.get("source_anchor") or "").strip()):
            findings.append({"field": f"{prefix}.source_anchor", "message": "an exact non-placeholder source anchor is required"})
        facts[fact_id] = fact

    claim_results: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    for claim_index, claim in enumerate(claim_rows):
        prefix = f"claims[{claim_index}]"
        if not isinstance(claim, dict):
            findings.append({"field": prefix, "message": "claim must be an object"})
            continue
        claim_id = str(claim.get("claim_id") or "")
        if not claim_id or claim_id in claim_ids:
            findings.append({"field": f"{prefix}.claim_id", "message": "claim_id must be unique and non-empty"})
        claim_ids.add(claim_id)
        atoms = claim.get("atoms")
        if not isinstance(atoms, list) or not atoms:
            findings.append({"field": f"{prefix}.atoms", "message": "claim requires at least one atom"})
            atoms = []
        atom_ids: set[str] = set()
        atom_results: list[dict[str, Any]] = []
        for atom_index, atom in enumerate(atoms):
            atom_prefix = f"{prefix}.atoms[{atom_index}]"
            if not isinstance(atom, dict):
                findings.append({"field": atom_prefix, "message": "atom must be an object"})
                continue
            atom_id = str(atom.get("atom_id") or "")
            if not atom_id or atom_id in atom_ids:
                findings.append({"field": f"{atom_prefix}.atom_id", "message": "atom_id must be unique within its claim"})
            atom_ids.add(atom_id)
            if atom.get("key") in (None, "") or atom.get("value") in (None, ""):
                findings.append({"field": atom_prefix, "message": "key and value are required"})
            links = atom.get("evidence_fact_ids")
            if not isinstance(links, list):
                findings.append({"field": f"{atom_prefix}.evidence_fact_ids", "message": "evidence_fact_ids must be a list"})
                links = []
            unknown = sorted({str(item) for item in links} - set(facts))
            if unknown:
                findings.append({"field": f"{atom_prefix}.evidence_fact_ids", "message": f"unknown fact IDs: {unknown}"})
            linked = [facts[str(item)] for item in links if str(item) in facts]
            same_key = [fact for fact in linked if _norm(fact.get("key")) == _norm(atom.get("key"))]
            supporting = [fact for fact in same_key if _fact_matches(atom, fact)]
            contradicting = [fact for fact in same_key if not _fact_matches(atom, fact)]
            if supporting and contradicting:
                status = "mixed"
            elif supporting:
                status = "supported"
            elif contradicting:
                status = "contradicted"
            else:
                status = "unsupported"
            declared = atom.get("declared_status")
            if declared is not None and declared != status:
                findings.append({"field": f"{atom_prefix}.declared_status", "message": f"declared {declared!r}, computed {status!r}"})
            material = atom.get("material", True) is not False
            if material and status != "supported":
                findings.append({"field": atom_prefix, "message": f"material claim atom is {status}"})
            atom_results.append({
                "atom_id": atom_id,
                "status": status,
                "material": material,
                "supporting_fact_ids": [str(item.get("fact_id")) for item in supporting],
                "contradicting_fact_ids": [str(item.get("fact_id")) for item in contradicting],
            })
        claim_results.append({
            "claim_id": claim_id,
            "status": "supported" if atom_results and all((not row["material"]) or row["status"] == "supported" for row in atom_results) else "not_supported",
            "atoms": atom_results,
        })

    return {
        "oracle_id": ORACLE_ID,
        "valid": bool(claim_results) and not findings,
        "claims": claim_results,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.input.read_text(encoding="utf-8-sig")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
