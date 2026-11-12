# -*- coding: utf-8 -*-
"""Concurrent benchmark runner with multi-turn mock tool loop and full JSONL persistence."""

import copy
import json
import os
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .bank import generate_suite
from .client import chat_completion
from .graders import GraderError, grade_item, grader_fingerprint


def _exec_mock_tool(mock_fs: Dict[str, Any], name: str, raw_args: str) -> str:
    try:
        args = json.loads(raw_args) if raw_args else {}
    except Exception:
        return json.dumps({"error": f"Invalid JSON arguments: {raw_args}"})

    if name == "list_dir":
        p = str(args.get("path", "")).rstrip("/") or "/"
        dirs = mock_fs.get("dirs") or {}
        if p in dirs:
            return json.dumps({"path": p, "entries": dirs[p]}, ensure_ascii=False)
        return json.dumps({"error": f"FileNotFoundError: directory {p} does not exist"})

    if name == "read_mock_file":
        p = str(args.get("path", ""))
        files = mock_fs.get("files") or {}
        if p in files:
            return json.dumps(files[p], ensure_ascii=False)
        return json.dumps({"error": f"FileNotFoundError: {p} does not exist"})

    if name == "query_metrics_page":
        cur = args.get("cursor") or ""
        pages = mock_fs.get("pages") or {}
        if cur in pages:
            return json.dumps(pages[cur], ensure_ascii=False)
        return json.dumps({"error": f"InvalidCursorError: unknown cursor {cur!r}"})

    if name == "query_db":
        table = str(args.get("table", ""))
        dbs = mock_fs.get("db") or {}
        if table in dbs:
            return json.dumps(dbs[table], ensure_ascii=False)
        return json.dumps({"error": f"TableNotFoundError: {table!r} does not exist"})

    if name == "write_mock_config":
        p = str(args.get("path", ""))
        key = str(args.get("key", ""))
        val = args.get("value")
        files = mock_fs.setdefault("files", {})
        if p not in files:
            return json.dumps({"written": False, "error": f"FileNotFoundError: {p}"})
        if mock_fs.get("fail_first_write"):
            mock_fs["fail_first_write"] = False
            return json.dumps({"written": False, "error": "LockBusyError: transient lock on config file, please retry write"})
        if isinstance(files[p], dict):
            files[p][key] = val
            return json.dumps({"written": True, "path": p, "key": key, "value": val}, ensure_ascii=False)
        return json.dumps({"written": False, "error": "TargetNotDictError"})

    return json.dumps({"error": f"UnknownToolError: {name}"})


def _run_single_trial(
    *,
    model: str,
    item: Dict[str, Any],
    rep: int,
    run_id: str,
    grader_ver: str,
    base_url: Optional[str],
    api_key: Optional[str],
    timeout_s: float,
    max_tokens: int,
) -> Dict[str, Any]:
    messages: List[Dict[str, Any]] = [{"role": "user", "content": item["prompt"]}]
    tools = item.get("tools")
    max_rounds = int(item.get("max_rounds", 1))
    mock_fs = copy.deepcopy(item.get("mock_fs")) if item.get("mock_fs") is not None else None

    ttft_list: List[float] = []
    total_s = 0.0
    reasoning_parts: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    reasoning_tokens = 0

    called_tools: List[str] = []
    calls_log: List[Dict[str, Any]] = []
    rounds_exceeded = False

    final_content = ""
    finish_reason = "stop"
    error_kind: Optional[str] = None
    error_msg: Optional[str] = None

    for rnd in range(max_rounds):
        res = chat_completion(
            model=model,
            messages=messages,
            base_url=base_url,
            api_key=api_key,
            tools=tools,
            temperature=0.0,
            max_tokens=max_tokens,
            timeout_s=timeout_s,
        )
        if not res["ok"]:
            error_kind = res["error_kind"] or "transport"
            error_msg = res["error"]
            finish_reason = "error"
            break

        if res["ttft_s"] is not None:
            ttft_list.append(res["ttft_s"])
        if res["total_s"] is not None:
            total_s += res["total_s"]
        if res["reasoning"]:
            reasoning_parts.append(res["reasoning"])

        u = res.get("usage") or {}
        prompt_tokens += int(u.get("prompt_tokens") or 0)
        completion_tokens += int(u.get("completion_tokens") or 0)
        cdet = u.get("completion_tokens_details") or {}
        reasoning_tokens += int(cdet.get("reasoning_tokens") or 0)

        finish_reason = res["finish_reason"]
        tcs = res.get("tool_calls") or []

        if tools and tcs:
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": res["content"] or None,
                "tool_calls": tcs,
            }
            messages.append(assistant_msg)
            for tc in tcs:
                fn = tc.get("function") or {}
                tname = fn.get("name") or ""
                targs = fn.get("arguments") or "{}"
                called_tools.append(tname)
                calls_log.append({"name": tname, "arguments": targs})
                tool_out = _exec_mock_tool(mock_fs or {}, tname, targs)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id") or f"call_{rnd}_{len(called_tools)}",
                        "content": tool_out,
                    }
                )
            if rnd == max_rounds - 1:
                rounds_exceeded = True
                error_kind = "tool_loop"
                error_msg = f"exceeded max_rounds={max_rounds}"
            continue

        final_content = res["content"]
        break

    # Classify non-completion faults (all counted as 0 in denominator!)
    if error_kind is None:
        if finish_reason == "length":
            error_kind = "truncated"
            error_msg = "finish_reason=length"
        elif not (final_content or "").strip():
            error_kind = "empty"
            error_msg = "empty_content"

    passed = 0
    reason = error_msg or ""
    if error_kind is None:
        trace = (
            {
                "called_tools": called_tools,
                "calls": calls_log,
                "rounds_exceeded": rounds_exceeded,
                "final_fs": mock_fs,
            }
            if tools
            else None
        )
        try:
            passed, reason = grade_item(item, final_content, tool_trace=trace)
        except GraderError as exc:
            error_kind = "grader_error"
            reason = str(exc)
            passed = 0

    full_reasoning = "\n".join(reasoning_parts)
    return {
        "run_id": run_id,
        "grader_version": grader_ver,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "model": model,
        "item_id": item["id"],
        "axis": item["axis"],
        "cat": item["cat"],
        "variant_group": item.get("variant_group"),
        "variant": item.get("variant", "spec"),
        "stress_group": item.get("stress_group"),
        "stress": item.get("stress", "clean"),
        "rep": rep,
        "passed": passed,
        "reason": reason,
        "error_kind": error_kind,
        "finish_reason": finish_reason,
        "ttft_s": round(ttft_list[0], 3) if ttft_list else None,
        "total_s": round(total_s, 3) if total_s > 0 else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "reasoning_tokens": reasoning_tokens,
        "content_chars": len(final_content or ""),
        "reasoning_chars": len(full_reasoning),
        "content": final_content,
        "reasoning": full_reasoning,
        "tool_calls": calls_log,
    }


def run_benchmark(
    *,
    models: List[str],
    reps: int = 3,
    seed: int = 20261112,
    profile: str = "quick",
    out_path: str = "eval_v2.jsonl",
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    workers: int = 4,
    timeout_s: float = 120.0,
    max_tokens: int = 8192,
    cats: Optional[List[str]] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> str:
    suite = generate_suite(seed=seed, profile=profile)
    if cats:
        cat_set = set(cats)
        suite = [it for it in suite if it["cat"] in cat_set]

    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)
    grader_ver = grader_fingerprint()

    jobs = []
    for m in models:
        for it in suite:
            for rep in range(1, reps + 1):
                jobs.append((m, it, rep))

    total_jobs = len(jobs)
    done = 0
    lock = threading.Lock()

    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    with open(out_path, "a", encoding="utf-8") as fout:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futs = {
                pool.submit(
                    _run_single_trial,
                    model=m,
                    item=it,
                    rep=rep,
                    run_id=run_id,
                    grader_ver=grader_ver,
                    base_url=base_url,
                    api_key=api_key,
                    timeout_s=timeout_s,
                    max_tokens=max_tokens,
                ): (m, it["id"], rep)
                for m, it, rep in jobs
            }
            for fut in as_completed(futs):
                rec = fut.result()
                with lock:
                    fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    fout.flush()
                    done += 1
                    if progress_cb:
                        status = "PASS" if rec["passed"] else f"FAIL({rec['error_kind'] or rec['reason'][:36]})"
                        progress_cb(f"[{done}/{total_jobs}] {rec['model']} | {rec['item_id']}#r{rec['rep']} -> {status} ({rec['total_s'] or 0:.1f}s)")
    return run_id
