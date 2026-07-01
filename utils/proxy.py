from __future__ import annotations

import base64
import json
import os
from typing import Any


SAU_PLAYWRIGHT_PROXY_ENV = "SAU_PLAYWRIGHT_PROXY"
SUPPORTED_PROXY_SCHEMES = ("http://", "https://", "socks5://", "socks5h://")


def normalize_playwright_proxy(
    proxy: dict[str, Any] | None = None,
    *,
    server: str = "",
    username: str = "",
    password: str = "",
) -> dict[str, str]:
    """整理成 Playwright 可直接接收的 context proxy 字典。"""
    source = dict(proxy or {})
    server_text = str(server or source.get("server") or "").strip()
    if not server_text:
        return {}
    if not server_text.lower().startswith(SUPPORTED_PROXY_SCHEMES):
        raise ValueError("代理 server 需要包含协议：http/https/socks5/socks5h")
    if server_text.lower().startswith("socks5h://"):
        # Playwright context proxy 只明确支持 socks5，外部输入可继续沿用 socks5h 语义。
        server_text = "socks5://" + server_text[len("socks5h://") :]

    result = {"server": server_text}
    username_text = str(username or source.get("username") or "").strip()
    password_text = str(password or source.get("password") or "").strip()
    if username_text:
        result["username"] = username_text
    if password_text:
        result["password"] = password_text
    return result


def encode_playwright_proxy(proxy: dict[str, Any] | None) -> str:
    normalized = normalize_playwright_proxy(proxy)
    if not normalized:
        return ""
    raw = json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def decode_playwright_proxy(value: str | None) -> dict[str, str]:
    text = str(value or "").strip()
    if not text:
        return {}
    for candidate in _decode_candidates(text):
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            return normalize_playwright_proxy(payload)
    return {}


def proxy_from_env(env: dict[str, str] | None = None) -> dict[str, str]:
    source = env if env is not None else os.environ
    return decode_playwright_proxy(source.get(SAU_PLAYWRIGHT_PROXY_ENV))


def proxy_from_args(args: Any) -> dict[str, str]:
    return normalize_playwright_proxy(
        server=str(getattr(args, "proxy_server", "") or ""),
        username=str(getattr(args, "proxy_username", "") or ""),
        password=str(getattr(args, "proxy_password", "") or ""),
    )


def activate_proxy_from_args(args: Any) -> dict[str, str]:
    proxy = proxy_from_args(args)
    if proxy:
        os.environ[SAU_PLAYWRIGHT_PROXY_ENV] = encode_playwright_proxy(proxy)
    return proxy


def merge_proxy_context_options(
    options: dict[str, Any],
    *,
    proxy: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    result = dict(options or {})
    resolved_proxy = normalize_playwright_proxy(proxy) if proxy else proxy_from_env(env)
    if resolved_proxy:
        result["proxy"] = resolved_proxy
    return result


def _decode_candidates(text: str) -> list[str]:
    candidates = [text]
    try:
        decoded = base64.b64decode(text).decode("utf-8")
    except Exception:
        decoded = ""
    if decoded:
        candidates.append(decoded)
    return candidates
