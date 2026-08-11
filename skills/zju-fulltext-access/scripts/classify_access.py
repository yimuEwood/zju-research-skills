#!/usr/bin/env python3
"""Classify likely lawful full-text routes from local metadata; no network access."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SENSITIVE_KEYS = {"password", "passwd", "cookie", "token", "secret", "credential", "session"}


def text(value: Any) -> str:
    return " ".join(str(value or "").split())


def normalized_doi(value: Any) -> str:
    doi = text(value).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
            break
    return doi.rstrip(".,;)")


def has_sensitive_data(value: Any, path: str = "") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).casefold() in SENSITIVE_KEYS and item not in (None, "", False):
                findings.append(child)
            findings.extend(has_sensitive_data(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(has_sensitive_data(item, f"{path}[{index}]"))
    return findings


def classify(record: dict[str, Any]) -> dict[str, Any]:
    sensitive = has_sensitive_data(record)
    if sensitive:
        return {"status": "blocked_sensitive_input", "route": "remove_credentials", "sensitive_fields": sensitive}
    oa_url = record.get("oa_url") or record.get("public_fulltext_url")
    repository_url = record.get("repository_url")
    api_url = record.get("api_url")
    publisher_url = record.get("publisher_url") or record.get("landing_url")
    database = record.get("database") or record.get("publisher")
    doi = normalized_doi(record.get("doi"))
    routes: list[dict[str, Any]] = []
    if oa_url:
        routes.append({"route": "open_access", "url": oa_url, "status": "likely", "rank": 1})
    if repository_url and repository_url != oa_url:
        routes.append({"route": "repository_copy", "url": repository_url, "status": "likely", "rank": 2})
    if api_url and record.get("api_access") == "public":
        routes.append({"route": "official_public_api", "url": api_url, "status": "likely", "rank": 3})
    if publisher_url:
        routes.append({"route": "publisher_landing_page", "url": publisher_url, "status": "unresolved", "rank": 4})
    if database:
        routes.append({"route": "zju_library_interactive", "url": "https://libweb.zju.edu.cn/56334/list.htm", "status": "subscription_required", "rank": 5})
    if doi:
        routes.append({"route": "manual_identifier_lookup", "url": f"https://doi.org/{doi}", "status": "unresolved", "rank": 6})
    routes.append({"route": "manual_citation_resolution", "url": "", "status": "unresolved", "rank": 7})
    if oa_url:
        route, status, url = "open_access", "likely", oa_url
    elif repository_url:
        route, status, url = "repository_copy", "likely", repository_url
    elif api_url and record.get("api_access") == "public":
        route, status, url = "official_public_api", "likely", api_url
    elif database:
        route, status, url = "zju_library_interactive", "subscription_required", "https://libweb.zju.edu.cn/56334/list.htm"
    elif doi:
        route, status, url = "manual_identifier_lookup", "unresolved", f"https://doi.org/{doi}"
    else:
        route, status, url = "manual_citation_resolution", "unresolved", ""
    component_urls = {
        "full_text": url if route in {"open_access", "repository_copy", "official_public_api"} else "",
        "supplement": text(record.get("supplement_url")),
        "protocol_or_registration": text(record.get("protocol_url") or record.get("registration_url")),
        "data": text(record.get("data_url")),
        "code": text(record.get("code_url")),
        "correction_or_retraction_notice": text(record.get("correction_url") or record.get("retraction_url")),
    }
    requested = record.get("requested_components") or ["full_text"]
    if not isinstance(requested, list):
        requested = [requested]
    requested = [text(item) for item in requested if text(item)]
    missing = [name for name in requested if not component_urls.get(name)]
    target_id = text(record.get("record_id")) or (f"doi:{doi}" if doi else text(record.get("title")))
    return {
        "status": status,
        "route": route,
        "url": url,
        "route_candidates": routes,
        "requires_interactive_authentication": route == "zju_library_interactive",
        "automation_allowed": route in {"open_access", "repository_copy", "official_public_api"},
        "requested_components": requested,
        "available_components": sorted(name for name, value in component_urls.items() if value),
        "missing_components": missing,
        "source_package_complete": not missing,
        "reader_handoff": {
            "record_id": target_id,
            "version_requested": text(record.get("version_requested")) or "not_specified",
            "version_obtained": text(record.get("version_obtained")) or "not_verified",
            "primary_url": component_urls["full_text"],
            "source_level": "full_text_candidate" if component_urls["full_text"] else "metadata_only",
            "component_urls": component_urls,
            "ready": bool(component_urls["full_text"]) and not missing,
        },
        "note": "Classification is not proof of access rights; verify on the target page.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    records = data if isinstance(data, list) else [data]
    result = [{"target": item.get("doi") or item.get("title") or str(index), **classify(item)} for index, item in enumerate(records, 1)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"classified": len(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
