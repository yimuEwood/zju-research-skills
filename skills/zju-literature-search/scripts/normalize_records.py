#!/usr/bin/env python3
"""Normalize and deduplicate scholarly records without network access."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
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
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return [json.loads(line) for line in content.splitlines() if line.strip()]
    if suffix == ".json":
        data = json.loads(content)
        if isinstance(data, dict):
            data = data.get("records")
        if not isinstance(data, list) or not all(isinstance(row, dict) for row in data):
            raise ValueError("Input JSON must be an array of records or an object with a records array")
        return data
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        return [dict(row) for row in csv.DictReader(io.StringIO(content), delimiter=delimiter)]
    if suffix == ".ris":
        return parse_ris(content)
    if suffix in {".bib", ".bibtex"}:
        return parse_bibtex(content)
    if suffix in {".nbib", ".enw"}:
        return parse_nbib(content)
    raise ValueError("Supported inputs are JSON, JSONL, CSV, TSV, RIS, BibTeX, and NBIB")


def parse_ris(content: str) -> list[dict[str, Any]]:
    """Parse the common bibliographic subset of RIS, preserving unmapped tags."""

    records: list[dict[str, Any]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for raw_line in content.splitlines():
        match = re.match(r"^([A-Z0-9]{2})  -\s?(.*)$", raw_line)
        if match:
            tag, value = match.groups()
            last_tag = tag
            if tag == "TY" and current:
                records.append(ris_record(current))
                current = {}
            if tag == "ER":
                records.append(ris_record(current))
                current = {}
                last_tag = ""
            else:
                current.setdefault(tag, []).append(value.strip())
        elif raw_line[:1].isspace() and last_tag and current.get(last_tag):
            current[last_tag][-1] = text(current[last_tag][-1] + " " + raw_line.strip())
        elif raw_line.strip():
            raise ValueError(f"Malformed RIS line: {raw_line!r}")
    if current:
        records.append(ris_record(current))
    return records


def ris_record(tags: dict[str, list[str]]) -> dict[str, Any]:
    title = first_value(tags, "TI", "T1", "CT")
    record = {
        "title": title,
        "authors": tags.get("AU", []) or tags.get("A1", []),
        "year": first_value(tags, "PY", "Y1", "DA"),
        "journal": first_value(tags, "JO", "JF", "T2", "JA"),
        "abstract": " ".join(tags.get("AB", []) or tags.get("N2", [])),
        "doi": first_value(tags, "DO"),
        "pmid": first_value(tags, "AN") if "pubmed" in first_value(tags, "DB").casefold() else "",
        "url": first_value(tags, "UR", "L1"),
        "source": first_value(tags, "DB") or "RIS import",
        "other_identifier": first_value(tags, "ID"),
        "import_format": "ris",
        "unmapped_tags": {key: value for key, value in tags.items() if key not in {
            "TY", "TI", "T1", "CT", "AU", "A1", "PY", "Y1", "DA", "JO", "JF", "T2", "JA",
            "AB", "N2", "DO", "AN", "UR", "L1", "DB", "ID",
        }},
    }
    return record


def first_value(tags: dict[str, list[str]], *keys: str) -> str:
    for key in keys:
        if tags.get(key):
            return text(tags[key][0])
    return ""


def split_bibtex_entries(content: str) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    index = 0
    while True:
        match = re.search(r"@([A-Za-z]+)\s*([({])", content[index:])
        if not match:
            break
        start = index + match.start()
        open_char = match.group(2)
        close_char = "}" if open_char == "{" else ")"
        body_start = index + match.end()
        depth = 1
        quoted = False
        escaped = False
        cursor = body_start
        while cursor < len(content) and depth:
            char = content[cursor]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = not quoted
            elif not quoted and char == open_char:
                depth += 1
            elif not quoted and char == close_char:
                depth -= 1
            cursor += 1
        if depth:
            raise ValueError(f"Unterminated BibTeX entry beginning at character {start}")
        raw_body = content[body_start:cursor - 1]
        citation_key, separator, fields = raw_body.partition(",")
        if not separator:
            raise ValueError(f"BibTeX entry at character {start} has no citation key separator")
        entries.append((match.group(1).casefold(), citation_key.strip(), fields))
        index = cursor
    return entries


def parse_bibtex_fields(content: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    cursor = 0
    while cursor < len(content):
        while cursor < len(content) and (content[cursor].isspace() or content[cursor] == ","):
            cursor += 1
        if cursor >= len(content):
            break
        match = re.match(r"([A-Za-z][\w-]*)\s*=\s*", content[cursor:])
        if not match:
            raise ValueError(f"Malformed BibTeX field near: {content[cursor:cursor + 40]!r}")
        key = match.group(1).casefold()
        cursor += match.end()
        if cursor >= len(content):
            raise ValueError(f"BibTeX field {key!r} has no value")
        if content[cursor] == "{":
            depth = 1
            start = cursor + 1
            cursor += 1
            while cursor < len(content) and depth:
                if content[cursor] == "{" and (cursor == 0 or content[cursor - 1] != "\\"):
                    depth += 1
                elif content[cursor] == "}" and (cursor == 0 or content[cursor - 1] != "\\"):
                    depth -= 1
                cursor += 1
            if depth:
                raise ValueError(f"Unterminated braced BibTeX value for {key!r}")
            value = content[start:cursor - 1]
        elif content[cursor] == '"':
            cursor += 1
            start = cursor
            escaped = False
            while cursor < len(content):
                if content[cursor] == '"' and not escaped:
                    break
                escaped = content[cursor] == "\\" and not escaped
                if content[cursor] != "\\":
                    escaped = False
                cursor += 1
            if cursor >= len(content):
                raise ValueError(f"Unterminated quoted BibTeX value for {key!r}")
            value = content[start:cursor]
            cursor += 1
        else:
            start = cursor
            while cursor < len(content) and content[cursor] != ",":
                cursor += 1
            value = content[start:cursor]
        fields[key] = text(value.replace("{", "").replace("}", ""))
    return fields


def parse_bibtex(content: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry_type, citation_key, raw_fields in split_bibtex_entries(content):
        if entry_type in {"comment", "preamble", "string"}:
            continue
        fields = parse_bibtex_fields(raw_fields)
        authors = [text(item) for item in re.split(r"\s+and\s+", fields.get("author", ""), flags=re.I) if text(item)]
        mapped_keys = {
            "title", "author", "year", "journal", "booktitle", "abstract", "doi", "pmid", "url", "eprint",
        }
        records.append({
            "title": fields.get("title", ""),
            "authors": authors,
            "year": fields.get("year", ""),
            "journal": fields.get("journal") or fields.get("booktitle", ""),
            "abstract": fields.get("abstract", ""),
            "doi": fields.get("doi", ""),
            "pmid": fields.get("pmid", ""),
            "url": fields.get("url", ""),
            "other_identifier": fields.get("eprint") or citation_key,
            "source": "BibTeX import",
            "import_format": "bibtex",
            "citation_key": citation_key,
            "entry_type": entry_type,
            "unmapped_fields": {key: value for key, value in fields.items() if key not in mapped_keys},
        })
    return records


def parse_nbib(content: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for raw_line in content.splitlines() + [""]:
        match = re.match(r"^([A-Z0-9]{2,4})\s*-\s?(.*)$", raw_line)
        if match:
            tag, value = match.groups()
            last_tag = tag
            current.setdefault(tag, []).append(value.strip())
        elif raw_line.startswith("      ") and last_tag and current.get(last_tag):
            current[last_tag][-1] = text(current[last_tag][-1] + " " + raw_line.strip())
        elif not raw_line.strip() and current:
            doi = ""
            for item in current.get("AID", []) + current.get("LID", []):
                if "[doi]" in item.casefold():
                    doi = re.sub(r"\s*\[doi\]\s*$", "", item, flags=re.I)
                    break
            records.append({
                "title": first_value(current, "TI"),
                "authors": current.get("FAU", []) or current.get("AU", []),
                "year": first_value(current, "DP", "DEP"),
                "journal": first_value(current, "JT", "TA"),
                "abstract": " ".join(current.get("AB", [])),
                "doi": doi,
                "pmid": first_value(current, "PMID"),
                "other_identifier": first_value(current, "PMC"),
                "source": first_value(current, "DB") or "PubMed NBIB import",
                "import_format": "nbib",
                "unmapped_tags": {key: value for key, value in current.items() if key not in {
                    "TI", "FAU", "AU", "DP", "DEP", "JT", "TA", "AB", "AID", "LID", "PMID", "PMC", "DB",
                }},
            })
            current = {}
            last_tag = ""
        elif raw_line.strip():
            raise ValueError(f"Malformed NBIB line: {raw_line!r}")
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    source_records = read_records(args.input)
    records = deduplicate(source_records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.suffix.lower() == ".jsonl":
        body = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in records) + "\n"
    else:
        body = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    args.output.write_text(body, encoding="utf-8")
    print(json.dumps({"input_records": len(source_records), "output_records": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
