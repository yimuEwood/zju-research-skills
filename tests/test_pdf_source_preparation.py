from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "zju-paper-reader" / "scripts" / "prepare_source.py"


def load_module():
    spec = importlib.util.spec_from_file_location("paper_source_preparation", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


try:
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - dependency availability is environment-specific
    canvas = None


def make_research_pdf(path: Path) -> None:
    assert canvas is not None
    document = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
    document.setFont("Helvetica-Bold", 15)
    document.drawString(72, 740, "Abstract")
    document.setFont("Helvetica", 11)
    document.drawString(72, 710, "We test a bounded mechanism using independently prepared samples.")
    document.drawString(72, 690, "Figure 1 summarizes the design and Table 1 reports the samples.")
    document.drawString(72, 670, "Equation (1) defines the response as y = a x + b.")
    document.showPage()
    document.setFont("Helvetica-Bold", 15)
    document.drawString(72, 740, "2 Results")
    document.setFont("Helvetica", 11)
    document.drawString(72, 710, "The primary result is reported with uncertainty and a prespecified contrast.")
    document.drawString(72, 690, "Fig. 2 shows the response and Eq. (2) gives the fitted relation.")
    document.showPage()
    document.save()


class PdfSourcePreparationTests(unittest.TestCase):
    @unittest.skipUnless(canvas is not None, "reportlab is required to generate the real PDF fixture")
    def test_real_pdf_builds_deterministic_page_anchored_bundle(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "study.pdf"
            make_research_pdf(pdf_path)

            first = module.prepare_source(pdf_path)
            second = module.prepare_source(pdf_path)

            self.assertEqual(first, second)
            self.assertEqual(first["bundle_type"], "reader-source-bundle")
            self.assertEqual(first["page_count"], 2)
            self.assertEqual(first["source"]["sha256"], hashlib.sha256(pdf_path.read_bytes()).hexdigest())
            self.assertEqual([page["page_id"] for page in first["pages"]], ["page-0001", "page-0002"])
            self.assertEqual([page["anchor"] for page in first["pages"]], ["[p. 1]", "[p. 2]"])
            self.assertIn("Abstract", [row["heading"] for row in first["detected_sections"]])
            self.assertIn("2 Results", [row["heading"] for row in first["detected_sections"]])
            self.assertGreaterEqual(first["coverage"]["figure_mentions"], 2)
            self.assertGreaterEqual(first["coverage"]["table_mentions"], 1)
            self.assertGreaterEqual(first["coverage"]["equation_mentions"], 2)
            self.assertEqual(first["coverage"]["text_page_coverage_ratio"], 1.0)
            self.assertEqual(first["ocr_required_pages"], [])
            self.assertIn(first["extraction"]["parser"]["name"], {"pymupdf", "pdfplumber"})
            self.assertTrue(first["extraction"]["parser"]["version"])

    @unittest.skipUnless(canvas is not None, "reportlab is required to generate the real PDF fixture")
    def test_cli_writes_json_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            pdf_path = Path(temporary) / "study.pdf"
            output_path = Path(temporary) / "reader-source-bundle.json"
            make_research_pdf(pdf_path)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--input",
                    str(pdf_path),
                    "--output",
                    str(output_path),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            bundle = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(bundle["page_count"], 2)
            self.assertEqual(bundle["pages"][1]["anchor"], "[p. 2]")
            self.assertEqual(json.loads(completed.stdout)["source_sha256"], bundle["source"]["sha256"])

    def test_utf8_text_source_uses_same_anchor_contract(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "notes.md"
            source.write_text("# Introduction\nFigure 3 is discussed here.\n", encoding="utf-8")
            bundle = module.prepare_source(source)
            self.assertEqual(bundle["source"]["format"], "markdown")
            self.assertEqual(bundle["extraction"]["parser"], {"name": "plain-text", "version": "utf-8"})
            self.assertEqual(bundle["pages"][0]["page_id"], "page-0001")
            self.assertEqual(bundle["coverage"]["figure_mentions"], 1)
            self.assertEqual(bundle["ocr_required_pages"], [])

    def test_rejects_unsupported_and_fake_pdf_inputs(self):
        module = load_module()
        with tempfile.TemporaryDirectory() as temporary:
            unsupported = Path(temporary) / "paper.docx"
            unsupported.write_bytes(b"not a supported source")
            fake_pdf = Path(temporary) / "paper.pdf"
            fake_pdf.write_text("not actually a PDF", encoding="utf-8")
            with self.assertRaisesRegex(module.SourcePreparationError, "unsupported source type"):
                module.prepare_source(unsupported)
            with self.assertRaisesRegex(module.SourcePreparationError, "valid PDF header"):
                module.prepare_source(fake_pdf)


if __name__ == "__main__":
    unittest.main()
