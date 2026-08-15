#!/usr/bin/env python3
"""Build and verify a source-grounded PPTX from a validated deck-plan JSON."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from validate_deck_plan import validate


WRITING_SCRIPTS = Path(__file__).resolve().parents[2] / "zju-scientific-writing" / "scripts"
if str(WRITING_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(WRITING_SCRIPTS))
from build_research_docx import load_registry, resolve_result_tokens, sha256_file  # noqa: E402


PLACEHOLDER = re.compile(
    r"\b(?:TODO|TBD|SOURCE_REQUIRED|NOT_GENERATED|NOT_INSPECTED|PLACEHOLDER)\b|\{\{|\}\}",
    re.IGNORECASE,
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _zip_write(archive: zipfile.ZipFile, name: str, body: str | bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, body.encode("utf-8") if isinstance(body, str) else body)


def _text_paragraph(text: str, *, size: int, color: str, bold: bool = False, bullet: bool = False) -> str:
    bullet_xml = '<a:buChar char="•"/>' if bullet else '<a:buNone/>'
    return (
        f'<a:p><a:pPr marL="{342900 if bullet else 0}" indent="{-171450 if bullet else 0}">{bullet_xml}</a:pPr>'
        f'<a:r><a:rPr lang="zh-CN" altLang="en-US" sz="{size}" b="{1 if bold else 0}">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="Arial"/>'
        f'<a:ea typeface="Microsoft YaHei"/></a:rPr><a:t>{escape(text)}</a:t></a:r>'
        f'<a:endParaRPr lang="zh-CN" sz="{size}"/></a:p>'
    )


def _text_box(
    shape_id: int,
    name: str,
    paragraphs: list[str],
    *,
    x: int,
    y: int,
    cx: int,
    cy: int,
    margin: int = 91440,
    placeholder_type: str | None = None,
) -> str:
    placeholder = (
        f'<p:nvPr><p:ph type="{escape(placeholder_type)}" idx="1"/></p:nvPr>'
        if placeholder_type
        else '<p:nvPr/>'
    )
    return (
        '<p:sp><p:nvSpPr>'
        f'<p:cNvPr id="{shape_id}" name="{escape(name)}"/>'
        '<p:cNvSpPr txBox="1"/>' + placeholder + '</p:nvSpPr>'
        '<p:spPr><a:xfrm>'
        f'<a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        '</a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/><a:ln><a:noFill/></a:ln></p:spPr>'
        f'<p:txBody><a:bodyPr wrap="square" lIns="{margin}" tIns="{margin}" rIns="{margin}" bIns="{margin}" anchor="t"/>'
        '<a:lstStyle/>' + "".join(paragraphs) + '</p:txBody></p:sp>'
    )


def _group_shape_tree() -> str:
    return (
        '<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
        '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
    )


def _slide_xml(title: str, body_lines: list[str], anchors: list[str]) -> str:
    title_box = _text_box(
        2,
        "Slide title",
        [_text_paragraph(title, size=3600, color="17365D", bold=True)],
        x=685800,
        y=365760,
        cx=10820400,
        cy=914400,
    )
    body_box = _text_box(
        3,
        "Evidence-led content",
        [
            _text_paragraph(line, size=2200, color="202020", bold=(index == 0), bullet=index > 0)
            for index, line in enumerate(body_lines)
        ],
        x=914400,
        y=1463040,
        cx=10363200,
        cy=3657600,
        margin=137160,
    )
    footer_text = "Sources: " + " | ".join(anchors)
    if len(footer_text) > 220:
        footer_text = footer_text[:217] + "..."
    footer_box = _text_box(
        4,
        "Source anchors",
        [_text_paragraph(footer_text, size=1200, color="5B6573")],
        x=685800,
        y=6035040,
        cx=10820400,
        cy=411480,
        margin=0,
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}">'
        '<p:cSld><p:spTree>' + _group_shape_tree() + title_box + body_box + footer_box + '</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )


def _notes_xml(notes: str, anchors: list[str], result_uses: list[dict[str, Any]]) -> str:
    note_lines = [notes, "[Sources]"] + [f"- {anchor}" for anchor in anchors]
    if result_uses:
        note_lines.append("[Results]")
        note_lines.extend(
            f"- {item['result_id']}:{item['field']} = {item['rendered']}"
            for item in result_uses
        )
    body = _text_box(
        2,
        "Speaker notes",
        [_text_paragraph(line, size=1200, color="202020", bullet=line.startswith("- ")) for line in note_lines],
        x=685800,
        y=685800,
        cx=5486400,
        cy=7772400,
        placeholder_type="body",
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:notes xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}">'
        '<p:cSld><p:spTree>' + _group_shape_tree() + body + '</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>'
    )


def _theme_xml() -> str:
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{DML}" name="ZJU Research"><a:themeElements>
<a:clrScheme name="ZJU Research"><a:dk1><a:srgbClr val="202020"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="17365D"/></a:dk2><a:lt2><a:srgbClr val="F2F5F8"/></a:lt2><a:accent1><a:srgbClr val="17365D"/></a:accent1><a:accent2><a:srgbClr val="0072B2"/></a:accent2><a:accent3><a:srgbClr val="009E73"/></a:accent3><a:accent4><a:srgbClr val="D55E00"/></a:accent4><a:accent5><a:srgbClr val="CC79A7"/></a:accent5><a:accent6><a:srgbClr val="E69F00"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme>
<a:fontScheme name="ZJU Research"><a:majorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:majorFont><a:minorFont><a:latin typeface="Arial"/><a:ea typeface="Microsoft YaHei"/><a:cs typeface="Arial"/></a:minorFont></a:fontScheme>
<a:fmtScheme name="ZJU Research"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
</a:themeElements></a:theme>'''


def _master_xml() -> str:
    empty_styles = (
        '<a:lvl1pPr marL="0" algn="l"><a:defRPr sz="2400"/></a:lvl1pPr>'
        '<a:lvl2pPr marL="342900" algn="l"><a:defRPr sz="2000"/></a:lvl2pPr>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldMaster xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}">'
        '<p:cSld name="ZJU Research Master"><p:bg><p:bgPr><a:solidFill><a:srgbClr val="FFFFFF"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        '<p:spTree>' + _group_shape_tree() + '</p:spTree></p:cSld>'
        '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
        '<p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst>'
        '<p:txStyles><p:titleStyle>' + empty_styles + '</p:titleStyle><p:bodyStyle>' + empty_styles + '</p:bodyStyle><p:otherStyle>' + empty_styles + '</p:otherStyle></p:txStyles>'
        '</p:sldMaster>'
    )


def _layout_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sldLayout xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}" type="blank" preserve="1">'
        '<p:cSld name="Blank"><p:spTree>' + _group_shape_tree() + '</p:spTree></p:cSld>'
        '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>'
    )


def _notes_master_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:notesMaster xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}">'
        '<p:cSld name="Notes Master"><p:spTree>' + _group_shape_tree() + '</p:spTree></p:cSld>'
        '<p:clrMap accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" bg1="lt1" bg2="lt2" folHlink="folHlink" hlink="hlink" tx1="dk1" tx2="dk2"/>'
        '<p:hf hdr="0" ftr="0" dt="0" sldNum="0"/><p:notesStyle><a:lvl1pPr marL="0"><a:defRPr sz="1200"/></a:lvl1pPr></p:notesStyle>'
        '</p:notesMaster>'
    )


def _relationships(rows: list[tuple[str, str, str]]) -> str:
    content = "".join(
        f'<Relationship Id="{escape(rel_id)}" Type="{escape(rel_type)}" Target="{escape(target)}"/>'
        for rel_id, rel_type, target in rows
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + content + '</Relationships>'
    )


def build_presentation(
    plan_path: Path,
    output_path: Path,
    *,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    plan_path = plan_path.resolve()
    payload = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("deck plan must be a JSON object")
    validation = validate(payload)
    if not validation["valid"]:
        raise ValueError(f"deck plan is invalid: {validation['findings']}")
    registry, results = load_registry(registry_path.resolve() if registry_path else None)
    rendered_slides: list[dict[str, Any]] = []
    all_result_uses: list[dict[str, Any]] = []
    all_anchors: set[str] = set()
    for index, slide in enumerate(payload["slides"], 1):
        title, title_uses = resolve_result_tokens(str(slide["title"]), results)
        claim, claim_uses = resolve_result_tokens(str(slide["claim"]), results)
        notes, notes_uses = resolve_result_tokens(str(slide["speaker_notes"]), results)
        on_slide = slide.get("on_slide_text", [])
        if isinstance(on_slide, str):
            on_slide = [on_slide]
        if not isinstance(on_slide, list):
            raise ValueError(f"slides[{index}].on_slide_text must be a string or list")
        rendered_lines: list[str] = [claim]
        line_uses: list[dict[str, Any]] = []
        for line in on_slide:
            rendered, uses = resolve_result_tokens(str(line), results)
            rendered_lines.append(rendered)
            line_uses.extend(uses)
        if len(rendered_lines) > 7:
            raise ValueError(f"slides[{index}] exceeds the bounded seven-line content budget")
        anchors = [str(item).strip() for item in slide.get("source_anchors", [])]
        if not anchors or any(not item for item in anchors):
            raise ValueError(f"slides[{index}] requires non-empty source anchors")
        combined = " ".join([title, *rendered_lines, notes, *anchors])
        if PLACEHOLDER.search(combined):
            raise ValueError(f"slides[{index}] contains an unresolved placeholder")
        uses = title_uses + claim_uses + notes_uses + line_uses
        all_result_uses.extend(uses)
        all_anchors.update(anchors)
        rendered_slides.append({
            "slide_id": str(slide["slide_id"]),
            "title": title,
            "body_lines": rendered_lines,
            "speaker_notes": notes,
            "source_anchors": anchors,
            "result_uses": uses,
        })

    slide_count = len(rendered_slides)
    presentation_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:presentation xmlns:a="{DML}" xmlns:r="{REL}" xmlns:p="{PML}" saveSubsetFonts="1">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        '<p:notesMasterIdLst><p:notesMasterId r:id="rId2"/></p:notesMasterIdLst>'
        '<p:sldIdLst>'
        + "".join(f'<p:sldId id="{256 + index}" r:id="rId{index + 2}"/>' for index in range(1, slide_count + 1))
        + '</p:sldIdLst><p:sldSz cx="12192000" cy="6858000" type="screen16x9"/>'
        '<p:notesSz cx="6858000" cy="9144000"/><p:defaultTextStyle><a:defPPr><a:defRPr lang="zh-CN"/></a:defPPr></p:defaultTextStyle></p:presentation>'
    )
    presentation_rels = [
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "slideMasters/slideMaster1.xml"),
        ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster", "notesMasters/notesMaster1.xml"),
    ] + [
        (f"rId{index + 2}", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", f"slides/slide{index}.xml")
        for index in range(1, slide_count + 1)
    ]
    content_overrides = [
        '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>',
        '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>',
        '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>',
        '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>',
        '<Override PartName="/ppt/notesMasters/notesMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for index in range(1, slide_count + 1):
        content_overrides.extend([
            f'<Override PartName="/ppt/slides/slide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>',
            f'<Override PartName="/ppt/notesSlides/notesSlide{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml"/>',
        ])
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(content_overrides) + '</Types>'
    )
    root_rels = _relationships([
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument", "ppt/presentation.xml"),
        ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties", "docProps/core.xml"),
        ("rId3", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties", "docProps/app.xml"),
    ])
    master_rels = _relationships([
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml"),
        ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme", "../theme/theme1.xml"),
    ])
    layout_rels = _relationships([
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster", "../slideMasters/slideMaster1.xml"),
    ])
    notes_master_rels = _relationships([
        ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme", "../theme/theme1.xml"),
    ])
    title = str(payload.get("title") or payload.get("project_id"))
    core_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f'<dc:title>{escape(title)}</dc:title><dc:subject>source-grounded research presentation</dc:subject>'
        '<dc:creator>zju-research-skills</dc:creator><cp:lastModifiedBy>zju-research-skills</cp:lastModifiedBy>'
        '<dcterms:created xsi:type="dcterms:W3CDTF">2026-08-15T00:00:00Z</dcterms:created>'
        '<dcterms:modified xsi:type="dcterms:W3CDTF">2026-08-15T00:00:00Z</dcterms:modified>'
        '</cp:coreProperties>'
    )
    app_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
        'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        f'<Application>zju-research-skills</Application><PresentationFormat>Widescreen</PresentationFormat><Slides>{slide_count}</Slides>'
        f'<Notes>{slide_count}</Notes><HiddenSlides>0</HiddenSlides><MMClips>0</MMClips><ScaleCrop>false</ScaleCrop>'
        '<Company>Zhejiang University research workflow</Company><AppVersion>1.0</AppVersion></Properties>'
    )

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w") as archive:
        _zip_write(archive, "[Content_Types].xml", content_types)
        _zip_write(archive, "_rels/.rels", root_rels)
        _zip_write(archive, "docProps/core.xml", core_xml)
        _zip_write(archive, "docProps/app.xml", app_xml)
        _zip_write(archive, "ppt/presentation.xml", presentation_xml)
        _zip_write(archive, "ppt/_rels/presentation.xml.rels", _relationships(presentation_rels))
        _zip_write(archive, "ppt/theme/theme1.xml", _theme_xml())
        _zip_write(archive, "ppt/slideMasters/slideMaster1.xml", _master_xml())
        _zip_write(archive, "ppt/slideMasters/_rels/slideMaster1.xml.rels", master_rels)
        _zip_write(archive, "ppt/slideLayouts/slideLayout1.xml", _layout_xml())
        _zip_write(archive, "ppt/slideLayouts/_rels/slideLayout1.xml.rels", layout_rels)
        _zip_write(archive, "ppt/notesMasters/notesMaster1.xml", _notes_master_xml())
        _zip_write(archive, "ppt/notesMasters/_rels/notesMaster1.xml.rels", notes_master_rels)
        for index, slide in enumerate(rendered_slides, 1):
            _zip_write(
                archive,
                f"ppt/slides/slide{index}.xml",
                _slide_xml(slide["title"], slide["body_lines"], slide["source_anchors"]),
            )
            _zip_write(
                archive,
                f"ppt/slides/_rels/slide{index}.xml.rels",
                _relationships([
                    ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout", "../slideLayouts/slideLayout1.xml"),
                    ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide", f"../notesSlides/notesSlide{index}.xml"),
                ]),
            )
            _zip_write(
                archive,
                f"ppt/notesSlides/notesSlide{index}.xml",
                _notes_xml(slide["speaker_notes"], slide["source_anchors"], slide["result_uses"]),
            )
            _zip_write(
                archive,
                f"ppt/notesSlides/_rels/notesSlide{index}.xml.rels",
                _relationships([
                    ("rId1", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster", "../notesMasters/notesMaster1.xml"),
                    ("rId2", "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide", f"../slides/slide{index}.xml"),
                ]),
            )

    verification = verify_pptx(
        output_path,
        expected_slide_count=slide_count,
        expected_anchors=all_anchors,
        expected_result_ids={item["result_id"] for item in all_result_uses},
    )
    if not verification["valid"]:
        raise ValueError(f"generated PPTX failed OOXML verification: {verification['findings']}")
    return {
        "schema_version": "1.0",
        "project_id": payload["project_id"],
        "source_id": payload["source_id"],
        "slide_count": slide_count,
        "source_anchors": sorted(all_anchors),
        "result_uses": all_result_uses,
        "inputs": {
            "deck_plan": {"path": plan_path.name, "sha256": sha256_file(plan_path)},
            "result_registry": (
                {"path": registry_path.resolve().name, "sha256": sha256_file(registry_path.resolve()), "registry_version": registry.get("registry_version")}
                if registry_path and registry is not None
                else None
            ),
        },
        "artifact": {
            "path": output_path.name,
            "format": "pptx",
            "sha256": sha256_file(output_path),
            "bytes": output_path.stat().st_size,
            "aspect_ratio": "16:9",
        },
        "qa": {
            "deck_plan_validation": "passed",
            "ooxml_reopen": "passed",
            "placeholder_scan": "passed",
            "source_resolution": "passed",
            "result_token_resolution": "passed",
            "speaker_notes_present": True,
            "verification": verification,
            "render_and_human_visual_review": "required",
        },
    }


def verify_pptx(
    path: Path,
    *,
    expected_slide_count: int,
    expected_anchors: set[str] | None = None,
    expected_result_ids: set[str] | None = None,
) -> dict[str, Any]:
    findings: list[str] = []
    visible_text = ""
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            required = {
                "[Content_Types].xml",
                "_rels/.rels",
                "ppt/presentation.xml",
                "ppt/slideMasters/slideMaster1.xml",
                "ppt/slideLayouts/slideLayout1.xml",
                "ppt/notesMasters/notesMaster1.xml",
            }
            for index in range(1, expected_slide_count + 1):
                required.update({
                    f"ppt/slides/slide{index}.xml",
                    f"ppt/slides/_rels/slide{index}.xml.rels",
                    f"ppt/notesSlides/notesSlide{index}.xml",
                    f"ppt/notesSlides/_rels/notesSlide{index}.xml.rels",
                })
            missing = required - names
            if missing:
                findings.append(f"missing OOXML parts: {sorted(missing)}")
            bad = archive.testzip()
            if bad:
                findings.append(f"corrupt ZIP member: {bad}")
            xml_parts = [
                name for name in sorted(names)
                if (name.startswith("ppt/slides/slide") or name.startswith("ppt/notesSlides/notesSlide"))
                and name.endswith(".xml")
            ]
            text_runs: list[str] = []
            for name in xml_parts:
                root = ET.fromstring(archive.read(name))
                text_runs.extend(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
            visible_text = " ".join(text_runs)
            if PLACEHOLDER.search(visible_text):
                findings.append("presentation contains unresolved placeholder text")
            for anchor in sorted(expected_anchors or set()):
                if anchor not in visible_text:
                    findings.append(f"source anchor missing from slides/notes: {anchor}")
            for result_id in sorted(expected_result_ids or set()):
                if result_id not in visible_text:
                    findings.append(f"result ID missing from notes provenance: {result_id}")
            slide_parts = [name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)]
            if len(slide_parts) != expected_slide_count:
                findings.append(f"slide count mismatch: expected {expected_slide_count}, found {len(slide_parts)}")
    except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        findings.append(f"could not reopen PPTX package: {exc}")
    return {"valid": not findings, "visible_characters": len(visible_text), "findings": findings}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--registry", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        manifest = build_presentation(args.plan, args.output, registry_path=args.registry)
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
        "slides": manifest["slide_count"],
        "output": str(args.output.resolve()),
        "manifest": str(manifest_path.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
