#!/usr/bin/env python3
"""Parse a JATS article into deterministic section, paragraph, object, and formula anchors."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree


SCHEMA_VERSION = "1.0"
MAX_JATS_BYTES = 30 * 1024 * 1024
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"


OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/yimuEwood/zju-research-skills/schemas/jats-reader-bundle-1.0.json",
    "title": "ZJU JATS reader bundle",
    "type": "object",
    "required": ["schema_version", "bundle_type", "source", "article", "sections", "paragraphs", "inventories", "coverage"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "bundle_type": {"const": "jats-reader-bundle"},
        "source": {"type": "object", "required": ["filename", "byte_count", "sha256"]},
        "sections": {"type": "array"},
        "paragraphs": {"type": "array"},
        "inventories": {
            "type": "object",
            "required": ["figures", "tables", "formulas", "references"],
        },
        "coverage": {"type": "object", "required": ["body_present", "paragraphs", "sections"]},
    },
}


class JatsParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def local_name(node_or_tag: ElementTree.Element | str) -> str:
    tag = node_or_tag.tag if isinstance(node_or_tag, ElementTree.Element) else node_or_tag
    return str(tag).rsplit("}", 1)[-1]


def normalized_text(node: ElementTree.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def children(node: ElementTree.Element, name: str) -> list[ElementTree.Element]:
    return [child for child in list(node) if local_name(child) == name]


def descendants(node: ElementTree.Element, name: str) -> Iterable[ElementTree.Element]:
    return (candidate for candidate in node.iter() if local_name(candidate) == name)


def first_descendant(node: ElementTree.Element, name: str) -> ElementTree.Element | None:
    return next(descendants(node, name), None)


def unique_id(prefix: str, raw_id: str, index: int, used: set[str]) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._:-]+", "-", raw_id).strip("-")
    candidate = cleaned or f"{prefix}-{index:04d}"
    if candidate not in used:
        used.add(candidate)
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in used:
        suffix += 1
    candidate = f"{candidate}-{suffix}"
    used.add(candidate)
    return candidate


def _article_metadata(root: ElementTree.Element) -> dict[str, Any]:
    title_group = first_descendant(root, "title-group")
    title = normalized_text(first_descendant(title_group, "article-title")) if title_group is not None else ""
    authors = []
    contrib_group = first_descendant(root, "contrib-group")
    if contrib_group is not None:
        for contrib in descendants(contrib_group, "contrib"):
            if contrib.get("contrib-type", "author") != "author":
                continue
            name_node = first_descendant(contrib, "name")
            if name_node is None:
                collab = normalized_text(first_descendant(contrib, "collab"))
                if collab:
                    authors.append(collab)
                continue
            surname = normalized_text(first_descendant(name_node, "surname"))
            given = normalized_text(first_descendant(name_node, "given-names"))
            full = " ".join(filter(None, [given, surname]))
            if full:
                authors.append(full)
    identifiers: dict[str, str] = {}
    for article_id in descendants(root, "article-id"):
        kind = str(article_id.get("pub-id-type") or "other").casefold()
        value = normalized_text(article_id)
        if value:
            identifiers[kind] = value
    journal = normalized_text(first_descendant(root, "journal-title"))
    pub_date = first_descendant(root, "pub-date")
    year = normalized_text(first_descendant(pub_date, "year")) if pub_date is not None else ""
    abstract = normalized_text(first_descendant(root, "abstract"))
    return {
        "title": title,
        "authors": authors,
        "identifiers": identifiers,
        "journal": journal,
        "year": int(year) if year.isdigit() else None,
        "abstract": abstract,
    }


def _section_records(body: ElementTree.Element) -> tuple[list[dict[str, Any]], dict[int, str]]:
    sections: list[dict[str, Any]] = []
    owner: dict[int, str] = {}
    used: set[str] = set()

    def visit(node: ElementTree.Element, parent_id: str | None, level: int) -> None:
        for section in children(node, "sec"):
            section_id = unique_id("sec", str(section.get("id") or ""), len(sections) + 1, used)
            title = normalized_text(next(iter(children(section, "title")), None))
            anchor = f"[JATS sec={section_id}]"
            record = {
                "section_id": section_id,
                "parent_section_id": parent_id,
                "level": level,
                "title": title,
                "section_type": str(section.get("sec-type") or ""),
                "anchor": anchor,
            }
            sections.append(record)
            for descendant in section.iter():
                owner[id(descendant)] = section_id
            visit(section, section_id, level + 1)

    for descendant in body.iter():
        owner[id(descendant)] = "body"
    visit(body, None, 1)
    return sections, owner


def _paragraph_records(body: ElementTree.Element, owner: dict[int, str]) -> list[dict[str, Any]]:
    records = []
    used: set[str] = set()
    for node in descendants(body, "p"):
        value = normalized_text(node)
        if not value:
            continue
        paragraph_id = unique_id("p", str(node.get("id") or ""), len(records) + 1, used)
        section_id = owner.get(id(node), "body")
        records.append(
            {
                "paragraph_id": paragraph_id,
                "section_id": section_id,
                "anchor": f"[JATS sec={section_id} p={paragraph_id}]",
                "text": value,
            }
        )
    return records


def _caption(node: ElementTree.Element) -> str:
    return normalized_text(next(iter(children(node, "caption")), None))


def _graphic_hrefs(node: ElementTree.Element) -> list[str]:
    values = []
    for candidate in node.iter():
        if local_name(candidate) in {"graphic", "inline-graphic", "media"}:
            href = candidate.get(XLINK_HREF) or candidate.get("href")
            if href:
                values.append(str(href))
    return list(dict.fromkeys(values))


def _object_inventory(body: ElementTree.Element, owner: dict[int, str], tag: str, prefix: str) -> list[dict[str, Any]]:
    records = []
    used: set[str] = set()
    for node in descendants(body, tag):
        object_id = unique_id(prefix, str(node.get("id") or ""), len(records) + 1, used)
        section_id = owner.get(id(node), "body")
        label = normalized_text(next(iter(children(node, "label")), None))
        record = {
            "object_id": object_id,
            "section_id": section_id,
            "label": label,
            "caption": _caption(node),
            "anchor": f"[JATS sec={section_id} {prefix}={object_id}]",
        }
        if tag == "fig":
            record["graphic_hrefs"] = _graphic_hrefs(node)
        else:
            rows = []
            for row in descendants(node, "tr"):
                cells = [normalized_text(cell) for cell in list(row) if local_name(cell) in {"td", "th"}]
                if cells:
                    rows.append(cells)
            record["rows"] = rows
            record["row_count"] = len(rows)
        records.append(record)
    return records


def _formula_inventory(body: ElementTree.Element, owner: dict[int, str]) -> list[dict[str, Any]]:
    records = []
    used: set[str] = set()
    for node in body.iter():
        if local_name(node) not in {"disp-formula", "inline-formula"}:
            continue
        formula_id = unique_id("formula", str(node.get("id") or ""), len(records) + 1, used)
        section_id = owner.get(id(node), "body")
        label = normalized_text(next(iter(children(node, "label")), None))
        tex = normalized_text(first_descendant(node, "tex-math"))
        mathml = normalized_text(first_descendant(node, "math"))
        value = tex or mathml or normalized_text(node)
        records.append(
            {
                "formula_id": formula_id,
                "display": local_name(node) == "disp-formula",
                "section_id": section_id,
                "label": label,
                "representation": "tex" if tex else "mathml_text" if mathml else "plain_text",
                "text": value,
                "anchor": f"[JATS sec={section_id} eq={formula_id}]",
            }
        )
    return records


def _reference_inventory(root: ElementTree.Element) -> list[dict[str, Any]]:
    records = []
    used: set[str] = set()
    for node in descendants(root, "ref"):
        ref_id = unique_id("ref", str(node.get("id") or ""), len(records) + 1, used)
        mixed = first_descendant(node, "mixed-citation")
        if mixed is None:
            mixed = first_descendant(node, "element-citation")
        records.append({"reference_id": ref_id, "anchor": f"[JATS ref={ref_id}]", "text": normalized_text(mixed if mixed is not None else node)})
    return records


def parse_jats(path: Path | str) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise JatsParseError("source_missing", f"JATS source does not exist: {source}")
    raw = source.read_bytes()
    if len(raw) > MAX_JATS_BYTES:
        raise JatsParseError("source_too_large", f"JATS source exceeds {MAX_JATS_BYTES} bytes")
    header = raw[:4096].upper()
    if b"<!DOCTYPE" in header or b"<!ENTITY" in header:
        raise JatsParseError("unsafe_xml", "DTD and entity declarations are not accepted")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        raise JatsParseError("invalid_xml", f"JATS source is not well-formed XML: {exc}") from exc
    if local_name(root) not in {"article", "articles"}:
        raise JatsParseError("not_jats", f"expected article root, found {local_name(root)}")
    article = root if local_name(root) == "article" else first_descendant(root, "article")
    if article is None:
        raise JatsParseError("not_jats", "article collection contains no article")
    body = first_descendant(article, "body")
    if body is None or not normalized_text(body):
        raise JatsParseError("bodyless_jats", "JATS source has no readable article body")
    sections, owner = _section_records(body)
    paragraphs = _paragraph_records(body, owner)
    figures = _object_inventory(body, owner, "fig", "fig")
    tables = _object_inventory(body, owner, "table-wrap", "table")
    formulas = _formula_inventory(body, owner)
    references = _reference_inventory(article)
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_type": "jats-reader-bundle",
        "source": {
            "filename": source.name,
            "mime_type": "application/xml",
            "byte_count": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "article": _article_metadata(article),
        "sections": sections,
        "paragraphs": paragraphs,
        "inventories": {"figures": figures, "tables": tables, "formulas": formulas, "references": references},
        "coverage": {
            "body_present": True,
            "sections": len(sections),
            "paragraphs": len(paragraphs),
            "figures": len(figures),
            "tables": len(tables),
            "formulas": len(formulas),
            "references": len(references),
            "body_characters": len(normalized_text(body)),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    try:
        if args.input is None or args.output is None:
            raise JatsParseError("missing_argument", "--input and --output are required")
        result = parse_jats(args.input)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"ok": True, "output": str(args.output), "paragraphs": len(result["paragraphs"])}, ensure_ascii=False))
        return 0
    except (JatsParseError, OSError) as exc:
        code = exc.code if isinstance(exc, JatsParseError) else "local_io_error"
        print(json.dumps({"ok": False, "error": {"code": code, "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 2 if code in {"missing_argument", "source_missing"} else 3


if __name__ == "__main__":
    raise SystemExit(main())
