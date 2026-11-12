# -*- coding: utf-8 -*-
"""Isolated subprocess sandbox for evaluating generated Python code fixes.

The child process ONLY receives serialized test inputs and returns actual outputs/states.
Expected values never enter the child process, preventing stdout/exit-code spoofing.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List


_HARNESS = r'''
import copy, io, json, os, sys, traceback

def _install_audit_hook():
    allowed_prefixes = tuple(
        os.path.realpath(p).lower()
        for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix)
        if p
    )
    blocked_exact = {
        "os.system", "os.posix_spawn", "os.fork", "pty.spawn",
        "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace",
        "os.mkdir", "os.makedirs", "os.chmod", "os.chown",
    }
    blocked_prefixes = (
        "socket.", "urllib.", "http.client.", "ftplib.", "poplib.", "imaplib.",
        "smtplib.", "telnetlib.", "webbrowser.", "subprocess.", "os.spawn",
        "os.exec", "ctypes.", "winreg.", "shutil.",
    )

    def _hook(event, args):
        if event in blocked_exact or event.startswith(blocked_prefixes):
            raise PermissionError(f"sandbox_blocked:{event}")
        if event == "open" and args:
            target = args[0]
            if isinstance(target, int):
                return
            mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
            if any(ch in mode for ch in ("w", "a", "x", "+")):
                raise PermissionError(f"sandbox_blocked_write:{target}")
            try:
                rp = os.path.realpath(os.fsdecode(target)).lower()
            except Exception:
                raise PermissionError("sandbox_blocked_open")
            if not any(rp.startswith(pref) for pref in allowed_prefixes):
                raise PermissionError(f"sandbox_blocked_read:{target}")

    sys.addaudithook(_hook)

def _main():
    raw = sys.stdin.read()
    spec = json.loads(raw)
    code = spec["code"]
    fn_name = spec["fn_name"]
    cases = spec["cases"]
    inplace = bool(spec.get("inplace", False))
    check_no_mutate = bool(spec.get("check_no_mutate", False))

    real_stdout = sys.stdout
    sys.stdout = io.StringIO()

    def _emit(obj):
        real_stdout.write(json.dumps(obj, ensure_ascii=False))
        real_stdout.flush()

    _install_audit_hook()

    ns = {}
    try:
        exec(compile(code, "<candidate>", "exec"), ns, ns)
    except Exception as exc:
        _emit({"ok": False, "error": f"compile/exec error: {type(exc).__name__}: {exc}"})
        return

    fn = ns.get(fn_name)
    if not callable(fn):
        _emit({"ok": False, "error": f"function '{fn_name}' not found or not callable"})
        return

    results = []
    for case in cases:
        args = copy.deepcopy(case["args"])
        kwargs = copy.deepcopy(case.get("kwargs") or {})
        args_snap = copy.deepcopy(args) if check_no_mutate else None
        try:
            ret = fn(*args, **kwargs)
            if check_no_mutate and args != args_snap:
                results.append({"ok": False, "error": "input_mutated: caller arguments were modified in place"})
            else:
                val = args[0] if inplace else ret
                results.append({"ok": True, "value": val})
        except Exception as exc:
            results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    _emit({"ok": True, "results": results})

if __name__ == "__main__":
    _main()
'''


def _find_python_exe() -> str:
    exe = sys.executable
    base = os.path.basename(exe).lower()
    if base.startswith("python"):
        return exe
    cand = os.path.join(os.path.dirname(exe), "python.exe" if os.name == "nt" else "python3")
    if os.path.isfile(cand):
        return cand
    return exe


def _clean_env() -> Dict[str, str]:
    env: Dict[str, str] = {"PYTHONIOENCODING": "utf-8"}
    for key in ("PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG", "LC_ALL"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


def run_code_cases(
    *,
    code: str,
    fn_name: str,
    cases: List[Dict[str, Any]],
    inplace: bool = False,
    check_no_mutate: bool = False,
    timeout_s: float = 5.0,
) -> Dict[str, Any]:
    """Execute candidate Python code inside a fresh -I isolated subprocess in a temp directory."""
    if not code or not code.strip():
        return {"ok": False, "error": "empty_code", "results": []}

    tmpdir = tempfile.mkdtemp(prefix="mb2_sb_")
    try:
        payload = json.dumps(
            {
                "code": code,
                "fn_name": fn_name,
                "cases": [{"args": c["args"], "kwargs": c.get("kwargs") or {}} for c in cases],
                "inplace": inplace,
                "check_no_mutate": check_no_mutate,
            },
            ensure_ascii=False,
        )
        py = _find_python_exe()
        proc = subprocess.run(
            [py, "-I", "-c", _HARNESS],
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_clean_env(),
            cwd=tmpdir,
            timeout=timeout_s,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:300]
            return {"ok": False, "error": f"subprocess_exit_{proc.returncode}: {err}", "results": []}
        try:
            data = json.loads(proc.stdout)
        except Exception:
            return {"ok": False, "error": f"invalid_sandbox_output: {proc.stdout[:200]}", "results": []}
        if not data.get("ok"):
            return {"ok": False, "error": data.get("error", "exec_failed"), "results": []}
        results = data.get("results") or []
        if len(results) != len(cases):
            return {"ok": False, "error": "result_count_mismatch", "results": []}
        return {"ok": True, "error": None, "results": results}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout_expired", "results": []}
    except Exception as exc:
        return {"ok": False, "error": f"sandbox_error: {exc}", "results": []}
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
