# SPOKEAgent evaluation harness

Test artifacts behind the structure-aware overhaul (v0.3.x → v0.4.x). Every result
here was produced by driving the **real BioRouter CLI + the XiaoMi MiMo model + the
real spokeagent MCP extension** — not a bespoke agent loop.

- `questions.json` — 100 natural-language questions across 10 themed batches
  (single-hop, drug-target, side-effects, anatomy/expression, pathways/GO,
  identifier resolution, multi-hop ranking, open-ended graph reasoning, performance
  stressors, schema robustness).
- `run_q.py` / `run_batch.py` — thin harness that shells out to `biorouter run`
  (MiMo provider, spokeagent attached via its venv entrypoint), captures each tool
  call / Cypher / timing / answer as stream-json, and retries transient spawn blips.
  Credentials (SPOKE passcode, MiMo key) are read at runtime from the local
  BioRouter keychain blob — **none are stored in this repo**.
- `grade.py` / `analyze.py` — per-batch efficiency + tool-usage summaries.
- `results/batchN.jsonl` — raw per-question records (tool calls, answers, timing).

See `../docs/TEST_FINDINGS.md` for the per-batch problem→fix log and
`../docs/CHANGELOG.md` for the change history.
