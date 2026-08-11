#!/usr/bin/env python3
"""Normalize and deduplicate scholarly records without network access."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable


DOI_PREFIX = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.I)


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
    }


def identity(record: dict[str, Any]) -> str:
    if record["doi"]:
        return f"doi:{record['doi']}"
    if record["pmid"]:
        return f"pmid:{record['pmid']}"
    title_key = normalize_title(record["title"])
    if title_key:
        return f"title-year:{title_key}:{record['year'] or ''}"
    return f"fallback:{record['other_identifier']}:{record['landing_url']}"


def merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    for key in ("source_database", "query_ids"):
        merged[key] = sorted(set(left.get(key, [])) | set(right.get(key, [])))
    for key, value in right.items():
        if key not in ("source_database", "query_ids") and not merged.get(key) and value:
            merged[key] = value
    return merged


def deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in records:
        normalized = canonicalize(raw)
        key = identity(normalized)
        if key not in found:
            found[key] = normalized
            order.append(key)
        else:
            found[key] = merge(found[key], normalized)
    output = []
    for index, key in enumerate(order, start=1):
        record = found[key]
        record["record_id"] = f"REC-{index:05d}"
        record["identifier_missing"] = not bool(record["doi"] or record["pmid"] or record["other_identifier"])
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
