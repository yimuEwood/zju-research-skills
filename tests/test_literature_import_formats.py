from __future__ import annotations

import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_normalizer():
    path = ROOT / "skills/zju-literature-search/scripts/normalize_records.py"
    spec = importlib.util.spec_from_file_location("normalize_imports", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load normalizer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LiteratureImportFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = load_normalizer()

    def test_csv_ris_bibtex_and_nbib_merge_by_doi(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            csv_path = base / "records.csv"
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["title", "authors", "year", "doi", "source"])
                writer.writeheader()
                writer.writerow({
                    "title": "跨格式科研记录",
                    "authors": "Li Ming;Wang Fang",
                    "year": "2024",
                    "doi": "https://doi.org/10.1234/ABC.1",
                    "source": "CSV export",
                })
            ris_path = base / "records.ris"
            ris_path.write_text(
                "TY  - JOUR\nTI  - 跨格式科研记录\nAU  - Li, Ming\nAU  - Wang, Fang\n"
                "PY  - 2024\nDO  - 10.1234/abc.1\nDB  - RIS database\nAB  - First line\n"
                "      continued abstract\nER  -\n",
                encoding="utf-8",
            )
            bib_path = base / "records.bib"
            bib_path.write_text(
                "@article{li2024,\n  title = {跨格式科研记录},\n"
                "  author = {Li, Ming and Wang, Fang},\n  year = {2024},\n"
                "  doi = {10.1234/ABC.1},\n  journal = {Journal of Tests}\n}\n",
                encoding="utf-8",
            )
            nbib_path = base / "records.nbib"
            nbib_path.write_text(
                "PMID- 12345678\nTI  - 跨格式科研记录\nFAU - Li, Ming\nFAU - Wang, Fang\n"
                "DP  - 2024\nAID - 10.1234/abc.1 [doi]\nDB  - PubMed\n\n",
                encoding="utf-8",
            )

            source_records = []
            for path in (csv_path, ris_path, bib_path, nbib_path):
                source_records.extend(self.module.read_records(path))
            normalized = self.module.deduplicate(source_records)

        self.assertEqual(len(normalized), 1)
        record = normalized[0]
        self.assertEqual(record["doi"], "10.1234/abc.1")
        self.assertEqual(record["source_database"], ["BibTeX import", "CSV export", "PubMed", "RIS database"])
        self.assertIn("continued abstract", record["abstract"])
        self.assertEqual(record["source_record_positions"], [1, 2, 3, 4])

    def test_bibtex_nested_braces_and_unicode_authors(self):
        content = (
            "@inproceedings{key-1, title={A {Nested} Title}, "
            "author={张三 and Li, Ming}, year=2025, booktitle={Materials Conference}, "
            "eprint={arXiv:2501.00001}, keywords={perovskite, stability}}"
        )
        records = self.module.parse_bibtex(content)
        self.assertEqual(records[0]["title"], "A Nested Title")
        self.assertEqual(records[0]["authors"], ["张三", "Li, Ming"])
        self.assertEqual(records[0]["other_identifier"], "arXiv:2501.00001")
        self.assertEqual(records[0]["unmapped_fields"]["keywords"], "perovskite, stability")

    def test_identity_free_rows_remain_distinct_across_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            path.write_text("source\nExport A\nExport A\n", encoding="utf-8")
            normalized = self.module.deduplicate(self.module.read_records(path))
        self.assertEqual(len(normalized), 2)
        self.assertTrue(all(item["identity_missing"] for item in normalized))

    def test_unsupported_format_fails_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.xml"
            path.write_text("<records />", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Supported inputs"):
                self.module.read_records(path)


if __name__ == "__main__":
    unittest.main()
