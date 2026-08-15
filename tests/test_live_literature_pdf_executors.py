from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOTS = ROOT / "evals" / "fixtures" / "provider-snapshots"
DOI = "10.1038/s41586-020-2649-2"
PMCID = "PMC7759461"


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover
    canvas = None


class LiveLiteraturePdfExecutorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.providers = load_module(
            "zju_query_providers_test",
            "skills/zju-literature-search/scripts/query_providers.py",
        )
        cls.fulltext = load_module(
            "zju_fetch_open_fulltext_test",
            "skills/zju-fulltext-access/scripts/fetch_open_fulltext.py",
        )
        cls.resolver = load_module(
            "zju_resolve_reference_test",
            "skills/zju-reference-audit/scripts/resolve_reference.py",
        )
        cls.jats = load_module(
            "zju_parse_jats_test",
            "skills/zju-paper-reader/scripts/parse_jats.py",
        )
        cls.pdf = load_module(
            "zju_prepare_source_deep_test",
            "skills/zju-paper-reader/scripts/prepare_source.py",
        )

    def test_recorded_four_provider_search_uses_real_parsers_and_schema(self):
        manifest = json.loads((SNAPSHOTS / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["hash_mode"], "canonical_lf_text")
        for item in manifest["files"]:
            path = SNAPSHOTS / item["path"]
            canonical_bytes = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            self.assertEqual(hashlib.sha256(canonical_bytes).hexdigest(), item["sha256"])
        result = self.providers.search(
            DOI,
            list(self.providers.PROVIDERS),
            page_size=5,
            pages=1,
            snapshot_dir=SNAPSHOTS,
        )
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["execution"]["mode"], "recorded_snapshot")
        exact = [row for row in result["records"] if row["doi"] == DOI]
        self.assertEqual({row["provider"] for row in exact}, set(self.providers.PROVIDERS))
        self.assertTrue(all(row["source_id"] and row["landing_url"] for row in exact))
        Draft202012Validator(self.providers.OUTPUT_SCHEMA).validate(result)
        file_schema = json.loads(
            (ROOT / "skills/zju-literature-search/references/provider-search-output.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator(file_schema).validate(result)

    def test_network_is_double_opt_in_and_pagination_is_bounded(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(self.providers.ProviderError, "ZJU_RESEARCH_LIVE_API"):
                self.providers.search("numpy", ["crossref"], live=True)
        with self.assertRaisesRegex(self.providers.ProviderError, "pages must be"):
            self.providers.search("numpy", ["crossref"], pages=4, snapshot_dir=SNAPSHOTS)
        with self.assertRaisesRegex(self.providers.ProviderError, "page_size must be"):
            self.providers.search("numpy", ["crossref"], page_size=51, snapshot_dir=SNAPSHOTS)

    def test_http_200_silent_provider_failure_is_not_treated_as_zero_results(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "message": {}}, request=request)

        with patch.dict(os.environ, {"ZJU_RESEARCH_LIVE_API": "1"}):
            result = self.providers.search(
                "bounded test",
                ["crossref"],
                page_size=1,
                live=True,
                transport=httpx.MockTransport(handler),
            )
        self.assertEqual(result["records"], [])
        self.assertTrue(result["execution"]["partial"])
        self.assertEqual(result["failures"][0]["code"], "invalid_provider_payload")

    def test_live_cache_is_opt_in_and_reuses_raw_validated_payload(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            payload = {
                "status": "ok",
                "message": {
                    "items": [{
                        "DOI": DOI,
                        "title": ["Array programming with NumPy"],
                        "author": [{"given": "Charles", "family": "Harris"}],
                        "issued": {"date-parts": [[2020]]},
                        "container-title": ["Nature"],
                        "URL": f"https://doi.org/{DOI}",
                    }]
                },
            }
            return httpx.Response(200, json=payload, request=request)

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ZJU_RESEARCH_LIVE_API": "1"}):
            transport = httpx.MockTransport(handler)
            first = self.providers.search(DOI, ["crossref"], page_size=1, live=True, cache_dir=Path(directory), transport=transport)
            second = self.providers.search(DOI, ["crossref"], page_size=1, live=True, cache_dir=Path(directory), transport=transport)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first["records"], second["records"])

    def test_reference_resolution_requires_exact_identifier_and_two_sources(self):
        result = self.resolver.resolve_reference(doi=DOI, snapshot_dir=SNAPSHOTS)
        self.assertEqual(result["status"], "verified_two_source")
        self.assertEqual(set(result["matched_sources"]), set(self.providers.PROVIDERS))
        self.assertEqual(result["canonical"]["doi"], DOI)
        self.assertFalse(result["conflicting_fields"])
        Draft202012Validator(self.resolver.OUTPUT_SCHEMA).validate(result)

    def test_recorded_oa_jats_fetch_writes_hash_verified_source_pack(self):
        snapshot = SNAPSHOTS / "europepmc-fulltext.json"
        with tempfile.TemporaryDirectory() as directory:
            result = self.fulltext.fetch_open_fulltext(
                doi=DOI,
                output_dir=Path(directory),
                snapshot=snapshot,
            )
            xml_path = Path(result["reader_handoff"]["source_path"])
            self.assertTrue(xml_path.is_file())
            self.assertEqual(result["artifact"]["sha256"], hashlib.sha256(xml_path.read_bytes()).hexdigest())
            self.assertEqual(result["access"]["route"], "official_open_fulltext_api")
            self.assertFalse(result["access"]["authentication_used"])
            self.assertFalse(result["access"]["paywall_bypassed"])
            Draft202012Validator(self.fulltext.OUTPUT_SCHEMA).validate(result)

            bundle = self.jats.parse_jats(xml_path)
            self.assertEqual(bundle["article"]["identifiers"]["pmcid"], PMCID)
            self.assertGreater(bundle["coverage"]["paragraphs"], 20)
            self.assertGreater(bundle["coverage"]["figures"], 0)
            self.assertGreater(bundle["coverage"]["references"], 20)
            figure = bundle["inventories"]["figures"][0]
            self.assertIn("sec=", figure["anchor"])
            self.assertTrue(figure["caption"])
            Draft202012Validator(self.jats.OUTPUT_SCHEMA).validate(bundle)

    def test_bodyless_jats_and_http_200_lookup_failure_are_explicit(self):
        bodyless = b"<article><front><article-meta><title-group><article-title>X</article-title></title-group></article-meta></front></article>"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bodyless.xml"
            path.write_bytes(bodyless)
            with self.assertRaisesRegex(self.jats.JatsParseError, "no readable article body"):
                self.jats.parse_jats(path)
        with self.assertRaisesRegex(self.fulltext.FullTextError, "no readable article body"):
            self.fulltext._validate_jats(bodyless)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"request": {"query": DOI}}, request=request)

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"ZJU_RESEARCH_LIVE_API": "1"}):
            with self.assertRaisesRegex(self.fulltext.FullTextError, "lacks hitCount"):
                self.fulltext.fetch_open_fulltext(
                    doi=DOI,
                    output_dir=Path(directory),
                    live=True,
                    transport=httpx.MockTransport(handler),
                )

    def test_fulltext_executor_rejects_arbitrary_hosts_and_credentials(self):
        with self.assertRaisesRegex(self.fulltext.FullTextError, "Europe PMC"):
            self.fulltext._checked_url("https://example.org/article.xml")
        with self.assertRaisesRegex(self.fulltext.FullTextError, "credential-free"):
            self.fulltext._checked_url("https://user:secret@www.ebi.ac.uk/article.xml")

    @unittest.skipUnless(canvas is not None, "reportlab is required for the real PDF fixture")
    def test_real_pdf_has_layout_bbox_caption_formula_and_vector_anchors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "anchored.pdf"
            document = canvas.Canvas(str(path), pagesize=(612, 792), invariant=1)
            document.setFont("Helvetica-Bold", 15)
            document.drawString(72, 740, "Results")
            document.setFont("Helvetica", 11)
            document.drawString(72, 710, "Figure 1. Measured response with uncertainty.")
            document.drawString(72, 680, "Equation (1): y = a x + b")
            document.rect(72, 500, 240, 120)
            document.showPage()
            document.save()

            bundle = self.pdf.prepare_source(path)
            self.assertEqual(bundle["schema_version"], "1.1")
            self.assertGreater(bundle["coverage"]["layout_blocks"], 0)
            self.assertGreater(bundle["coverage"]["vector_drawings"], 0)
            first_block = bundle["pages"][0]["layout_blocks"][0]
            self.assertTrue(first_block["block_id"])
            self.assertEqual(len(first_block["bbox_points"]), 4)
            figure = bundle["inventories"]["figures"][0]
            self.assertEqual(figure["role"], "caption_candidate")
            self.assertTrue(figure["block_id"])
            self.assertEqual(len(figure["bbox_points"]), 4)
            self.assertGreaterEqual(bundle["coverage"]["formula_candidates"], 1)
            self.assertIn("block", bundle["inventories"]["formula_candidates"][0]["anchor"])
            Draft202012Validator(self.pdf.OUTPUT_SCHEMA).validate(bundle)

    def test_each_new_cli_emits_a_json_schema_without_network(self):
        scripts = [
            "skills/zju-literature-search/scripts/query_providers.py",
            "skills/zju-fulltext-access/scripts/fetch_open_fulltext.py",
            "skills/zju-reference-audit/scripts/resolve_reference.py",
            "skills/zju-paper-reader/scripts/prepare_source.py",
            "skills/zju-paper-reader/scripts/parse_jats.py",
        ]
        for relative in scripts:
            completed = subprocess.run(
                [sys.executable, str(ROOT / relative), "--print-schema"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            schema = json.loads(completed.stdout)
            Draft202012Validator.check_schema(schema)


if __name__ == "__main__":
    unittest.main()
