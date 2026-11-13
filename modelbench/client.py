# -*- coding: utf-8 -*-
"""Zero-dependency OpenAI-compatible streaming client with TTFT and reasoning telemetry."""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


def resolve_endpoint(base_url: Optional[str]) -> str:
    u = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
    if not u.endswith("/chat/completions"):
        u += "/chat/completions"
    return u


def resolve_api_key(api_key: Optional[str]) -> str:
    return (api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")).strip()


def _parse_sse_stream(resp, t0: float) -> Dict[str, Any]:
    ttft: Optional[float] = None
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls_acc: Dict[int, Dict[str, Any]] = {}
    finish_reason: Optional[str] = None
    usage: Dict[str, Any] = {}

    for raw in resp:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            obj = json.loads(payload)
        except Exception:
            continue

        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            usage = obj["usage"]

        choices = obj.get("choices") or []
        if not choices:
            continue
        ch = choices[0]
        if ch.get("finish_reason"):
            finish_reason = ch["finish_reason"]

        delta = ch.get("delta") or {}
        c = delta.get("content") or ""
        r = delta.get("reasoning_content") or delta.get("reasoning") or ""
        tcs = delta.get("tool_calls") or []

        if (c or r or tcs) and ttft is None:
            ttft = time.perf_counter() - t0
        if c:
            content_parts.append(c)
        if r:
            reasoning_parts.append(r)

        for tc in tcs:
            idx = int(tc.get("index", 0))
            entry = tool_calls_acc.setdefault(
                idx,
                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if tc.get("id"):
                entry["id"] = tc["id"]
            if tc.get("type"):
                entry["type"] = tc["type"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                entry["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                entry["function"]["arguments"] += fn["arguments"]

    total_s = time.perf_counter() - t0
    tool_calls = [tool_calls_acc[k] for k in sorted(tool_calls_acc)] if tool_calls_acc else []
    return {
        "ok": True,
        "content": "".join(content_parts),
        "reasoning": "".join(reasoning_parts),
        "tool_calls": tool_calls,
        "finish_reason": finish_reason or "stop",
        "ttft_s": round(ttft if ttft is not None else total_s, 3),
        "total_s": round(total_s, 3),
        "usage": usage,
        "error": None,
        "error_kind": None,
    }


def chat_completion(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.0,
    max_tokens: int = 4096,
    timeout_s: float = 120.0,
    max_retries: int = 2,
    extra_body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Call OpenAI-compatible /chat/completions via streaming SSE with clean per-attempt timing."""
    url = resolve_endpoint(base_url)
    key = resolve_api_key(api_key)

    body: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if extra_body:
        body.update(extra_body)

    raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"

    last_err: Optional[Tuple[str, str]] = None
    for attempt in range(max_retries + 1):
        req = urllib.request.Request(url, data=raw_body, headers=headers, method="POST")
        t0 = time.perf_counter()
        try:
            # Direct proxy-bypass for localhost endpoints, standard env proxy otherwise
            if "127.0.0.1" in url or "localhost" in url:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                resp_ctx = opener.open(req, timeout=timeout_s)
            else:
                resp_ctx = urllib.request.urlopen(req, timeout=timeout_s)

            with resp_ctx as resp:
                return _parse_sse_stream(resp, t0)

        except urllib.error.HTTPError as exc:
            elapsed = round(time.perf_counter() - t0, 3)
            err_body = ""
            try:
                err_body = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                pass
            kind = "api_error"
            low = err_body.lower()
            if exc.code in (400, 413, 422) and ("context" in low or "token" in low or "length" in low):
                kind = "context_overflow"
            last_err = (kind, f"HTTP {exc.code} ({elapsed}s): {err_body}")
            if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            break
        except Exception as exc:
            elapsed = round(time.perf_counter() - t0, 3)
            last_err = ("transport", f"{type(exc).__name__} ({elapsed}s): {exc}")
            if attempt < max_retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            break

    kind, msg = last_err or ("transport", "unknown request failure")
    return {
        "ok": False,
        "content": "",
        "reasoning": "",
        "tool_calls": [],
        "finish_reason": "error",
        "ttft_s": None,
        "total_s": None,
        "usage": {},
        "error": msg,
        "error_kind": kind,
    }
