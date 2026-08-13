# Full-paper reader schema

## Reader source bundle

Before interpretation, prepare `reader-source-bundle.json` from the exact local source. The deterministic bundle uses `schema_version: 1.0` and `bundle_type: reader-source-bundle`, and records:

- source filename, media type, byte count, and SHA-256;
- parser name/version and text-normalization rule;
- stable page IDs (`page-0001`) and anchors (`[p. 1]`) with extracted text and character counts;
- detected headings with page ranges and line-level anchors;
- figure, table, and equation mentions as navigation cues;
- pages that need OCR because little or no text was extractable;
- page, character, structure, and inventory coverage metrics.

Create it with `python scripts/prepare_source.py --input paper.pdf --output reader-source-bundle.json`. PDF extraction prefers PyMuPDF and falls back to pdfplumber when available. UTF-8 `.txt`, `.md`, and `.markdown` inputs are also accepted as one-page sources.

The bundle is an extraction record, not a reading result. Heading and object detection are heuristic. Confirm figures, tables, equations, captions, printed pagination, and OCR-required pages against the visible source before making claims from them.

## Source identity

Record title, authors, venue, year, DOI or stable identifier, version, local path or lawful URL, access date, PDF pages, extraction method, and the bundle's source SHA-256. If the source bytes change, prepare a new bundle rather than reusing old anchors.

## Coverage table

List every major section, figure, table, equation block, supplement, and status: `read`, `partially_read`, `not_available`, or `not_applicable`. Explain gaps.

## Bilingual-reader mode

For each source section include:

1. Original heading and page range.
2. Concise English reconstruction faithful to the source.
3. Chinese explanation with essential technical terms retained in parentheses.
4. Key equations with variables and assumptions.
5. Figure/table interpretation linked to captions and results text.
6. Source anchors such as `[p. 6, Results, Fig. 3b]`.

Avoid sentence-by-sentence duplication unless explicitly requested. Never translate a term in a way that changes its scientific scope.

## Required inventories

- Equation inventory: identifier, purpose, variables, assumptions, and anchor.
- Figure inventory: figure/panel, experimental or analytical role, primary comparison, uncertainty encoding, and anchor.
- Table inventory: table, variables/columns, population or sample, key result, and anchor.
- Evidence inventory: claim, evidence type, anchor, confidence, and caveat.
