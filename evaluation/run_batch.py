#!/usr/bin/env python3
"""Run a batch of questions through the REAL biorouter+MiMo+spokeagent pipeline.

Usage: run_batch.py <batch_number> [extdir] [results_suffix]
Writes results/batchN[suffix].jsonl (one record per question) incrementally.
"""
import json, sys, pathlib, time
from run_q import run_question, DEFAULT_EXTDIR

HERE = pathlib.Path(__file__).resolve().parent
QS = json.load(open(HERE / "questions.json"))

def main():
    batch = int(sys.argv[1])
    extdir = sys.argv[2] if (len(sys.argv) > 2 and sys.argv[2]) else DEFAULT_EXTDIR
    suffix = sys.argv[3] if len(sys.argv) > 3 else ""
    qs = [q for q in QS if q["batch"] == batch]
    outdir = HERE / "results"; outdir.mkdir(exist_ok=True)
    outpath = outdir / f"batch{batch}{suffix}.jsonl"
    f = open(outpath, "w")
    for q in qs:
        print(f"[{time.strftime('%H:%M:%S')}] {q['id']} ...", flush=True)
        rec = run_question(q["id"], q["text"], extdir)
        rec["tests"] = q["tests"]
        f.write(json.dumps(rec, default=str) + "\n"); f.flush()
        status = "TIMEOUT" if rec["timed_out"] else f"{rec['n_tool_calls']} calls"
        print(f"    -> {rec['elapsed_s']}s, {status}, rc={rec['returncode']}", flush=True)
    f.close()
    print(f"WROTE {outpath}")

if __name__ == "__main__":
    main()
