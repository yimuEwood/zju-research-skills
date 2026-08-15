#!/usr/bin/env python3
"""Resolve one identifier to an official Europe PMC open JATS package.

This executor never handles publisher credentials or paywalled content.  Live
requests require both ``--live`` and ``ZJU_RESEARCH_LIVE_API=1``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx


SCHEMA_VERSION = "1.0"
LIVE_ENV = "ZJU_RESEARCH_LIVE_API"
ALLOWED_HOST = "www.ebi.ac.uk"
MAX_METADATA_BYTES = 4 * 1024 * 1024
MAX_JATS_BYTES = 30 * 1024 * 1024
TIMEOUT_SECONDS = 25.0
PMCID_RE = re.compile(r"^PMC\d+$", re.IGNORECASE)
PMID_RE = re.compile(r"^\d{1,12}$")


OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/yimuEwood/zju-research-skills/schemas/open-fulltext-package-1.0.json",
    "title": "ZJU lawful open full-text source package",
    "type": "object",
    "required": ["schema_version", "status", "target", "access", "artifact", "reader_handoff"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "status": {"enum": ["verified_open_fulltext"]},
        "target": {"type": "object", "required": ["pmcid"]},
        "access": {"type": "object", "required": ["provider", "route", "retrieved_at", "source_url"]},
        "artifact": {"type": "object", "required": ["filename", "mime_type", "byte_count", "sha256"]},
        "reader_handoff": {"type": "object", "required": ["ready", "source_level", "source_path"]},
    },
}


class FullTextError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, exit_code: int = 3) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


def normalize_doi(value: str) -> str:
    value = " ".join(str(value or "").split()).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.rstrip(".,;)")
    if value and not value.startswith("10."):
        raise FullTextError("invalid_identifier", "DOI must begin with 10.", exit_code=2)
    return value


def normalize_identifiers(*, doi: str = "", pmid: str = "", pmcid: str = "") -> dict[str, str]:
    normalized = {
        "doi": normalize_doi(doi),
        "pmid": " ".join(str(pmid or "").split()),
        "pmcid": " ".join(str(pmcid or "").split()).upper(),
    }
    supplied = sum(bool(value) for value in normalized.values())
    if supplied != 1:
        raise FullTextError("invalid_identifier", "supply exactly one of DOI, PMID, or PMCID", exit_code=2)
    if normalized["pmid"] and not PMID_RE.fullmatch(normalized["pmid"]):
        raise FullTextError("invalid_identifier", "PMID must contain digits only", exit_code=2)
    if normalized["pmcid"] and not PMCID_RE.fullmatch(normalized["pmcid"]):
        raise FullTextError("invalid_identifier", "PMCID must match PMC followed by digits", exit_code=2)
    return normalized


def _checked_url(url: str) -> None:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise FullTextError("blocked_endpoint", "invalid official API URL", exit_code=2) from exc
    if parsed.scheme != "https" or parsed.host != ALLOWED_HOST or parsed.userinfo:
        raise FullTextError("blocked_endpoint", "only credential-free HTTPS requests to Europe PMC are allowed", exit_code=2)


class OfficialClient:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self.client = httpx.Client(
            timeout=httpx.Timeout(TIMEOUT_SECONDS),
            follow_redirects=False,
            headers={"User-Agent": "zju-research-skills/1.0 (+https://github.com/yimuEwood/zju-research-skills)"},
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def get(self, url: str, params: dict[str, Any] | None, max_bytes: int) -> httpx.Response:
        _checked_url(url)
        try:
            response = self.client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise FullTextError("network_timeout", "Europe PMC request timed out", retryable=True, exit_code=4) from exc
        except httpx.HTTPError as exc:
            raise FullTextError("network_error", f"Europe PMC request failed: {exc}", retryable=True, exit_code=4) from exc
        if response.is_redirect:
            raise FullTextError("redirect_rejected", "official endpoint returned an unexpected redirect")
        if response.status_code != 200:
            retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504}
            raise FullTextError("http_status", f"Europe PMC returned HTTP {response.status_code}", retryable=retryable, exit_code=4 if retryable else 3)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise FullTextError("response_too_large", f"response declares more than {max_bytes} bytes")
        if len(response.content) > max_bytes:
            raise FullTextError("response_too_large", f"response exceeded {max_bytes} bytes")
        return response


def _resolve_pmcid(payload: Any, identifiers: dict[str, str]) -> tuple[str, dict[str, str]]:
    try:
        results = payload["resultList"]["result"]
        hit_count = payload["hitCount"]
    except (KeyError, TypeError) as exc:
        raise FullTextError("invalid_provider_payload", "Europe PMC HTTP 200 lookup lacks hitCount/resultList.result") from exc
    if not isinstance(hit_count, int) or not isinstance(results, list):
        raise FullTextError("invalid_provider_payload", "Europe PMC lookup fields have invalid types")
    eligible = []
    for result in results:
        if not isinstance(result, dict):
            continue
        candidate = str(result.get("pmcid") or "").upper()
        if PMCID_RE.fullmatch(candidate) and str(result.get("inEPMC") or "").upper() == "Y":
            eligible.append(result)
    if not eligible:
        raise FullTextError("open_fulltext_not_found", "no Europe PMC open full-text record matched the identifier")
    if len(eligible) > 1:
        exact = []
        for result in eligible:
            if identifiers["doi"] and normalize_doi(str(result.get("doi") or "")) == identifiers["doi"]:
                exact.append(result)
            elif identifiers["pmid"] and str(result.get("pmid") or result.get("id") or "") == identifiers["pmid"]:
                exact.append(result)
        eligible = exact
    if len(eligible) != 1:
        raise FullTextError("ambiguous_identifier", "multiple Europe PMC records matched; resolve identity before retrieval")
    result = eligible[0]
    return str(result["pmcid"]).upper(), {
        "doi": normalize_doi(str(result.get("doi") or identifiers["doi"])),
        "pmid": str(result.get("pmid") or result.get("id") or identifiers["pmid"]),
        "pmcid": str(result["pmcid"]).upper(),
    }


def _validate_jats(xml_bytes: bytes) -> ElementTree.Element:
    if b"<!DOCTYPE" in xml_bytes[:4096].upper() or b"<!ENTITY" in xml_bytes[:4096].upper():
        raise FullTextError("unsafe_xml", "JATS document contains a DTD or entity declaration")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise FullTextError("invalid_jats", f"full-text response is not well-formed XML: {exc}") from exc
    tag = root.tag.rsplit("}", 1)[-1]
    if tag not in {"article", "articles"}:
        raise FullTextError("invalid_jats", f"expected JATS article root, found {tag}")
    body = next((node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "body"), None)
    body_text = " ".join("".join(body.itertext()).split()) if body is not None else ""
    if not body_text:
        raise FullTextError("bodyless_jats", "JATS response has no readable article body")
    return root


def _license_text(root: ElementTree.Element) -> str:
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] == "license":
            value = " ".join("".join(node.itertext()).split())
            if value:
                return value[:1000]
    return ""


def fetch_open_fulltext(
    *,
    doi: str = "",
    pmid: str = "",
    pmcid: str = "",
    output_dir: Path,
    live: bool = False,
    snapshot: Path | None = None,
    record_snapshot: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    identifiers = normalize_identifiers(doi=doi, pmid=pmid, pmcid=pmcid)
    if live and os.environ.get(LIVE_ENV) != "1":
        raise FullTextError("live_opt_in_required", f"set {LIVE_ENV}=1 as well as --live", exit_code=2)
    if not live and snapshot is None:
        raise FullTextError("offline_source_required", "network is off; pass --snapshot or explicitly opt in with --live", exit_code=2)

    lookup_payload: dict[str, Any] | None = None
    xml_bytes: bytes
    client = OfficialClient(transport=transport)
    try:
        if live:
            if identifiers["pmcid"]:
                resolved = identifiers.copy()
                resolved_pmcid = identifiers["pmcid"]
            else:
                if identifiers["doi"]:
                    query = f'DOI:"{identifiers["doi"]}"'
                else:
                    query = f'EXT_ID:{identifiers["pmid"]} AND SRC:MED'
                lookup_url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
                response = client.get(lookup_url, {"query": query, "format": "json", "pageSize": 5, "resultType": "core"}, MAX_METADATA_BYTES)
                try:
                    lookup_payload = response.json()
                except ValueError as exc:
                    raise FullTextError("invalid_json", "Europe PMC lookup returned HTTP 200 with invalid JSON") from exc
                resolved_pmcid, resolved = _resolve_pmcid(lookup_payload, identifiers)
            jats_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{resolved_pmcid}/fullTextXML"
            xml_bytes = client.get(jats_url, None, MAX_JATS_BYTES).content
            mode = "live"
        else:
            assert snapshot is not None
            wrapper = json.loads(snapshot.read_text(encoding="utf-8"))
            if not isinstance(wrapper, dict) or wrapper.get("provider") != "europepmc":
                raise FullTextError("invalid_snapshot", "full-text snapshot wrapper is invalid", exit_code=2)
            lookup_payload = wrapper.get("lookup_response")
            if identifiers["pmcid"]:
                resolved_pmcid = identifiers["pmcid"]
                resolved = identifiers.copy()
                target = wrapper.get("target") if isinstance(wrapper.get("target"), dict) else {}
                if target.get("pmcid") and str(target["pmcid"]).upper() != resolved_pmcid:
                    raise FullTextError("snapshot_identifier_mismatch", "snapshot PMCID does not match the request", exit_code=2)
                resolved.update({key: str(target.get(key) or resolved[key]) for key in resolved})
            else:
                if not isinstance(lookup_payload, dict):
                    raise FullTextError("invalid_snapshot", "snapshot lacks a lookup response", exit_code=2)
                resolved_pmcid, resolved = _resolve_pmcid(lookup_payload, identifiers)
            xml_text = wrapper.get("jats_xml")
            if not isinstance(xml_text, str):
                raise FullTextError("invalid_snapshot", "snapshot lacks jats_xml text", exit_code=2)
            xml_bytes = xml_text.encode("utf-8")
            jats_url = str(wrapper.get("source_url") or f"https://www.ebi.ac.uk/europepmc/webservices/rest/{resolved_pmcid}/fullTextXML")
            mode = "recorded_snapshot"
    finally:
        client.close()

    root = _validate_jats(xml_bytes)
    if live and record_snapshot is not None:
        record_snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot_wrapper = {
            "snapshot_schema_version": "1.0",
            "provider": "europepmc",
            "target": resolved,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "source_url": jats_url,
            "lookup_response": lookup_payload,
            "jats_xml": xml_bytes.decode("utf-8"),
        }
        temporary = record_snapshot.with_suffix(".tmp")
        temporary.write_text(json.dumps(snapshot_wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(record_snapshot)
    output_dir.mkdir(parents=True, exist_ok=True)
    xml_path = output_dir / f"{resolved_pmcid}.xml"
    xml_path.write_bytes(xml_bytes)
    retrieved_at = datetime.now(timezone.utc).isoformat() if live else "recorded_snapshot"
    package = {
        "schema_version": SCHEMA_VERSION,
        "status": "verified_open_fulltext",
        "target": resolved,
        "access": {
            "provider": "Europe PMC",
            "route": "official_open_fulltext_api",
            "mode": mode,
            "retrieved_at": retrieved_at,
            "source_url": jats_url,
            "authentication_used": False,
            "paywall_bypassed": False,
        },
        "artifact": {
            "filename": xml_path.name,
            "mime_type": "application/xml",
            "byte_count": len(xml_bytes),
            "sha256": hashlib.sha256(xml_bytes).hexdigest(),
            "license_text": _license_text(root),
        },
        "reader_handoff": {
            "ready": True,
            "source_level": "full_text_jats",
            "source_path": str(xml_path),
            "record_id": f"pmcid:{resolved_pmcid}",
            "version_obtained": "europe_pmc_jats_snapshot" if not live else "europe_pmc_jats_live",
        },
    }
    manifest_path = output_dir / "source-pack.json"
    manifest_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    package["manifest_path"] = str(manifest_path)
    return package


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--doi")
    group.add_argument("--pmid")
    group.add_argument("--pmcid")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--record-snapshot", type=Path, help="write a raw live JATS snapshot for offline tests")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    try:
        if args.output_dir is None:
            raise FullTextError("missing_output", "--output-dir is required", exit_code=2)
        result = fetch_open_fulltext(
            doi=args.doi or "", pmid=args.pmid or "", pmcid=args.pmcid or "",
            output_dir=args.output_dir, live=args.live, snapshot=args.snapshot,
            record_snapshot=args.record_snapshot,
        )
        print(json.dumps({"ok": True, "source_pack": result["manifest_path"], "pmcid": result["target"]["pmcid"]}, ensure_ascii=False))
        return 0
    except FullTextError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except (OSError, json.JSONDecodeError) as exc:
        error = FullTextError("local_io_error", str(exc), exit_code=2)
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
