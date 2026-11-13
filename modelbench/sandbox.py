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
from typing import Any, Dict, List, Optional


_HARNESS = r'''
import copy, io, json, os, sys, threading, traceback

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
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raw = sys.stdin.buffer.read().decode("utf-8")
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

    if spec.get("concurrency_test"):
        import ast
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in ("Lock", "RLock", "Semaphore", "BoundedSemaphore"):
                    _emit({"ok": False, "error": f"security_violation: forbidden lock used ({node.attr}), must use CAS DB primitives"})
                    return
                if isinstance(node, ast.ImportFrom) and node.module == "threading":
                    for alias in node.names:
                        if alias.name in ("Lock", "RLock", "Semaphore", "BoundedSemaphore"):
                            _emit({"ok": False, "error": f"security_violation: forbidden lock imported ({alias.name}), must use CAS DB primitives"})
                            return
        except Exception as exc:
            _emit({"ok": False, "error": f"AST parse error: {exc}"})
            return

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

    if spec.get("concurrency_test"):
        import concurrent.futures, time
        class MockAccountDB:
            def __init__(self, bal=1000, ver=0):
                self._bal = bal
                self._ver = ver
                self._l = threading.Lock()
            def get(self, uid):
                with self._l:
                    res = {"balance": self._bal, "version": self._ver}
                time.sleep(0.0005)
                return res
            def compare_and_set(self, uid, exp_ver, new_bal, new_ver):
                with self._l:
                    if self._ver == exp_ver:
                        self._bal = new_bal
                        self._ver = new_ver
                        return True
                    return False

        db = MockAccountDB(1000, 0)
        t0 = time.time()
        success_cnt = 0
        fail_cnt = 0
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
                futs = [pool.submit(fn, db, 1, 15, 10) for _ in range(100)]
                for fut in concurrent.futures.as_completed(futs):
                    res = fut.result()
                    if res is True:
                        success_cnt += 1
                    else:
                        fail_cnt += 1
            elapsed = time.time() - t0
            if elapsed > 2.0:
                results = [{"ok": False, "error": f"concurrency_timeout: elapsed={elapsed:.2f}s > 2.0s"}]
            else:
                results = [{"ok": True, "value": {
                    "success_count": success_cnt,
                    "fail_count": fail_cnt,
                    "final_balance": db._bal,
                    "final_version": db._ver,
                }}]
        except Exception as exc:
            results = [{"ok": False, "error": f"concurrency_exec_error: {type(exc).__name__}: {exc}"}]
        _emit({"ok": True, "results": results})
        return

    if spec.get("unicode_weakref_test"):
        import gc, weakref
        cls = ns.get(fn_name)
        if not isinstance(cls, type):
            _emit({"ok": False, "error": f"'{fn_name}' must be a class, got {type(cls).__name__}"})
            return

        try:
            router = cls()
            pool = list(spec.get("unicode_cf_pool") or ["\u200b", "\u2060", "\ufeff", "\u200e"])
            cf = (pool * 8)[:8]
            received = []

            class Handler:
                def __init__(self, tag):
                    self.tag = tag

                def __call__(self, payload):
                    received.append((self.tag, payload))

            # NOTE: callables are plain objects (no closures), so a strong reference held by
            # the router really is a leak and shows up in the weakref probe below.
            h_main = Handler("main")
            h_emoji, h_emoji_plain = Handler("emoji"), Handler("emoji_plain")
            h_zwnj, h_zwnj_plain = Handler("zwnj"), Handler("zwnj_plain")

            # --- (a) random Cf injection: two differently-polluted spellings must collapse ---
            topic_sub = "orders" + cf[0] + "_event" + cf[1]
            topic_pub = "orders" + cf[2] + "_event" + cf[3]
            router.subscribe(topic_sub, h_main)
            router.publish(topic_pub, {"order_id": 999})
            if [(t, p) for t, p in received if t == "main"] != [("main", {"order_id": 999})]:
                _emit({"ok": False, "error": f"cf_unicode_clean_failed: Cf pool {pool} not normalised (handler not invoked or mismatch)"})
                return

            # --- (a2) bound-method subscribers must survive (WeakMethod, not weakref.ref) ---
            class Svc:
                def __init__(self):
                    self.hits = 0

                def on_msg(self, payload):
                    self.hits += 1

            svc = Svc()
            router.subscribe("svc_feed" + cf[6], svc.on_msg)
            router.publish("svc_feed" + cf[7], {"n": 1})
            if svc.hits != 1:
                _emit({"ok": False, "error": "bound_method_lost: a bound method subscriber was garbage collected before dispatch (use weakref.WeakMethod)"})
                return

            # --- (b) ZWJ/ZWNJ must survive: they are Cf but semantically load-bearing ---
            emoji_topic = "alert\U0001F468\u200d\U0001F469\u200d\U0001F467"
            zwnj_topic = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
            # A blanket "strip every Cf" implementation collapses these pairs, so subscribe the
            # ZWJ/ZWNJ-free spelling too: if the two collide, ZWJ/ZWNJ were wrongly removed.
            router.subscribe(emoji_topic, h_emoji)
            router.subscribe(emoji_topic.replace("\u200d", ""), h_emoji_plain)
            router.subscribe(zwnj_topic, h_zwnj)
            router.subscribe(zwnj_topic.replace("\u200c", ""), h_zwnj_plain)

            mark = len(received)
            router.publish(emoji_topic + cf[4], {"kind": "emoji"})
            router.publish(zwnj_topic + cf[5], {"kind": "zwnj"})
            tags = [t for t, _ in received[mark:]]
            if "emoji" not in tags:
                _emit({"ok": False, "error": "zwj_stripped: U+200D inside an emoji sequence must be preserved"})
                return
            if "emoji_plain" in tags:
                _emit({"ok": False, "error": "zwj_stripped: ZWJ-bearing and ZWJ-free topics must not collide (U+200D was removed)"})
                return
            if "zwnj" not in tags:
                _emit({"ok": False, "error": "zwnj_stripped: U+200C inside a Persian word must be preserved"})
                return
            if "zwnj_plain" in tags:
                _emit({"ok": False, "error": "zwnj_stripped: ZWNJ-bearing and ZWNJ-free topics must not collide (U+200C was removed)"})
                return

            # --- (c) weakref leak probe: the router must not keep the subscriber alive ---
            sentinel = weakref.ref(h_main)
            del h_main
            for _ in range(5):
                gc.collect()
            if sentinel() is not None:
                _emit({"ok": False, "error": "memory_leak: handler instance was not garbage collected (strong reference retained)"})
                return

            # --- (d) expired references must be pruned silently ---
            try:
                router.publish(topic_pub, {"order_id": 1000})
            except Exception as exc:
                _emit({"ok": False, "error": f"post_gc_publish_error: dead weakref caused exception {exc}"})
                return

            _emit({"ok": True, "results": [{
                "ok": True,
                "value": {
                    "cf_clean_verified": True,
                    "zwj_preserved": True,
                    "zwnj_preserved": True,
                    "gc_sentinel_collected": True,
                    "safe_post_gc": True,
                }
            }]})
            return
        except Exception as exc:
            _emit({"ok": False, "error": f"unicode_weakref_test_error: {type(exc).__name__}: {exc}"})
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
    concurrency_test: bool = False,
    unicode_weakref_test: bool = False,
    unicode_cf_pool: Optional[List[str]] = None,
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
                "concurrency_test": concurrency_test,
                "unicode_weakref_test": unicode_weakref_test,
                "unicode_cf_pool": unicode_cf_pool or [],
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
