#!/usr/bin/env python3
"""Run bounded scholarly searches against public APIs or recorded snapshots.

Network access is disabled unless ``ZJU_RESEARCH_LIVE_API=1`` and ``--live`` are
both present.  Offline snapshots use the same provider parsers as live calls.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import httpx


SCHEMA_VERSION = "1.0"
LIVE_ENV = "ZJU_RESEARCH_LIVE_API"
MAX_PAGES = 3
MAX_PAGE_SIZE = 50
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TIMEOUT_SECONDS = 20.0
ALLOWED_HOSTS = {
    "api.crossref.org",
    "api.openalex.org",
    "www.ebi.ac.uk",
    "eutils.ncbi.nlm.nih.gov",
}
PROVIDERS = ("crossref", "openalex", "europepmc", "pubmed")


OUTPUT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://github.com/yimuEwood/zju-research-skills/schemas/provider-search-output-1.0.json",
    "title": "ZJU bounded provider search result",
    "type": "object",
    "required": ["schema_version", "query", "providers", "records", "failures", "execution"],
    "properties": {
        "schema_version": {"const": SCHEMA_VERSION},
        "query": {"type": "string", "minLength": 1},
        "providers": {"type": "array", "items": {"enum": list(PROVIDERS)}},
        "records": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "provider", "source_id", "title", "authors", "year", "venue",
                    "doi", "pmid", "pmcid", "landing_url", "source_database",
                ],
            },
        },
        "failures": {"type": "array", "items": {"type": "object"}},
        "execution": {
            "type": "object",
            "required": ["mode", "page_size", "pages_requested", "network_enabled"],
        },
    },
}


class ProviderError(RuntimeError):
    """Structured provider failure that is safe to serialize."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str = "",
        retryable: bool = False,
        exit_code: int = 3,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.retryable = retryable
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "provider": self.provider or None,
            "retryable": self.retryable,
        }


def _text(value: Any) -> str:
    if isinstance(value, list):
        return " ".join(_text(item) for item in value if _text(item))
    return " ".join(str(value or "").split())


def _doi(value: Any) -> str:
    raw = _text(value).lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    return raw.rstrip(".,;)")


def _pmid(value: Any) -> str:
    raw = _text(value).rstrip("/")
    for prefix in ("https://pubmed.ncbi.nlm.nih.gov/", "http://pubmed.ncbi.nlm.nih.gov/", "pmid:"):
        if raw.casefold().startswith(prefix):
            raw = raw[len(prefix) :].rstrip("/")
            break
    return raw


def _pmcid(value: Any) -> str:
    raw = _text(value).rstrip("/")
    for prefix in ("https://www.ncbi.nlm.nih.gov/pmc/articles/", "https://pmc.ncbi.nlm.nih.gov/articles/", "pmcid:"):
        if raw.casefold().startswith(prefix):
            raw = raw[len(prefix) :].rstrip("/")
            break
    return raw.upper()


def _year_from_parts(value: Any) -> int | None:
    try:
        candidate = value[0][0]
        return int(candidate) if 1000 <= int(candidate) <= 9999 else None
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def _abstract_from_inverted(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positioned: list[tuple[int, str]] = []
    for token, positions in index.items():
        if not isinstance(positions, list):
            continue
        for position in positions:
            if isinstance(position, int) and position >= 0:
                positioned.append((position, str(token)))
    return " ".join(token for _, token in sorted(positioned))


def _stable_source_id(provider: str, raw_id: Any, doi: str, pmid: str, title: str) -> str:
    candidate = _text(raw_id)
    if candidate:
        return candidate
    identity = doi or pmid or title.casefold()
    digest = hashlib.sha256(f"{provider}:{identity}".encode("utf-8")).hexdigest()[:24]
    return f"{provider}:{digest}"


def _record(
    provider: str,
    *,
    raw_id: Any,
    title: Any,
    authors: list[str],
    year: int | None,
    venue: Any,
    abstract: Any = "",
    doi: Any = "",
    pmid: Any = "",
    pmcid: Any = "",
    landing_url: Any = "",
    record_type: Any = "",
    cited_by_count: int | None = None,
) -> dict[str, Any]:
    title_text = _text(title)
    normalized_doi = _doi(doi)
    normalized_pmid = _pmid(pmid)
    normalized_pmcid = _pmcid(pmcid)
    return {
        "provider": provider,
        "source_id": _stable_source_id(provider, raw_id, normalized_doi, normalized_pmid, title_text),
        "title": title_text,
        "authors": [_text(item) for item in authors if _text(item)],
        "year": year,
        "venue": _text(venue),
        "abstract": _text(abstract),
        "doi": normalized_doi,
        "pmid": normalized_pmid,
        "pmcid": normalized_pmcid,
        "landing_url": _text(landing_url),
        "record_type": _text(record_type),
        "cited_by_count": cited_by_count,
        "source_database": [provider],
        "verification_status": "provider_metadata_unverified_against_second_source",
    }


def parse_crossref(payload: Any) -> list[dict[str, Any]]:
    try:
        if payload.get("status") != "ok":
            raise KeyError("status")
        items = payload["message"]["items"]
    except (AttributeError, KeyError, TypeError) as exc:
        raise ProviderError("invalid_provider_payload", "Crossref HTTP 200 payload lacks status=ok/message.items", provider="crossref") from exc
    if not isinstance(items, list):
        raise ProviderError("invalid_provider_payload", "Crossref message.items is not an array", provider="crossref")
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        authors = []
        for author in item.get("author") or []:
            if isinstance(author, dict):
                name = _text(" ".join(filter(None, [author.get("given"), author.get("family")])))
                if name:
                    authors.append(name)
        year = _year_from_parts((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts"))
        records.append(
            _record(
                "crossref", raw_id=item.get("DOI"), title=item.get("title"), authors=authors,
                year=year, venue=item.get("container-title"), abstract=item.get("abstract"),
                doi=item.get("DOI"), landing_url=item.get("URL"), record_type=item.get("type"),
                cited_by_count=item.get("is-referenced-by-count") if isinstance(item.get("is-referenced-by-count"), int) else None,
            )
        )
    return records


def parse_openalex(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list) or not isinstance(payload.get("meta"), dict):
        raise ProviderError("invalid_provider_payload", "OpenAlex HTTP 200 payload lacks meta/results", provider="openalex")
    records = []
    for item in payload["results"]:
        if not isinstance(item, dict):
            continue
        authors = []
        for authorship in item.get("authorships") or []:
            if isinstance(authorship, dict) and isinstance(authorship.get("author"), dict):
                name = _text(authorship["author"].get("display_name"))
                if name:
                    authors.append(name)
        primary = item.get("primary_location") if isinstance(item.get("primary_location"), dict) else {}
        source = primary.get("source") if isinstance(primary.get("source"), dict) else {}
        ids = item.get("ids") if isinstance(item.get("ids"), dict) else {}
        records.append(
            _record(
                "openalex", raw_id=item.get("id"), title=item.get("display_name") or item.get("title"),
                authors=authors, year=item.get("publication_year") if isinstance(item.get("publication_year"), int) else None,
                venue=source.get("display_name"), abstract=_abstract_from_inverted(item.get("abstract_inverted_index")),
                doi=item.get("doi") or ids.get("doi"), pmid=ids.get("pmid"), pmcid=ids.get("pmcid"),
                landing_url=primary.get("landing_page_url") or item.get("id"), record_type=item.get("type"),
                cited_by_count=item.get("cited_by_count") if isinstance(item.get("cited_by_count"), int) else None,
            )
        )
    return records


def parse_europepmc(payload: Any) -> list[dict[str, Any]]:
    try:
        results = payload["resultList"]["result"]
        hit_count = payload["hitCount"]
    except (KeyError, TypeError) as exc:
        raise ProviderError("invalid_provider_payload", "Europe PMC HTTP 200 payload lacks hitCount/resultList.result", provider="europepmc") from exc
    if not isinstance(results, list) or not isinstance(hit_count, int):
        raise ProviderError("invalid_provider_payload", "Europe PMC resultList.result or hitCount has an invalid type", provider="europepmc")
    records = []
    for item in results:
        if not isinstance(item, dict):
            continue
        authors = []
        author_list = item.get("authorList") if isinstance(item.get("authorList"), dict) else {}
        for author in author_list.get("author") or []:
            if isinstance(author, dict):
                name = _text(author.get("fullName") or " ".join(filter(None, [author.get("firstName"), author.get("lastName")])))
                if name:
                    authors.append(name)
        records.append(
            _record(
                "europepmc", raw_id=item.get("id") or item.get("pmid") or item.get("pmcid"), title=item.get("title"),
                authors=authors, year=int(item["pubYear"]) if str(item.get("pubYear", "")).isdigit() else None,
                venue=item.get("journalTitle"), abstract=item.get("abstractText"), doi=item.get("doi"),
                pmid=item.get("pmid"), pmcid=item.get("pmcid"),
                landing_url=f"https://europepmc.org/article/{_text(item.get('source'))}/{_text(item.get('id'))}" if item.get("id") else "",
                record_type=item.get("pubType"), cited_by_count=int(item["citedByCount"]) if str(item.get("citedByCount", "")).isdigit() else None,
            )
        )
    return records


def parse_pubmed(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("esearch"), dict) or not isinstance(payload.get("esummary"), dict):
        raise ProviderError("invalid_provider_payload", "PubMed snapshot must contain esearch and esummary objects", provider="pubmed")
    search = payload["esearch"].get("esearchresult")
    summary = payload["esummary"].get("result")
    if not isinstance(search, dict) or not isinstance(search.get("idlist"), list) or not isinstance(summary, dict) or not isinstance(summary.get("uids"), list):
        raise ProviderError("invalid_provider_payload", "PubMed HTTP 200 payload lacks esearchresult.idlist or result.uids", provider="pubmed")
    records = []
    for uid in summary["uids"]:
        item = summary.get(str(uid))
        if not isinstance(item, dict):
            raise ProviderError("invalid_provider_payload", f"PubMed summary silently omitted PMID {uid}", provider="pubmed")
        article_ids = {}
        for identifier in item.get("articleids") or []:
            if isinstance(identifier, dict) and identifier.get("idtype"):
                article_ids[str(identifier["idtype"]).casefold()] = identifier.get("value")
        authors = [_text(author.get("name")) for author in item.get("authors") or [] if isinstance(author, dict) and _text(author.get("name"))]
        pubdate = _text(item.get("pubdate"))
        year = int(pubdate[:4]) if len(pubdate) >= 4 and pubdate[:4].isdigit() else None
        records.append(
            _record(
                "pubmed", raw_id=uid, title=item.get("title"), authors=authors, year=year,
                venue=item.get("fulljournalname") or item.get("source"), doi=article_ids.get("doi"),
                pmid=article_ids.get("pubmed") or uid, pmcid=article_ids.get("pmc"),
                landing_url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/", record_type="; ".join(item.get("pubtype") or []),
            )
        )
    return records


PARSERS = {
    "crossref": parse_crossref,
    "openalex": parse_openalex,
    "europepmc": parse_europepmc,
    "pubmed": parse_pubmed,
}


def _validate_url(url: str) -> None:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise ProviderError("blocked_endpoint", f"invalid provider URL: {url}", exit_code=2) from exc
    if parsed.scheme != "https" or parsed.host not in ALLOWED_HOSTS:
        raise ProviderError("blocked_endpoint", f"endpoint must use HTTPS and an allowlisted host: {url}", exit_code=2)
    if parsed.userinfo:
        raise ProviderError("blocked_endpoint", "credentials are forbidden in provider URLs", exit_code=2)


class BoundedHttpClient:
    def __init__(self, *, transport: httpx.BaseTransport | None = None) -> None:
        headers = {
            "Accept": "application/json",
            "User-Agent": "zju-research-skills/1.0 (+https://github.com/yimuEwood/zju-research-skills)",
        }
        self.client = httpx.Client(
            timeout=httpx.Timeout(TIMEOUT_SECONDS), follow_redirects=False, headers=headers, transport=transport
        )

    def close(self) -> None:
        self.client.close()

    def get_json(self, provider: str, url: str, params: dict[str, Any]) -> dict[str, Any]:
        _validate_url(url)
        try:
            response = self.client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise ProviderError("network_timeout", f"{provider} request timed out", provider=provider, retryable=True, exit_code=4) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("network_error", f"{provider} request failed: {exc}", provider=provider, retryable=True, exit_code=4) from exc
        if response.is_redirect:
            raise ProviderError("redirect_rejected", f"{provider} returned a redirect; endpoint drift requires review", provider=provider)
        if response.status_code != 200:
            retryable = response.status_code in {408, 425, 429, 500, 502, 503, 504}
            raise ProviderError("http_status", f"{provider} returned HTTP {response.status_code}", provider=provider, retryable=retryable, exit_code=4 if retryable else 3)
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
            raise ProviderError("response_too_large", f"{provider} declared more than {MAX_RESPONSE_BYTES} bytes", provider=provider)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError("response_too_large", f"{provider} response exceeded {MAX_RESPONSE_BYTES} bytes", provider=provider)
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("invalid_json", f"{provider} returned HTTP 200 with invalid JSON", provider=provider) from exc
        if not isinstance(payload, dict):
            raise ProviderError("invalid_provider_payload", f"{provider} top-level payload is not an object", provider=provider)
        return payload


def _cache_key(provider: str, url: str, params: dict[str, Any]) -> str:
    canonical = f"{provider}\n{url}?{httpx.QueryParams(sorted((str(k), str(v)) for k, v in params.items()))}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_read(cache_dir: Path | None, provider: str, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
    if cache_dir is None:
        return None
    path = cache_dir / provider / f"{_cache_key(provider, url, params)}.json"
    if not path.is_file():
        return None
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    payload = wrapper.get("response") if isinstance(wrapper, dict) else None
    if not isinstance(payload, dict):
        raise ProviderError("invalid_cache", f"invalid cache entry: {path}", provider=provider)
    return payload


def _cache_write(cache_dir: Path | None, provider: str, url: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
    if cache_dir is None:
        return
    directory = cache_dir / provider
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_cache_key(provider, url, params)}.json"
    wrapper = {
        "cache_schema_version": "1.0",
        "provider": provider,
        "endpoint": url,
        "parameters_sha256": hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "response": payload,
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _snapshot_pages(snapshot_dir: Path, provider: str, query: str) -> list[dict[str, Any]]:
    path = snapshot_dir / f"{provider}.json"
    if not path.is_file():
        raise ProviderError("snapshot_missing", f"recorded snapshot is missing: {path}", provider=provider, exit_code=2)
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(wrapper, dict) or wrapper.get("provider") != provider or not isinstance(wrapper.get("responses"), list):
        raise ProviderError("invalid_snapshot", f"snapshot wrapper is invalid: {path}", provider=provider, exit_code=2)
    if _text(wrapper.get("query")) != _text(query):
        raise ProviderError("snapshot_query_mismatch", f"snapshot query does not match requested query for {provider}", provider=provider, exit_code=2)
    responses = wrapper["responses"]
    if not all(isinstance(item, dict) for item in responses):
        raise ProviderError("invalid_snapshot", f"snapshot responses are invalid: {path}", provider=provider, exit_code=2)
    return responses


def _write_snapshot(snapshot_dir: Path, provider: str, query: str, page_size: int, responses: list[dict[str, Any]]) -> None:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    wrapper = {
        "snapshot_schema_version": "1.0",
        "provider": provider,
        "query": query,
        "page_size": page_size,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "responses": responses,
    }
    path = snapshot_dir / f"{provider}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _request_page(
    provider: str,
    query: str,
    page: int,
    page_size: int,
    client: BoundedHttpClient,
    cache_dir: Path | None,
) -> dict[str, Any]:
    offset = (page - 1) * page_size
    exact_doi = _doi(query) if _text(query).casefold().startswith(("10.", "doi:", "https://doi.org/")) else ""
    if provider == "crossref":
        params = {"rows": page_size, "offset": offset}
        params["filter" if exact_doi else "query.bibliographic"] = f"doi:{exact_doi}" if exact_doi else query
        url = "https://api.crossref.org/works"
    elif provider == "openalex":
        params = {"per-page": page_size, "page": page}
        params["filter" if exact_doi else "search"] = f"doi:{exact_doi}" if exact_doi else query
        url = "https://api.openalex.org/works"
        mailto = _text(os.environ.get("ZJU_OPENALEX_MAILTO"))
        if mailto:
            params["mailto"] = mailto
    elif provider == "europepmc":
        provider_query = f'DOI:"{exact_doi}"' if exact_doi else query
        url, params = "https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"query": provider_query, "format": "json", "pageSize": page_size, "page": page, "resultType": "core"}
    elif provider == "pubmed":
        search_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
        provider_query = f'"{exact_doi}"[doi]' if exact_doi else query
        search_params = {"db": "pubmed", "term": provider_query, "retmax": page_size, "retstart": offset, "retmode": "json", "tool": "zju_research_skills"}
        esearch = _cache_read(cache_dir, provider, search_url, search_params)
        if esearch is None:
            esearch = client.get_json(provider, search_url, search_params)
            _cache_write(cache_dir, provider, search_url, search_params, esearch)
        try:
            ids = esearch["esearchresult"]["idlist"]
        except (KeyError, TypeError) as exc:
            raise ProviderError("invalid_provider_payload", "PubMed ESearch HTTP 200 payload lacks esearchresult.idlist", provider="pubmed") from exc
        if not isinstance(ids, list):
            raise ProviderError("invalid_provider_payload", "PubMed ESearch idlist is not an array", provider="pubmed")
        if not ids:
            return {"esearch": esearch, "esummary": {"result": {"uids": []}}}
        summary_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
        summary_params = {"db": "pubmed", "id": ",".join(map(str, ids)), "retmode": "json", "tool": "zju_research_skills"}
        esummary = _cache_read(cache_dir, provider, summary_url, summary_params)
        if esummary is None:
            esummary = client.get_json(provider, summary_url, summary_params)
            _cache_write(cache_dir, provider, summary_url, summary_params, esummary)
        return {"esearch": esearch, "esummary": esummary}
    else:
        raise ProviderError("unknown_provider", f"unsupported provider: {provider}", provider=provider, exit_code=2)
    cached = _cache_read(cache_dir, provider, url, params)
    if cached is not None:
        return cached
    payload = client.get_json(provider, url, params)
    _cache_write(cache_dir, provider, url, params, payload)
    return payload


def search(
    query: str,
    providers: list[str],
    *,
    page_size: int = 10,
    pages: int = 1,
    live: bool = False,
    snapshot_dir: Path | None = None,
    cache_dir: Path | None = None,
    record_snapshot_dir: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    query = _text(query)
    if not query:
        raise ProviderError("invalid_query", "query must not be empty", exit_code=2)
    if not 1 <= page_size <= MAX_PAGE_SIZE:
        raise ProviderError("invalid_page_size", f"page_size must be 1..{MAX_PAGE_SIZE}", exit_code=2)
    if not 1 <= pages <= MAX_PAGES:
        raise ProviderError("invalid_page_count", f"pages must be 1..{MAX_PAGES}", exit_code=2)
    unknown = sorted(set(providers) - set(PROVIDERS))
    if unknown:
        raise ProviderError("unknown_provider", f"unsupported providers: {', '.join(unknown)}", exit_code=2)
    if not providers:
        raise ProviderError("missing_provider", "select at least one provider", exit_code=2)
    if live and os.environ.get(LIVE_ENV) != "1":
        raise ProviderError("live_opt_in_required", f"set {LIVE_ENV}=1 as well as --live", exit_code=2)
    if record_snapshot_dir is not None and not live:
        raise ProviderError("snapshot_recording_requires_live", "--record-snapshot-dir is valid only with --live", exit_code=2)
    if not live and snapshot_dir is None:
        raise ProviderError("offline_source_required", "network is off; pass --snapshot-dir or explicitly opt in with --live", exit_code=2)

    client = BoundedHttpClient(transport=transport)
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    provider_pages: dict[str, int] = {}
    try:
        for provider in providers:
            parsed_pages = 0
            try:
                if live:
                    responses = [
                        {"page": page, "response": _request_page(provider, query, page, page_size, client, cache_dir)}
                        for page in range(1, pages + 1)
                    ]
                    if record_snapshot_dir is not None:
                        _write_snapshot(record_snapshot_dir, provider, query, page_size, responses)
                else:
                    assert snapshot_dir is not None
                    responses = _snapshot_pages(snapshot_dir, provider, query)
                    if len(responses) < pages:
                        raise ProviderError(
                            "snapshot_page_shortfall",
                            f"{provider} snapshot has {len(responses)} page(s), fewer than requested {pages}",
                            provider=provider,
                            exit_code=2,
                        )
                    responses = responses[:pages]
                for item in responses:
                    payload = item.get("response")
                    if not isinstance(payload, dict):
                        raise ProviderError("invalid_snapshot", f"{provider} page response is not an object", provider=provider, exit_code=2)
                    records.extend(PARSERS[provider](payload))
                    parsed_pages += 1
                provider_pages[provider] = parsed_pages
            except ProviderError as exc:
                failures.append(exc.as_dict())
    finally:
        client.close()

    return {
        "schema_version": SCHEMA_VERSION,
        "query": query,
        "providers": providers,
        "records": records,
        "failures": failures,
        "execution": {
            "mode": "live" if live else "recorded_snapshot",
            "network_enabled": live,
            "page_size": page_size,
            "pages_requested": pages,
            "pages_parsed_by_provider": provider_pages,
            "result_count": len(records),
            "partial": bool(failures),
            "cache_enabled": cache_dir is not None,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query")
    parser.add_argument("--providers", default=",".join(PROVIDERS), help="comma-separated provider names")
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--snapshot-dir", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--record-snapshot-dir", type=Path, help="write raw live responses for offline tests")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--print-schema", action="store_true")
    args = parser.parse_args()
    if args.print_schema:
        print(json.dumps(OUTPUT_SCHEMA, ensure_ascii=False, indent=2))
        return 0
    try:
        provider_list = [item.strip().casefold() for item in args.providers.split(",") if item.strip()]
        result = search(
            args.query or "", provider_list, page_size=args.page_size, pages=args.pages,
            live=args.live, snapshot_dir=args.snapshot_dir, cache_dir=args.cache_dir,
            record_snapshot_dir=args.record_snapshot_dir,
        )
        body = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(body, encoding="utf-8")
            print(json.dumps({"ok": True, "output": str(args.output), "records": len(result["records"]), "failures": len(result["failures"])}, ensure_ascii=False))
        else:
            print(body, end="")
        return 0 if not result["failures"] else 5
    except ProviderError as exc:
        print(json.dumps({"ok": False, "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except (OSError, json.JSONDecodeError) as exc:
        error = ProviderError("local_io_error", str(exc), exit_code=2)
        print(json.dumps({"ok": False, "error": error.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
