#!/usr/bin/env python3
"""Build a safe subprocess environment for Codex CLI evaluation calls."""

from __future__ import annotations

import os
from typing import Mapping


PROXY_KEYS = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy")


def normalize_proxy(value: str) -> str | None:
    candidate = value.strip()
    if not candidate:
        return None
    if ";" in candidate or "=" in candidate:
        return None
    if "://" not in candidate:
        candidate = "http://" + candidate
    authority = candidate.split("://", 1)[1].split("/", 1)[0]
    if "@" in authority:
        return None
    return candidate


def parse_proxy_server(value: str) -> dict[str, str]:
    """Parse a WinINet ProxyServer value without accepting embedded credentials."""
    value = value.strip()
    if not value:
        return {}
    if ";" not in value and "=" not in value:
        proxy = normalize_proxy(value)
        return {"http": proxy, "https": proxy} if proxy else {}
    result: dict[str, str] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        scheme, raw = part.split("=", 1)
        scheme = scheme.strip().lower()
        if scheme not in {"http", "https"}:
            continue
        proxy = normalize_proxy(raw)
        if proxy:
            result[scheme] = proxy
    if "http" in result and "https" not in result:
        result["https"] = result["http"]
    return result


def _wininet_proxy() -> dict[str, str]:
    if os.name != "nt":
        return {}
    try:
        import winreg

        path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
        return parse_proxy_server(server) if enabled else {}
    except (OSError, ValueError, TypeError):
        return {}


def codex_subprocess_env(base: Mapping[str, str] | None = None) -> tuple[dict[str, str], str]:
    env = dict(os.environ if base is None else base)
    if any(env.get(key) for key in PROXY_KEYS):
        return env, "environment"
    proxies = _wininet_proxy()
    if not proxies:
        return env, "none"
    for scheme, value in proxies.items():
        env[f"{scheme.upper()}_PROXY"] = value
        env[f"{scheme.lower()}_proxy"] = value
    return env, "wininet"
