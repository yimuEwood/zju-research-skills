#!/usr/bin/env python3
"""Normalize and deduplicate scholarly records without network access."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)
RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalize_doi(value: Any) -> str:
    return DOI_PREFIX.sub("", text(value)).strip().lower().rstrip(".,;)")


def normalize_title(value: Any) -> str:
    value = unicodedata.normalize("NFKC", text(value)).casefold()
    return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()


def normalize_authors(value: Any) -> list[str]:
    if isinstance(value, list):
        return [text(item) for item in value if text(item)]
    raw = text(value)
    return [item.strip() for item in re.split(r"\s*;\s*", raw) if item.strip()]


def valid_record_id(value: Any) -> str:
    candidate = text(value)
    return candidate if RECORD_ID.fullmatch(candidate) else ""


def first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if record.get(key) not in (None, "", []):
            return record[key]
    return ""


def canonicalize(record: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(first(record, "doi", "DOI"))
    pmid = text(first(record, "pmid", "PMID"))
    title = text(first(record, "title", "Title"))
    year_raw = first(record, "year", "publication_year", "published")
    match = re.search(r"(?:19|20)\d{2}", text(year_raw))
    year = int(match.group()) if match else None
    sources = first(record, "source_database", "source", "database")
    if not isinstance(sources, list):
        sources = [text(sources)] if text(sources) else []
    query_ids = first(record, "query_ids", "query_id")
    if not isinstance(query_ids, list):
        query_ids = [text(query_ids)] if text(query_ids) else []
    supplied_record_id = text(first(record, "record_id", "Record ID"))
    preserved_record_id = valid_record_id(supplied_record_id)
    return {
        "title": title,
        "authors": normalize_authors(first(record, "authors", "author")),
        "year": year,
        "venue": text(first(record, "venue", "journal", "container_title")),
        "abstract": text(first(record, "abstract", "summary")),
        "doi": doi,
        "pmid": pmid,
        "other_identifier": text(first(record, "other_identifier", "id", "arxiv_id")),
        "landing_url": text(first(record, "landing_url", "url", "URL")),
        "source_database": sorted(set(sources)),
        "query_ids": sorted(set(query_ids)),
        "retrieved_at": text(first(record, "retrieved_at", "retrieval_date")),
        "verification_status": text(first(record, "verification_status")) or "metadata_unverified",
        "_record_id_candidates": [preserved_record_id] if preserved_record_id else [],
        "_invalid_record_ids": [supplied_record_id] if supplied_record_id and not preserved_record_id else [],
    }


def identity(record: dict[str, Any]) -> str:
    if record["doi"]:
        return f"doi:{record['doi']}"
    if record["pmid"]:
        return f"pmid:{record['pmid']}"
    if record["other_identifier"]:
        return f"other:{record['other_identifier'].casefold()}"
    title_key = normalize_title(record["title"])
    if title_key:
        author_key = "|".join(normalize_title(author) for author in record.get("authors", []))
        return f"title-year-author:{title_key}:{record['year'] or ''}:{author_key}"
    if record["landing_url"]:
        return f"url:{record['landing_url'].strip().casefold()}"
    candidates = record.get("_record_id_candidates", [])
    if candidates:
        return f"existing-record-id:{candidates[0]}"
    return ""


def merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key in (
        "source_database", "query_ids", "source_record_positions", "warnings",
        "_record_id_candidates", "_invalid_record_ids",
    ):
        merged[key] = sorted(set(left.get(key, [])) | set(right.get(key, [])))
    for key, value in right.items():
        if key not in ("source_database", "query_ids", "source_record_positions", "warnings") and not merged.get(key) and value:
            merged[key] = value
    return merged


def anonymous_fingerprint(record: dict[str, Any]) -> str:
    payload = {
        "title": normalize_title(record.get("title")),
        "authors": [normalize_title(author) for author in record.get("authors", [])],
        "year": record.get("year"),
        "venue": normalize_title(record.get("venue")),
        "abstract": normalize_title(record.get("abstract")),
        "doi": record.get("doi"),
        "pmid": record.get("pmid"),
        "other_identifier": str(record.get("other_identifier") or "").casefold(),
        "landing_url": str(record.get("landing_url") or "").strip().casefold(),
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def generated_record_id(identity_key: str, used: set[str]) -> str:
    digest = hashlib.sha256(identity_key.encode("utf-8")).hexdigest().upper()
    for length in (24, 32, 40, 64):
        candidate = f"REC-{digest[:length]}"
        if candidate not in used:
            return candidate
    suffix = 2
    while f"REC-{digest}-{suffix}" in used:
        suffix += 1
    return f"REC-{digest}-{suffix}"


def deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anonymous: list[dict[str, Any]] = []
    for source_position, raw in enumerate(records, start=1):
        normalized = canonicalize(raw)
        normalized["source_record_positions"] = [source_position]
        normalized["warnings"] = [
            f"invalid_record_id_not_preserved:{record_id}"
            for record_id in normalized.get("_invalid_record_ids", [])
        ]
        key = identity(normalized)
        if not key:
            anonymous.append(normalized)
            continue
        if key not in found:
            found[key] = normalized
            order.append(key)
        else:
            found[key] = merge(found[key], normalized)

    anonymous_counts: dict[str, int] = {}
    for normalized in sorted(anonymous, key=anonymous_fingerprint):
        fingerprint = anonymous_fingerprint(normalized)
        anonymous_counts[fingerprint] = anonymous_counts.get(fingerprint, 0) + 1
        key = f"anonymous:{fingerprint}:{anonymous_counts[fingerprint]}"
        normalized["warnings"].append(
            "identity_missing: preserved without deduplication; stable ID derives from normalized content"
        )
        found[key] = normalized
        order.append(key)

    assigned: dict[str, str] = {}
    candidate_owners: dict[str, list[str]] = {}
    for key in sorted(order):
        for candidate in set(found[key].get("_record_id_candidates", [])):
            candidate_owners.setdefault(candidate, []).append(key)
    owner_by_candidate = {
        candidate: sorted(keys)[0] for candidate, keys in candidate_owners.items()
    }
    for key in sorted(order):
        owned = sorted(
            candidate for candidate in found[key].get("_record_id_candidates", [])
            if owner_by_candidate.get(candidate) == key
        )
        if owned:
            assigned[key] = owned[0]

    unavailable_ids = set(candidate_owners) | set(assigned.values())
    for key in sorted(order):
        if key not in assigned:
            assigned[key] = generated_record_id(key, unavailable_ids)
            unavailable_ids.add(assigned[key])

    assigned_ids = set(assigned.values())
    output = []
    for key in order:
        record = found[key]
        candidates = sorted(set(record.pop("_record_id_candidates", [])))
        invalid_ids = record.pop("_invalid_record_ids", [])
        record["record_id"] = assigned[key]
        record["identity_key"] = key
        record["record_id_aliases"] = [
            candidate for candidate in candidates
            if candidate != record["record_id"] and candidate not in assigned_ids
        ]
        colliding = [
            candidate for candidate in candidates
            if candidate != record["record_id"] and candidate in assigned_ids
        ]
        if colliding:
            record["warnings"].append(
                f"record_id_collision: generated a distinct ID because {', '.join(colliding)} is assigned to another record"
            )
        if not candidates:
            record["warnings"].append("record_id_generated_from_normalized_identity")
        if invalid_ids:
            record["warnings"] = sorted(set(record["warnings"]))
        record["identifier_missing"] = not bool(record["doi"] or record["pmid"] or record["other_identifier"])
        record["identity_missing"] = not bool(
            record["title"]
            or record["doi"]
            or record["pmid"]
            or record["other_identifier"]
            or record["landing_url"]
        )
        output.append(record)
    return output


def read_records(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    data = json.loads(content)
    if not isinstance(data, list):
        raise ValueError("Input JSON must be an array of records")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    records = deduplicate(read_records(args.input))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix.lower() == ".jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in records) + "\n"
    else:
        body = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(body, encoding="utf-8")
    print(json.dumps({"input_records": len(read_records(args.input)), "output_records": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
