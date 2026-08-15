#!/usr/bin/env python3
"""Render a validated proposal manifest into a traceable DOCX package."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from validate_proposal_manifest import validate


WRITING_SCRIPTS = Path(__file__).resolve().parents[2] / "zju-scientific-writing" / "scripts"
if str(WRITING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WRITING_SCRIPTS))
from build_research_docx import build_docx  # noqa: E402


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def proposal_document_source(payload: dict[str, Any]) -> dict[str, Any]:
    evidence_rows = [item for item in payload.get("evidence", []) if isinstance(item, dict)]
    sources = []
    for item in evidence_rows:
        source_id = str(item.get("evidence_id") or "").strip()
        anchor = str(item.get("source_anchor") or "").strip()
        citation = str(item.get("citation") or item.get("title") or source_id).strip()
        if source_id and anchor:
            sources.append({"source_id": source_id, "citation": citation, "anchor": anchor})
    if not sources:
        raise ValueError("proposal rendering requires evidence records with stable source_anchor values")
    known_sources = {item["source_id"] for item in sources}

    sections: list[dict[str, Any]] = []
    objective_paragraphs = []
    for objective in payload.get("objectives", []):
        if not isinstance(objective, dict):
            continue
        source_ids = [item for item in _strings(objective.get("evidence_ids")) if item in known_sources]
        objective_paragraphs.append({
            "text": (
                f"{objective.get('objective_id')}: {objective.get('question')}. "
                f"Success criteria: {'; '.join(_strings(objective.get('success_criteria')))}"
            ),
            "source_ids": source_ids,
        })
    if objective_paragraphs:
        sections.append({"heading": "Research objectives", "level": 1, "paragraphs": objective_paragraphs})

    hypothesis_bullets = []
    for hypothesis in payload.get("hypotheses", []):
        if not isinstance(hypothesis, dict):
            continue
        source_ids = [item for item in _strings(hypothesis.get("evidence_ids")) if item in known_sources]
        hypothesis_bullets.append({
            "text": (
                f"{hypothesis.get('hypothesis_id')}: {hypothesis.get('statement')}; "
                f"predictions: {'; '.join(_strings(hypothesis.get('predictions')))}; "
                f"falsifiers: {'; '.join(_strings(hypothesis.get('falsifiers')))}"
            ),
            "source_ids": source_ids,
        })
    if hypothesis_bullets:
        sections.append({"heading": "Competing hypotheses", "level": 1, "bullets": hypothesis_bullets})

    work_package_paragraphs = []
    for package in payload.get("work_packages", []):
        if not isinstance(package, dict):
            continue
        source_ids = [item for item in _strings(package.get("evidence_ids")) if item in known_sources]
        timeline = package.get("timeline") if isinstance(package.get("timeline"), dict) else {}
        gate = package.get("decision_gate") if isinstance(package.get("decision_gate"), dict) else {}
        work_package_paragraphs.append({
            "text": (
                f"{package.get('work_package_id')} (months {timeline.get('start_month')}–{timeline.get('end_month')}): "
                f"methods: {'; '.join(_strings(package.get('methods')))}. "
                f"Outputs: {'; '.join(_strings(package.get('outputs')))}. "
                f"Decision metric: {gate.get('metric')}."
            ),
            "source_ids": source_ids,
        })
    if work_package_paragraphs:
        sections.append({"heading": "Work packages and decision gates", "level": 1, "paragraphs": work_package_paragraphs})

    capability_bullets = []
    for capability in payload.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        source_ids = [item for item in _strings(capability.get("evidence_ids")) if item in known_sources]
        capability_bullets.append({
            "text": (
                f"{capability.get('item')} — {capability.get('status')}. "
                f"Constraint: {capability.get('constraint') or 'none declared'}. "
                f"Mitigation: {capability.get('mitigation') or 'not required for available capability'}."
            ),
            "source_ids": source_ids,
        })
    if capability_bullets:
        sections.append({"heading": "Feasibility and capability evidence", "level": 1, "bullets": capability_bullets})

    risk_bullets = []
    for risk in payload.get("risks", []):
        if isinstance(risk, dict):
            risk_bullets.append(
                f"{risk.get('risk_id') or risk.get('risk')}: {risk.get('risk') or risk.get('description')}; "
                f"mitigation: {risk.get('mitigation')}"
            )
        elif str(risk).strip():
            risk_bullets.append(str(risk))
    if risk_bullets:
        sections.append({"heading": "Risks and mitigations", "level": 1, "bullets": risk_bullets})

    compliance_bullets = []
    for item in payload.get("compliance", []):
        if isinstance(item, dict):
            compliance_bullets.append("; ".join(f"{key}: {value}" for key, value in sorted(item.items())))
        elif str(item).strip():
            compliance_bullets.append(str(item))
    if compliance_bullets:
        sections.append({"heading": "Compliance", "level": 1, "bullets": compliance_bullets})
    if not sections:
        raise ValueError("proposal manifest did not produce any document sections")

    return {
        "document_id": str(payload.get("proposal_id")),
        "title": str(payload.get("title") or f"Research proposal — {payload.get('proposal_id')}"),
        "subtitle": f"Mode: {payload.get('mode')} | Scheme status: {payload.get('scheme_status')}",
        "sections": sections,
        "sources": sources,
    }


def render(
    manifest_path: Path,
    output_path: Path,
    *,
    registry_path: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("proposal manifest must be a JSON object")
    report = validate(payload)
    if not report["valid"]:
        raise ValueError(f"proposal manifest is invalid: {report['findings']}")
    source_payload = proposal_document_source(payload)
    source_path = output_path.resolve().with_suffix(".source.json")
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(
        json.dumps(source_payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    artifact_manifest = build_docx(
        source_path,
        output_path,
        registry_path=registry_path,
        document_kind="proposal",
    )
    artifact_manifest["proposal_validation"] = {
        "valid": True,
        "workflow_version": payload.get("workflow_version"),
        "execution_plan_ready": report.get("execution_plan_ready"),
        "ready_to_start": report.get("ready_to_start"),
    }
    artifact_manifest["proposal_manifest"] = {
        "path": manifest_path.resolve().name,
        "proposal_id": payload.get("proposal_id"),
    }
    return artifact_manifest, source_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--proposal-manifest", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-manifest", type=Path)
    args = parser.parse_args()
    try:
        artifact, source_path = render(
            args.proposal_manifest,
            args.output,
            registry_path=args.registry,
        )
        artifact_path = args.artifact_manifest or args.output.with_suffix(".manifest.json")
        artifact_path.write_text(
            json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "output": str(args.output.resolve()),
        "source": str(source_path),
        "manifest": str(artifact_path.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
