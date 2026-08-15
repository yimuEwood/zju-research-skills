#!/usr/bin/env python3
"""Prepare a deterministic, page-anchored source bundle from a PDF or UTF-8 text file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "1.1"
PDF_SUFFIXES = {".pdf"}
TEXT_SUFFIXES = {".md", ".markdown", ".txt"}
KNOWN_HEADING_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s+)?"
    r"(?:abstract|summary|introduction|background|related work|literature review|"
    r"materials and methods|methods?|experimental(?: section)?|results?(?: and discussion)?|"
    r"discussion|conclusions?|limitations?|data availability|acknowledg(?:e)?ments?|references|"
    r"supplementary (?:information|methods?|results?))$",
    re.IGNORECASE,
)
NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+){0,3}[.)]?\s+[A-Za-z][^.!?]{1,100}$")
FIGURE_RE = re.compile(r"(?i)(?<!\w)(?:fig(?:ure)?\.?\s*|图\s*)([A-Za-z]?\d+[A-Za-z]?)")
TABLE_RE = re.compile(r"(?i)(?<!\w)(?:table\s*|表\s*)([A-Za-z]?\d+[A-Za-z]?)")
EQUATION_RE = re.compile(
    r"(?i)(?<!\w)(?:eq(?:uation)?\.?\s*\(?\s*|公式\s*)([A-Za-z]?\d+[A-Za-z]?)\s*\)?"
)
FORMULA_CANDIDATE_RE = re.compile(r"(?:=|≈|≤|≥|∑|∫|√|±|α|β|γ|μ|σ|Δ)")

OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/yimuEwood/zju-research-skills/schemas/pdf-reader-source-bundle-1.1.json",
    "title": "ZJU PDF or text reader source bundle",
    "type": "object",
    "required": ["schema_version", "bundle_type", "source", "page_count", "pages", "inventories", "coverage"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "bundle_type": {"const": "reader-source-bundle"},
        "source": {"type": "object", "required": ["filename", "format", "byte_count", "sha256"]},
        "pages": {"type": "array", "items": {"type": "object", "required": ["page_id", "anchor", "layout_blocks"]}},
        "inventories": {"type": "object", "required": ["figures", "tables", "equations", "formula_candidates", "visual_objects"]},
        "coverage": {"type": "object"},
    },
}


class SourcePreparationError(ValueError):
    """Raised when a source cannot be prepared without guessing its contents."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_text(text: str | None) -> str:
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _pymupdf_version(module: Any) -> str:
    version = getattr(module, "VersionBind", None)
    if version:
        return str(version)
    version_tuple = getattr(module, "version", None)
    if isinstance(version_tuple, tuple) and version_tuple:
        return str(version_tuple[0])
    return "unknown"


def _rounded_bbox(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return [round(float(item), 3) for item in value]
    except (TypeError, ValueError):
        return None


def _pymupdf_blocks(page: Any) -> list[dict[str, Any]]:
    page_dict = page.get_text("dict", sort=True)
    blocks = []
    for raw in page_dict.get("blocks") or []:
        if not isinstance(raw, dict):
            continue
        block_type = int(raw.get("type", 0))
        if block_type == 0:
            line_texts = []
            styles = []
            for line in raw.get("lines") or []:
                spans = line.get("spans") or [] if isinstance(line, dict) else []
                text = "".join(str(span.get("text") or "") for span in spans if isinstance(span, dict)).rstrip()
                if text:
                    line_texts.append(text)
                for span in spans:
                    if not isinstance(span, dict):
                        continue
                    styles.append(
                        {
                            "font": str(span.get("font") or ""),
                            "size": round(float(span.get("size") or 0), 3),
                            "flags": int(span.get("flags") or 0),
                        }
                    )
            text = _normalize_text("\n".join(line_texts))
            if text:
                blocks.append({"type": "text", "bbox": _rounded_bbox(raw.get("bbox")), "text": text, "styles": styles})
        elif block_type == 1:
            blocks.append(
                {
                    "type": "image",
                    "bbox": _rounded_bbox(raw.get("bbox")),
                    "width_pixels": raw.get("width"),
                    "height_pixels": raw.get("height"),
                    "extension": str(raw.get("ext") or ""),
                }
            )
    return blocks


def _extract_with_pymupdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is not installed") from exc

    pages: list[dict[str, Any]] = []
    with fitz.open(path) as document:
        if not document.is_pdf:
            raise SourcePreparationError("PyMuPDF did not recognize the input as a PDF")
        for index, page in enumerate(document):
            text = _normalize_text(page.get_text("text", sort=True))
            layout_blocks = _pymupdf_blocks(page)
            pages.append(
                {
                    "page_number": index + 1,
                    "text": text,
                    "width_points": round(float(page.rect.width), 3),
                    "height_points": round(float(page.rect.height), 3),
                    "layout_blocks": layout_blocks,
                    "embedded_image_count": sum(block.get("type") == "image" for block in layout_blocks),
                    "vector_drawing_count": len(page.get_drawings()),
                }
            )
    return pages, {"name": "pymupdf", "version": _pymupdf_version(fitz)}


def _extract_with_pdfplumber(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        raise RuntimeError("pdfplumber is not installed") from exc

    pages: list[dict[str, Any]] = []
    with pdfplumber.open(path) as document:
        for index, page in enumerate(document.pages):
            text = _normalize_text(page.extract_text(layout=False))
            words = page.extract_words(use_text_flow=True, keep_blank_chars=False)
            rows: dict[float, list[dict[str, Any]]] = {}
            for word in words:
                rows.setdefault(round(float(word.get("top", 0)), 1), []).append(word)
            layout_blocks = []
            for _, row in sorted(rows.items()):
                row = sorted(row, key=lambda item: float(item.get("x0", 0)))
                row_text = " ".join(str(item.get("text") or "") for item in row).strip()
                if not row_text:
                    continue
                layout_blocks.append(
                    {
                        "type": "text",
                        "text": row_text,
                        "bbox": [
                            round(min(float(item.get("x0", 0)) for item in row), 3),
                            round(min(float(item.get("top", 0)) for item in row), 3),
                            round(max(float(item.get("x1", 0)) for item in row), 3),
                            round(max(float(item.get("bottom", 0)) for item in row), 3),
                        ],
                        "styles": [],
                    }
                )
            for image in page.images:
                layout_blocks.append(
                    {
                        "type": "image",
                        "bbox": _rounded_bbox([image.get("x0"), image.get("top"), image.get("x1"), image.get("bottom")]),
                        "width_pixels": image.get("srcsize", (None, None))[0] if isinstance(image.get("srcsize"), (list, tuple)) else None,
                        "height_pixels": image.get("srcsize", (None, None))[1] if isinstance(image.get("srcsize"), (list, tuple)) else None,
                        "extension": "",
                    }
                )
            pages.append(
                {
                    "page_number": index + 1,
                    "text": text,
                    "width_points": round(float(page.width), 3),
                    "height_points": round(float(page.height), 3),
                    "layout_blocks": layout_blocks,
                    "embedded_image_count": len(page.images),
                    "vector_drawing_count": 0,
                }
            )
    return pages, {"name": "pdfplumber", "version": str(pdfplumber.__version__)}


def _extract_pdf(path: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    errors: list[str] = []
    extractors: tuple[
        Callable[[Path], tuple[list[dict[str, Any]], dict[str, str]]], ...
    ] = (_extract_with_pymupdf, _extract_with_pdfplumber)
    for extractor in extractors:
        try:
            pages, parser = extractor(path)
            if not pages:
                raise SourcePreparationError("the PDF has no pages")
            return pages, parser
        except Exception as exc:  # each parser is an independent fallback
            errors.append(f"{extractor.__name__}: {type(exc).__name__}: {exc}")
    detail = "; ".join(errors)
    raise SourcePreparationError(f"unable to extract the PDF with available parsers: {detail}")


def _is_heading(line: str) -> bool:
    candidate = re.sub(r"\s+", " ", line).strip()
    if not candidate or len(candidate) > 120:
        return False
    if KNOWN_HEADING_RE.fullmatch(candidate) or NUMBERED_HEADING_RE.fullmatch(candidate):
        return True
    letters = [char for char in candidate if char.isalpha()]
    return bool(
        2 <= len(candidate.split()) <= 10
        and len(letters) >= 4
        and all(char.isupper() for char in letters)
        and not candidate.endswith((".", ":", ";", "?", "!"))
    )


def _detect_sections(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for page in pages:
        for line_number, line in enumerate(page["text"].splitlines(), start=1):
            heading = re.sub(r"\s+", " ", line).strip()
            if _is_heading(heading):
                found.append(
                    {
                        "section_id": f"section-{len(found) + 1:03d}",
                        "heading": heading,
                        "page_start": page["page_number"],
                        "page_end": page["page_number"],
                        "anchor": f"[p. {page['page_number']}, line {line_number}]",
                    }
                )
    for index, section in enumerate(found):
        next_page = found[index + 1]["page_start"] if index + 1 < len(found) else len(pages) + 1
        section["page_end"] = max(section["page_start"], next_page - 1)
    return found


def _excerpt(line: str, limit: int = 180) -> str:
    compact = re.sub(r"\s+", " ", line).strip()
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _collect_mentions(
    pages: list[dict[str, Any]], pattern: re.Pattern[str], kind: str
) -> list[dict[str, Any]]:
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for page in pages:
        text_blocks = [block for block in page.get("layout_blocks", []) if block.get("type") == "text"]
        if not text_blocks:
            text_blocks = [{"block_id": "page-text", "bbox": None, "text": page["text"]}]
        for block in text_blocks:
            for line_number, line in enumerate(str(block.get("text") or "").splitlines(), start=1):
                for match in pattern.finditer(line):
                    label = match.group(1).upper()
                    block_id = str(block.get("block_id") or "page-text")
                    key = (page["page_number"], block_id, label)
                    if key in seen:
                        continue
                    seen.add(key)
                    compact = re.sub(r"\s+", " ", line).strip().casefold()
                    caption_prefixes = {
                        "figure": ("fig", "figure", "\u56fe"),
                        "table": ("table", "\u8868"),
                        "equation": ("eq", "equation", "\u516c\u5f0f"),
                    }
                    mentions.append(
                        {
                            "mention_id": f"{kind}-{len(mentions) + 1:03d}",
                            "label": label,
                            "page_id": page["page_id"],
                            "page_number": page["page_number"],
                            "block_id": block_id,
                            "bbox_points": block.get("bbox_points"),
                            "role": "caption_candidate" if compact.startswith(caption_prefixes[kind]) else "cross_reference",
                            "anchor": f"[p. {page['page_number']}, block {block_id}, line {line_number}]",
                            "excerpt": _excerpt(line),
                        }
                    )
    return mentions


def _formula_candidates(pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates = []
    seen: set[tuple[str, int]] = set()
    for page in pages:
        for block in page.get("layout_blocks", []):
            if block.get("type") != "text":
                continue
            for line_number, line in enumerate(str(block.get("text") or "").splitlines(), start=1):
                compact = re.sub(r"\s+", " ", line).strip()
                if not compact or len(compact) > 180 or not FORMULA_CANDIDATE_RE.search(compact):
                    continue
                # Keep this a navigation candidate, not a semantic equation claim.
                alpha = sum(char.isalpha() for char in compact)
                operators = sum(char in "=+−-*/^≈≤≥∑∫√" for char in compact)
                if operators < 1 or (len(compact.split()) > 24 and alpha > 80):
                    continue
                key = (str(block["block_id"]), line_number)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "formula_candidate_id": f"formula-{len(candidates) + 1:03d}",
                        "page_id": page["page_id"],
                        "page_number": page["page_number"],
                        "block_id": block["block_id"],
                        "bbox_points": block.get("bbox_points"),
                        "anchor": f"[p. {page['page_number']}, block {block['block_id']}, line {line_number}]",
                        "excerpt": _excerpt(compact),
                        "status": "candidate_requires_visual_confirmation",
                    }
                )
    return candidates


def _decorate_pages(raw_pages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    for page in raw_pages:
        page_number = int(page["page_number"])
        text = _normalize_text(page.get("text"))
        raw_blocks = page.get("layout_blocks") or []
        if not raw_blocks and text:
            raw_blocks = [{"type": "text", "bbox": None, "text": text, "styles": []}]
        layout_blocks = []
        for index, block in enumerate(raw_blocks, start=1):
            block_type = str(block.get("type") or "text")
            block_id = f"page-{page_number:04d}-block-{index:04d}"
            decorated = {
                "block_id": block_id,
                "type": block_type,
                "anchor": f"[p. {page_number}, block {block_id}]",
                "bbox_points": block.get("bbox"),
            }
            if block_type == "text":
                block_text = _normalize_text(block.get("text"))
                decorated.update(
                    {
                        "text": block_text,
                        "char_count": len(block_text),
                        "line_count": len(block_text.splitlines()) if block_text else 0,
                        "styles": block.get("styles") or [],
                    }
                )
            else:
                decorated.update(
                    {
                        "width_pixels": block.get("width_pixels"),
                        "height_pixels": block.get("height_pixels"),
                        "extension": block.get("extension") or "",
                        "status": "visual_object_requires_inspection",
                    }
                )
            layout_blocks.append(decorated)
        pages.append(
            {
                "page_id": f"page-{page_number:04d}",
                "page_number": page_number,
                "anchor": f"[p. {page_number}]",
                "char_count": len(text),
                "line_count": len(text.splitlines()) if text else 0,
                "width_points": page.get("width_points"),
                "height_points": page.get("height_points"),
                "layout_blocks": layout_blocks,
                "embedded_image_count": int(page.get("embedded_image_count") or 0),
                "vector_drawing_count": int(page.get("vector_drawing_count") or 0),
                "text": text,
            }
        )
    return pages


def prepare_source(input_path: Path | str, min_ocr_chars: int = 40) -> dict[str, Any]:
    """Return a deterministic reader-source-bundle for ``input_path``."""
    path = Path(input_path)
    if min_ocr_chars < 0:
        raise SourcePreparationError("min_ocr_chars must be non-negative")
    if not path.is_file():
        raise SourcePreparationError(f"source file does not exist: {path}")

    suffix = path.suffix.casefold()
    if suffix not in PDF_SUFFIXES | TEXT_SUFFIXES:
        raise SourcePreparationError(
            f"unsupported source type {suffix or '<none>'}; expected PDF, TXT, MD, or MARKDOWN"
        )
    source_bytes = path.read_bytes()
    if suffix in PDF_SUFFIXES:
        if b"%PDF-" not in source_bytes[:1024]:
            raise SourcePreparationError("the .pdf file does not contain a valid PDF header")
        raw_pages, parser = _extract_pdf(path)
        source_format = "pdf"
        mime_type = "application/pdf"
    else:
        try:
            text = source_bytes.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise SourcePreparationError("text input must be UTF-8 encoded") from exc
        text = _normalize_text(text)
        if not text:
            raise SourcePreparationError("text input is empty")
        raw_pages = [{"page_number": 1, "text": text}]
        parser = {"name": "plain-text", "version": "utf-8"}
        source_format = "markdown" if suffix in {".md", ".markdown"} else "text"
        mime_type = "text/markdown" if source_format == "markdown" else "text/plain"

    pages = _decorate_pages(raw_pages)
    sections = _detect_sections(pages)
    visual_objects = []
    for page in pages:
        for block in page.get("layout_blocks", []):
            if block.get("type") == "image":
                visual_objects.append(
                    {
                        "visual_object_id": f"visual-{len(visual_objects) + 1:03d}",
                        "page_id": page["page_id"],
                        "page_number": page["page_number"],
                        "block_id": block["block_id"],
                        "bbox_points": block.get("bbox_points"),
                        "anchor": block["anchor"],
                        "status": "unclassified_image_requires_caption_and_visual_inspection",
                    }
                )
    inventories = {
        "figures": _collect_mentions(pages, FIGURE_RE, "figure"),
        "tables": _collect_mentions(pages, TABLE_RE, "table"),
        "equations": _collect_mentions(pages, EQUATION_RE, "equation"),
        "formula_candidates": _formula_candidates(pages),
        "visual_objects": visual_objects,
    }
    ocr_required_pages = []
    if source_format == "pdf":
        for page in pages:
            if page["char_count"] < min_ocr_chars:
                ocr_required_pages.append(
                    {
                        "page_id": page["page_id"],
                        "page_number": page["page_number"],
                        "anchor": page["anchor"],
                        "char_count": page["char_count"],
                        "threshold": min_ocr_chars,
                        "reason": "little_or_no_extractable_text",
                    }
                )

    page_count = len(pages)
    pages_with_text = sum(bool(page["text"]) for page in pages)
    usable_pages = page_count - len(ocr_required_pages)
    return {
        "schema_version": SCHEMA_VERSION,
        "bundle_type": "reader-source-bundle",
        "source": {
            "filename": path.name,
            "format": source_format,
            "mime_type": mime_type,
            "byte_count": len(source_bytes),
            "sha256": _sha256(source_bytes),
        },
        "extraction": {
            "parser": parser,
            "text_normalization": "UTF-8, LF newlines, trailing whitespace removed",
            "ocr_threshold_characters": min_ocr_chars if source_format == "pdf" else None,
        },
        "page_count": page_count,
        "pages": pages,
        "detected_sections": sections,
        "inventories": inventories,
        "ocr_required_pages": ocr_required_pages,
        "coverage": {
            "pages_total": page_count,
            "pages_with_text": pages_with_text,
            "pages_without_text": page_count - pages_with_text,
            "pages_needing_ocr": len(ocr_required_pages),
            "text_page_coverage_ratio": round(pages_with_text / page_count, 6),
            "usable_page_coverage_ratio": round(usable_pages / page_count, 6),
            "total_characters": sum(page["char_count"] for page in pages),
            "sections_detected": len(sections),
            "figure_mentions": len(inventories["figures"]),
            "table_mentions": len(inventories["tables"]),
            "equation_mentions": len(inventories["equations"]),
            "formula_candidates": len(inventories["formula_candidates"]),
            "visual_objects": len(inventories["visual_objects"]),
            "vector_drawings": sum(page["vector_drawing_count"] for page in pages),
            "layout_blocks": sum(len(page["layout_blocks"]) for page in pages),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="PDF, TXT, MD, or MARKDOWN source")
    parser.add_argument("--output", type=Path, help="destination JSON file")
    parser.add_argument(
        "--min-ocr-chars",
        type=int,
        default=40,
        help="mark PDF pages below this extracted-character count as needing OCR",
    )
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    try:
        if args.input is None or args.output is None:
            raise SourcePreparationError("--input and --output are required")
        bundle = prepare_source(args.input, min_ocr_chars=args.min_ocr_chars)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, SourcePreparationError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "output": str(args.output),
                "page_count": bundle["page_count"],
                "source_sha256": bundle["source"]["sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
