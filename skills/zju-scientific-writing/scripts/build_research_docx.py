#!/usr/bin/env python3
"""Build and verify a source- and result-bound research DOCX using OOXML only."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


RESULT_TOKEN = re.compile(r"\{\{result:([^:}]+):([^|}]+)(?:\|([^}]+))?\}\}")
PLACEHOLDER = re.compile(
    r"\b(?:TODO|TBD|SOURCE_REQUIRED|NOT_GENERATED|NOT_INSPECTED|PLACEHOLDER)\b|\{\{|\}\}",
    re.IGNORECASE,
)
SOURCE_MARKER = re.compile(r"\[([A-Za-z][A-Za-z0-9._:-]{1,80})\]")
REFERENCE_HEADING = {"references", "sources", "参考文献", "来源"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True)
class Block:
    kind: str
    text: str
    level: int = 0
    source_ids: tuple[str, ...] = ()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_path(value: Any, dotted: str) -> Any:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"canonical result field does not exist: {dotted}")
        current = current[part]
    return current


def load_registry(path: Path | None) -> tuple[dict[str, Any] | None, dict[str, dict[str, Any]]]:
    if path is None:
        return None, {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("result registry must be a JSON object")
    indexed: dict[str, dict[str, Any]] = {}
    for result in payload.get("results", []):
        if not isinstance(result, dict) or not result.get("result_id"):
            continue
        result_id = str(result["result_id"])
        if result_id in indexed:
            raise ValueError(f"duplicate result_id in registry: {result_id}")
        indexed[result_id] = result
    return payload, indexed


def resolve_result_tokens(text: str, results: dict[str, dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    uses: list[dict[str, Any]] = []

    def replace(match: re.Match[str]) -> str:
        result_id, field, format_spec = match.groups()
        result = results.get(result_id)
        if result is None:
            raise ValueError(f"unknown canonical result ID in document token: {result_id}")
        if result.get("status") != "verified":
            raise ValueError(f"document token references a non-verified result: {result_id}")
        value = get_path(result, field)
        if format_spec:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(f"numeric format {format_spec!r} cannot be applied to {result_id}:{field}")
            rendered = format(value, format_spec)
        elif isinstance(value, bool):
            rendered = "true" if value else "false"
        elif isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(value)
        uses.append({"result_id": result_id, "field": field, "rendered": rendered, "format": format_spec})
        return rendered

    return RESULT_TOKEN.sub(replace, text), uses


def _source_rows(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict):
            raise ValueError(f"sources[{index}] must be an object")
        source_id = str(item.get("source_id") or "").strip()
        citation = str(item.get("citation") or "").strip()
        anchor = str(item.get("anchor") or item.get("source_anchor") or "").strip()
        if not source_id or source_id in seen or not citation or not anchor:
            raise ValueError(f"sources[{index}] requires a unique source_id, citation, and anchor")
        if PLACEHOLDER.search(source_id + " " + citation + " " + anchor):
            raise ValueError(f"sources[{index}] contains an unresolved placeholder")
        seen.add(source_id)
        rows.append({"source_id": source_id, "citation": citation, "anchor": anchor})
    return rows


def parse_json_source(payload: dict[str, Any]) -> tuple[str, str, list[Block], list[dict[str, str]]]:
    document_id = str(payload.get("document_id") or "").strip()
    title = str(payload.get("title") or "").strip()
    if not document_id or not title:
        raise ValueError("JSON document source requires document_id and title")
    sources = _source_rows(payload.get("sources"))
    blocks: list[Block] = []
    subtitle = str(payload.get("subtitle") or "").strip()
    if subtitle:
        blocks.append(Block("subtitle", subtitle))
    sections = payload.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ValueError("JSON document source requires a non-empty sections list")
    for index, section in enumerate(sections, 1):
        if not isinstance(section, dict):
            raise ValueError(f"sections[{index}] must be an object")
        heading = str(section.get("heading") or "").strip()
        level = int(section.get("level", 1))
        if not heading or level not in {1, 2, 3}:
            raise ValueError(f"sections[{index}] requires heading and level 1, 2, or 3")
        blocks.append(Block("heading", heading, level=level))
        for paragraph in section.get("paragraphs", []):
            if isinstance(paragraph, str):
                text = paragraph.strip()
                source_ids: tuple[str, ...] = ()
            elif isinstance(paragraph, dict):
                text = str(paragraph.get("text") or "").strip()
                raw_ids = paragraph.get("source_ids", [])
                if not isinstance(raw_ids, list):
                    raise ValueError(f"sections[{index}].paragraphs source_ids must be a list")
                source_ids = tuple(str(item) for item in raw_ids)
            else:
                raise ValueError(f"sections[{index}].paragraphs entries must be strings or objects")
            if not text:
                raise ValueError(f"sections[{index}] contains an empty paragraph")
            blocks.append(Block("paragraph", text, source_ids=source_ids))
        for bullet in section.get("bullets", []):
            if isinstance(bullet, str):
                text = bullet.strip()
                source_ids = ()
            elif isinstance(bullet, dict):
                text = str(bullet.get("text") or "").strip()
                raw_ids = bullet.get("source_ids", [])
                if not isinstance(raw_ids, list):
                    raise ValueError(f"sections[{index}].bullets source_ids must be a list")
                source_ids = tuple(str(item) for item in raw_ids)
            else:
                raise ValueError(f"sections[{index}].bullets entries must be strings or objects")
            if not text:
                raise ValueError(f"sections[{index}] contains an empty bullet")
            blocks.append(Block("bullet", text, source_ids=source_ids))
    return document_id, title, blocks, sources


def parse_markdown_source(text: str, source_name: str) -> tuple[str, str, list[Block], list[dict[str, str]]]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    title = ""
    blocks: list[Block] = []
    sources: list[dict[str, str]] = []
    paragraph: list[str] = []
    in_references = False

    def flush() -> None:
        if paragraph:
            rendered = " ".join(item.strip() for item in paragraph if item.strip()).strip()
            if rendered:
                blocks.append(Block("paragraph", rendered, source_ids=tuple(SOURCE_MARKER.findall(rendered))))
            paragraph.clear()

    for raw in lines:
        line = raw.rstrip()
        heading = re.match(r"^(#{1,3})\s+(.+?)\s*$", line)
        if heading:
            flush()
            level = len(heading.group(1))
            value = heading.group(2).strip()
            if level == 1 and not title:
                title = value
                in_references = False
                continue
            in_references = value.casefold() in REFERENCE_HEADING
            if not in_references:
                blocks.append(Block("heading", value, level=level))
            continue
        if not line.strip():
            flush()
            continue
        if in_references:
            match = re.match(r"^[-*]\s+\[([^\]]+)\]\s+(.+?)\s+\|\s*anchor\s*=\s*(.+?)\s*$", line)
            if not match:
                raise ValueError(
                    "Markdown reference rows must use '- [SRC-ID] Citation | anchor=stable-anchor'"
                )
            sources.append({
                "source_id": match.group(1).strip(),
                "citation": match.group(2).strip(),
                "anchor": match.group(3).strip(),
            })
            continue
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            flush()
            value = bullet.group(1).strip()
            blocks.append(Block("bullet", value, source_ids=tuple(SOURCE_MARKER.findall(value))))
        else:
            paragraph.append(line)
    flush()
    if not title:
        raise ValueError("Markdown source requires a level-one title")
    if not blocks:
        raise ValueError("Markdown source contains no document body")
    sources = _source_rows(sources)
    document_id = "DOC-" + hashlib.sha256((source_name + "\n" + title).encode("utf-8")).hexdigest()[:16].upper()
    return document_id, title, blocks, sources


def load_source(path: Path) -> tuple[str, str, list[Block], list[dict[str, str]]]:
    if path.suffix.casefold() == ".json":
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, dict):
            raise ValueError("JSON document source must be an object")
        return parse_json_source(payload)
    if path.suffix.casefold() in {".md", ".markdown"}:
        return parse_markdown_source(path.read_text(encoding="utf-8-sig"), path.name)
    raise ValueError("document source must be Markdown or JSON")


def _paragraph_xml(text: str, style: str = "Normal", *, bullet: bool = False) -> str:
    ppr = [f'<w:pStyle w:val="{escape(style)}"/>']
    if bullet:
        ppr.append('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>')
    return (
        "<w:p><w:pPr>" + "".join(ppr) + "</w:pPr>"
        "<w:r><w:t xml:space=\"preserve\">" + escape(text) + "</w:t></w:r></w:p>"
    )


def _zip_write(archive: zipfile.ZipFile, name: str, body: str | bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, body.encode("utf-8") if isinstance(body, str) else body)


def build_docx(
    source_path: Path,
    output_path: Path,
    *,
    registry_path: Path | None = None,
    document_kind: str = "manuscript",
) -> dict[str, Any]:
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise ValueError(f"source file does not exist: {source_path}")
    document_id, title, blocks, sources = load_source(source_path)
    registry, results = load_registry(registry_path.resolve() if registry_path else None)
    known_sources = {item["source_id"] for item in sources}
    if not sources:
        raise ValueError("a deliverable DOCX requires at least one citation/source anchor")

    rendered_blocks: list[Block] = []
    result_uses: list[dict[str, Any]] = []
    cited_sources: set[str] = set()
    for block in blocks:
        rendered, uses = resolve_result_tokens(block.text, results)
        if PLACEHOLDER.search(rendered):
            raise ValueError(f"document contains an unresolved placeholder in: {rendered[:80]!r}")
        marker_sources = set(SOURCE_MARKER.findall(rendered))
        linked_sources = set(block.source_ids) | marker_sources
        unknown = linked_sources - known_sources
        if unknown:
            raise ValueError(f"document body references unknown source IDs: {sorted(unknown)}")
        if block.source_ids:
            missing_markers = [source_id for source_id in block.source_ids if f"[{source_id}]" not in rendered]
            if missing_markers:
                rendered += " " + " ".join(f"[{source_id}]" for source_id in missing_markers)
        cited_sources.update(linked_sources)
        result_uses.extend(uses)
        rendered_blocks.append(Block(block.kind, rendered, block.level, block.source_ids))
    if not cited_sources:
        raise ValueError("document body must cite at least one declared source ID")

    body_xml = [_paragraph_xml(title, "Title")]
    for block in rendered_blocks:
        if block.kind == "subtitle":
            body_xml.append(_paragraph_xml(block.text, "Subtitle"))
        elif block.kind == "heading":
            body_xml.append(_paragraph_xml(block.text, f"Heading{block.level}"))
        elif block.kind == "bullet":
            body_xml.append(_paragraph_xml(block.text, "Normal", bullet=True))
        else:
            body_xml.append(_paragraph_xml(block.text, "Normal"))
    body_xml.append(_paragraph_xml("References and source anchors", "Heading1"))
    for source in sources:
        body_xml.append(_paragraph_xml(
            f"[{source['source_id']}] {source['citation']} — {source['anchor']}",
            "Reference",
            bullet=True,
        ))
    if result_uses:
        body_xml.append(_paragraph_xml("Canonical result provenance", "Heading1"))
        unique_uses = sorted(
            {(item["result_id"], item["field"], item["rendered"]) for item in result_uses}
        )
        for result_id, field, rendered in unique_uses:
            body_xml.append(_paragraph_xml(
                f"{result_id}:{field} = {rendered}",
                "Reference",
                bullet=True,
            ))
    body_xml.append(
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
        'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<w:body>' + "".join(body_xml) + '</w:body></w:document>'
    )
    styles_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft YaHei"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:rPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="120" w:line="300" w:lineRule="auto"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="240"/><w:keepNext/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="44"/><w:szCs w:val="44"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:i/><w:color w:val="526777"/><w:sz w:val="24"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="280" w:after="100"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:color w:val="17365D"/><w:sz w:val="30"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="220" w:after="80"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="365F91"/><w:sz w:val="25"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="180" w:after="60"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:sz w:val="22"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Reference"><w:name w:val="Reference"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="60"/></w:pPr><w:rPr><w:sz w:val="18"/><w:color w:val="404040"/></w:rPr></w:style>
</w:styles>'''
    numbering_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial"/></w:rPr></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>'''
    rels_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>'''
    document_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>'''
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{escape(title)}</dc:title><dc:subject>{escape(document_kind)}</dc:subject>'
        '<dc:creator>zju-research-skills</dc:creator><cp:lastModifiedBy>zju-research-skills</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-08-15T00:00:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-08-15T00:00:00Z</dcterms:modified>'
        '</cp:coreProperties>'
    )
    app_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>zju-research-skills</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><Company>Zhejiang University research workflow</Company><AppVersion>1.0</AppVersion></Properties>'''
    settings_xml = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:zoom w:percent="100"/><w:defaultTabStop w:val="720"/><w:compat/></w:settings>'''

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w") as archive:
        _zip_write(archive, "[Content_Types].xml", content_types)
        _zip_write(archive, "_rels/.rels", rels_xml)
        _zip_write(archive, "word/document.xml", document_xml)
        _zip_write(archive, "word/_rels/document.xml.rels", document_rels)
        _zip_write(archive, "word/styles.xml", styles_xml)
        _zip_write(archive, "word/numbering.xml", numbering_xml)
        _zip_write(archive, "word/settings.xml", settings_xml)
        _zip_write(archive, "docProps/core.xml", core_xml)
        _zip_write(archive, "docProps/app.xml", app_xml)

    registry_hash = sha256_file(registry_path.resolve()) if registry_path else None
    manifest = {
        "schema_version": "1.0",
        "document_id": document_id,
        "document_kind": document_kind,
        "title": title,
        "source": {"path": source_path.name, "sha256": sha256_file(source_path)},
        "result_registry": (
            {"path": registry_path.resolve().name, "sha256": registry_hash, "registry_version": registry.get("registry_version")}
            if registry_path and registry is not None
            else None
        ),
        "sources": sources,
        "cited_source_ids": sorted(cited_sources),
        "result_uses": result_uses,
        "artifact": {
            "path": output_path.name,
            "format": "docx",
            "sha256": sha256_file(output_path),
            "bytes": output_path.stat().st_size,
        },
        "qa": {
            "ooxml_reopen": "pending",
            "placeholder_scan": "passed",
            "source_resolution": "passed",
            "result_token_resolution": "passed",
            "visual_render_review": "required",
        },
    }
    verification = verify_docx(output_path, expected_source_ids=known_sources, expected_result_ids={item["result_id"] for item in result_uses})
    manifest["qa"]["ooxml_reopen"] = "passed" if verification["valid"] else "failed"
    manifest["qa"]["verification"] = verification
    if not verification["valid"]:
        raise ValueError(f"generated DOCX failed OOXML verification: {verification['findings']}")
    return manifest


def verify_docx(
    path: Path,
    *,
    expected_source_ids: set[str] | None = None,
    expected_result_ids: set[str] | None = None,
) -> dict[str, Any]:
    findings: list[str] = []
    required = {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
        "word/styles.xml",
        "word/numbering.xml",
        "word/settings.xml",
        "word/_rels/document.xml.rels",
    }
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            missing = required - names
            if missing:
                findings.append(f"missing OOXML parts: {sorted(missing)}")
            bad = archive.testzip()
            if bad:
                findings.append(f"corrupt ZIP member: {bad}")
            document = archive.read("word/document.xml")
            root = ET.fromstring(document)
            texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            visible = " ".join(texts)
            if not visible.strip():
                findings.append("document has no visible text")
            if PLACEHOLDER.search(visible):
                findings.append("document contains unresolved placeholder text")
            for source_id in sorted(expected_source_ids or set()):
                if f"[{source_id}]" not in visible:
                    findings.append(f"source ID missing from visible document: {source_id}")
            for result_id in sorted(expected_result_ids or set()):
                if result_id not in visible:
                    findings.append(f"result ID missing from provenance appendix: {result_id}")
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        findings.append(f"could not reopen DOCX package: {exc}")
        visible = ""
    return {
        "valid": not findings,
        "visible_characters": len(visible),
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Structured JSON or constrained Markdown")
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--document-kind", default="manuscript", choices=("manuscript", "report", "proposal"))
    args = parser.parse_args()
    try:
        manifest = build_docx(
            args.source,
            args.output,
            registry_path=args.registry,
            document_kind=args.document_kind,
        )
        manifest_path = args.manifest or args.output.with_suffix(".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "document_id": manifest["document_id"],
        "output": str(args.output.resolve()),
        "manifest": str(manifest_path.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
