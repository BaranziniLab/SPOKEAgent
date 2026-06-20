#!/usr/bin/env python3
"""Thin harness around the REAL biorouter CLI.

This does NOT reimplement the agent. It shells out to the actual `biorouter run`
binary with the MiMo provider and the real spokeagent MCP extension attached,
exactly as a user typing into BioRouter would, and records what the real agent
did (tool calls + Cypher, tool results, final answer, timing). Output format is
stream-json so we can see every tool request/response.

Usage: run_q.py <qid> "<question>" [extdir]
"""
import json, os, subprocess, sys, time, signal, pathlib

HERE = pathlib.Path(__file__).resolve().parent
BR = "/Users/wgu/Desktop/BioRouter/target/release/biorouter"
SANDBOX = HERE / "sandbox"
DEFAULT_EXTDIR = os.path.expanduser("~/.config/biorouter/extensions/spokeagent")
SEC = json.load(open("/tmp/br_secrets.json"))
PC = SEC["SPOKEAGENT_PASSCODE"]
MK = SEC["XIAOMI_MIMO_API_KEY"]
MODEL = os.environ.get("SPOKE_TEST_MODEL", "mimo-v2.5-pro")
TIMEOUT = int(os.environ.get("SPOKE_TEST_TIMEOUT", "360"))

def run_question(qid, question, extdir=DEFAULT_EXTDIR, _attempt=1):
    """Run one question. Biorouter occasionally fails to spawn the stdio
    extension ('process quit before initialization ... os error 2') — a flaky
    exec race on the host, unrelated to the extension. Detect that and retry so
    measurements reflect the extension's real behaviour, not the spawn race."""
    rec = _run_once(qid, question, extdir)
    startup_failed = ("Failed to start extension" in (rec.get("stderr_tail") or "")
                      and rec["n_tool_calls"] == 0)
    # Also retry clean early failures (transient MiMo/biorouter blips): non-zero
    # exit, no tool calls, no answer, and not our own timeout kill.
    transient = (not rec.get("timed_out") and rec.get("returncode", 0) not in (0, None)
                 and rec["n_tool_calls"] == 0 and not (rec.get("final_text") or "").strip())
    if (startup_failed or transient) and _attempt < 4:
        time.sleep(2)
        return run_question(qid, question, extdir, _attempt + 1)
    rec["attempts"] = _attempt
    return rec

def _run_once(qid, question, extdir=DEFAULT_EXTDIR):
    env = dict(os.environ)
    env["BIOROUTER_PATH_ROOT"] = str(SANDBOX)
    env["XIAOMI_MIMO_API_KEY"] = MK
    env["XIAOMI_MIMO_HOST"] = "https://token-plan-sgp.xiaomimimo.com/v1"
    # Invoke the extension's own venv entrypoint directly. This is what `uv run`
    # ends up executing, but without uv's per-spawn resolve step, which was
    # intermittently failing ("No such file or directory") under rapid sequential
    # spawns. The final packaged-extension verification still uses the real `uv`
    # install path. Falls back to `uv run` if the venv entrypoint is absent.
    entry = os.path.join(extdir, ".venv", "bin", "spokeagent")
    if os.path.exists(entry):
        ext = f"SPOKEAGENT_PASSCODE={PC} SPOKE_LOG_LEVEL=WARNING {entry}"
    else:
        ext = f"SPOKEAGENT_PASSCODE={PC} SPOKE_LOG_LEVEL=WARNING uv run --directory {extdir} spokeagent"
    cmd = [BR, "run", "-t", question,
           "--provider", "xiaomi_mimo", "--model", MODEL,
           "--with-extension", ext,
           "--no-session", "--output-format", "stream-json"]
    t0 = time.time()
    p = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True)
    timed_out = False
    try:
        out, err = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
        out, err = p.communicate()
    dt = time.time() - t0
    return parse(qid, question, out, err, dt, timed_out, p.returncode)

def parse(qid, question, out, err, dt, timed_out, rc):
    events = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append({"_raw": line[:500]})
    tool_calls, final_text, usage = [], [], None
    for ev in events:
        # stream-json events have varying shapes; walk content arrays
        msg = ev.get("message", ev)
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for c in content:
                if not isinstance(c, dict):
                    continue
                typ = c.get("type", "")
                if typ in ("toolRequest", "tool_use", "toolUse"):
                    tr = c.get("toolCall", c.get("tool_call", c))
                    val = tr.get("value", tr) if isinstance(tr, dict) else {}
                    name = val.get("name") or c.get("name")
                    args = val.get("arguments") or val.get("input") or c.get("input") or {}
                    tool_calls.append({"name": name, "args": args, "phase": "request"})
                elif typ in ("toolResponse", "tool_result", "toolResult"):
                    tr = c.get("toolResult", c.get("tool_result", c))
                    val = tr.get("value", tr) if isinstance(tr, dict) else {}
                    txt = _extract_text(val)
                    tool_calls.append({"phase": "response", "text": txt[:1500]})
                elif typ == "text" and isinstance(c.get("text"), str):
                    if msg.get("role") in (None, "assistant"):
                        final_text.append(c["text"])
        u = (ev.get("usage") or (msg.get("usage") if isinstance(msg, dict) else None))
        if u:
            usage = u
    reqs = [t for t in tool_calls if t.get("phase") == "request"]
    return {
        "qid": qid, "question": question, "elapsed_s": round(dt, 1),
        "timed_out": timed_out, "returncode": rc,
        "n_tool_calls": len(reqs),
        "tool_calls": tool_calls,
        "final_text": "\n".join(final_text).strip(),
        "usage": usage,
        "stderr_tail": err[-800:] if err else "",
        "n_events": len(events),
    }

def _extract_text(val):
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return " ".join(_extract_text(x) for x in val)
    if isinstance(val, dict):
        if "text" in val:
            return _extract_text(val["text"])
        if "content" in val:
            return _extract_text(val["content"])
        return json.dumps(val, default=str)[:1500]
    return str(val)

if __name__ == "__main__":
    qid = sys.argv[1]
    question = sys.argv[2]
    extdir = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_EXTDIR
    rec = run_question(qid, question, extdir)
    print(json.dumps(rec, indent=2, default=str))
