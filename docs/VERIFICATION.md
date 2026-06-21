# Packaged-extension verification (v0.4.1)

The release artifact `dist/spokeagent-0.4.1.brxt` was extracted to a clean
directory, built with `uv sync` (venv + console entrypoint OK), and driven through
the **real BioRouter CLI + XiaoMi MiMo** — confirming the packaged bundle (not the
dev tree) delivers the improvements.

- Tools exposed by the fresh install: `get_spoke_schema`, `resolve_entity`,
  `describe_node`, `find_path`, `query_spoke` (5/5).
- **V1** "genes associated with multiple sclerosis" → 23.8 s / 3 calls
  (schema → resolve_entity → query), correct (BTNL2, IRF5, …).
- **V2** "drugs that target EGFR" → correct, via
  `(:Compound)-[:BINDS_CbP]->(:Protein)` (no fictional `TARGETS_CtG`).
- **V3** "how are APOE and coronary artery disease connected" → used **`find_path`**,
  correct.
- **V4** "how many human proteins" → 22.9 s / 3 calls, **202,161 via
  `(:Organism)-[:ENCODES_OeP]->(:Protein)`** — i.e. structure-aware, avoiding the
  26 s Protein-by-organism scan.

Faster/efficient traversals, correct identifier resolution, accurate multi-hop
answers, one-call connectivity, and schema/perf robustness all reproduced from the
installed `.brxt`.
