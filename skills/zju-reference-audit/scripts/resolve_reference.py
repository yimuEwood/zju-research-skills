#!/usr/bin/env python3
"""Resolve one DOI or PMID across bounded scholarly provider searches."""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


LITERATURE_SCRIPTS = Path(__file__).resolve().parents[2] / "zju-literature-search" / "scripts"
if str(LITERATURE_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(LITERATURE_SCRIPTS))

from query_providers import PROVIDERS, ProviderError, search  # noqa: E402


SCHEMA_VERSION = "1.0"
PMID_RE = re.compile(r"^\d{1,12}$")
OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/yimuEwood/zju-research-skills/schemas/reference-resolution-1.0.json",
    "title": "ZJU multi-provider reference resolution",
    "type": "object",
    "required": ["schema_version", "identifier", "status", "matched_sources", "canonical", "field_evidence", "provider_search"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "status": {"enum": ["verified_two_source", "single_source_only", "not_found", "source_conflict"]},
        "matched_sources": {"type": "array", "items": {"type": "string"}},
        "canonical": {"type": ["object", "null"]},
        "field_evidence": {"type": "array"},
    },
}


class ResolutionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_doi(value: Any) -> str:
    raw = " ".join(str(value or "").split()).casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    return raw.rstrip(".,;)")


def normalize_text(value: Any) -> str:
    value = unicodedata.normalize("NFKC", " ".join(str(value or "").split())).casefold()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def _author_sequence(values: list[Any]) -> tuple[str, ...]:
    particles = {"al", "da", "de", "del", "der", "di", "du", "la", "le", "van", "von"}
    result = []
    for value in values:
        raw = " ".join(str(value or "").split())
        parts = raw.split()
        if not parts:
            continue
        final_letters = re.sub(r"[^A-Za-z]", "", parts[-1])
        pubmed_initials = bool(final_letters) and len(final_letters) <= 4 and final_letters.isupper()
        if pubmed_initials and len(parts) > 1:
            surname_parts = parts[:-1]
        else:
            surname_parts = [parts[-1]]
            cursor = len(parts) - 2
            while cursor >= 0 and normalize_text(parts[cursor]) in particles:
                surname_parts.insert(0, parts[cursor])
                cursor -= 1
        result.append(normalize_text(" ".join(surname_parts)))
    return tuple(result)


def _matched(record: dict[str, Any], identifier: dict[str, str]) -> bool:
    if identifier["doi"]:
        return normalize_doi(record.get("doi")) == identifier["doi"]
    return str(record.get("pmid") or "") == identifier["pmid"]


def _field_evidence(records: list[dict[str, Any]], field: str) -> dict[str, Any]:
    rows = []
    for record in records:
        value = record.get(field)
        if value not in (None, "", []):
            rows.append({"provider": record["provider"], "value": value})
    if not rows:
        return {"field": field, "status": "not_verified", "values": []}
    if field == "authors":
        normalized = [tuple(normalize_text(item) for item in row["value"]) for row in rows]
    elif field == "doi":
        normalized = [normalize_doi(row["value"]) for row in rows]
    elif field == "year":
        normalized = [row["value"] for row in rows]
    else:
        normalized = [normalize_text(row["value"]) for row in rows]
    first = normalized[0]
    exact = all(value == first for value in normalized[1:])
    if exact:
        status = "match" if len(rows) >= 2 else "single_source"
    elif field == "authors" and all(_author_sequence(row["value"]) == _author_sequence(rows[0]["value"]) for row in rows[1:]):
        status = "minor_difference"
    elif field in {"title", "venue"} and all(
        difflib.SequenceMatcher(None, str(first), str(value)).ratio() >= 0.94 for value in normalized[1:]
    ):
        status = "minor_difference"
    else:
        status = "source_conflict"
    return {"field": field, "status": status, "values": rows}


def resolve_reference(
    *,
    doi: str = "",
    pmid: str = "",
    providers: list[str] | None = None,
    live: bool = False,
    snapshot_dir: Path | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    identifier = {"doi": normalize_doi(doi), "pmid": " ".join(str(pmid or "").split())}
    if sum(bool(value) for value in identifier.values()) != 1:
        raise ResolutionError("invalid_identifier", "supply exactly one of DOI or PMID")
    if identifier["doi"] and not identifier["doi"].startswith("10."):
        raise ResolutionError("invalid_identifier", "DOI must begin with 10.")
    if identifier["pmid"] and not PMID_RE.fullmatch(identifier["pmid"]):
        raise ResolutionError("invalid_identifier", "PMID must contain digits only")
    selected = providers or list(PROVIDERS)
    query = identifier["doi"] or identifier["pmid"]
    provider_result = search(
        query,
        selected,
        page_size=10,
        pages=1,
        live=live,
        snapshot_dir=snapshot_dir,
        cache_dir=cache_dir,
    )
    matches_by_provider: dict[str, dict[str, Any]] = {}
    for record in provider_result["records"]:
        if _matched(record, identifier) and record["provider"] not in matches_by_provider:
            matches_by_provider[record["provider"]] = record
    matches = [matches_by_provider[name] for name in selected if name in matches_by_provider]
    fields = ["doi", "pmid", "pmcid", "title", "authors", "year", "venue"]
    evidence = [_field_evidence(matches, field) for field in fields]
    conflicts = [row["field"] for row in evidence if row["status"] == "source_conflict"]
    if not matches:
        status, canonical = "not_found", None
    else:
        # Preserve a primary registry value while exposing every disagreement.
        priority = {"crossref": 0, "pubmed": 1, "europepmc": 2, "openalex": 3}
        canonical = min(matches, key=lambda item: priority.get(item["provider"], 99)).copy()
        if conflicts:
            status = "source_conflict"
        elif len(matches) >= 2:
            status = "verified_two_source"
        else:
            status = "single_source_only"
    return {
        "schema_version": SCHEMA_VERSION,
        "identifier": identifier,
        "status": status,
        "matched_sources": [record["provider"] for record in matches],
        "canonical": canonical,
        "field_evidence": evidence,
        "conflicting_fields": conflicts,
        "provider_search": {
            "mode": provider_result["execution"]["mode"],
            "failures": provider_result["failures"],
            "providers_attempted": selected,
            "records_examined": len(provider_result["records"]),
            "exact_identifier_matches": len(matches),
        },
        "human_confirmation_required": status in {"not_found", "single_source_only", "source_conflict"},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--doi")
    group.add_argument("--pmid")
    parser.add_argument("--providers", default=",".join(PROVIDERS))
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    try:
        if args.output is None:
            raise ResolutionError("missing_output", "--output is required")
        selected = [item.strip().casefold() for item in args.providers.split(",") if item.strip()]
        result = resolve_reference(
            doi=args.doi or "", pmid=args.pmid or "", providers=selected,
            live=args.live, snapshot_dir=args.snapshot_dir, cache_dir=args.cache_dir,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(args.output), "status": result["status"]}, ensure_ascii=False))
        return 0 if result["status"] == "verified_two_source" else 5
    except ResolutionError as exc:
        print(json.dumps({"ok": False, "error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2
    except ProviderError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "local_io_error", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
