# SPOKE Agent test findings & fixes (per-batch)

Testing the REAL installed spokeagent extension through `biorouter run` + MiMo
(mimo-v2.5-pro), clean sandbox (`BIOROUTER_PATH_ROOT`), one extension attached.
Each batch: run 10 → diagnose → fix extension source → document → next batch.

## Baseline (original extension, before any fix)
Two tools: `get_spoke_schema` (dumps raw `apoc.meta.schema()`), `query_spoke`
(arbitrary read Cypher). Bundled skill has **incorrect** Cypher examples
(`TARGETS_CtG` edge does not exist; `r.score` is not a real property).

---

## Batch 1 (Q1–Q10) — baseline results
Per-question (elapsed s / tool calls / rc): Q1 62.9/7, Q2 129.1/15, Q3 62.5/9,
Q4 89.6/17, Q5 12.1/2 **CRASH rc=-15 no answer**, Q6 101.6/16, Q7 5.9/1,
Q8 34.6/6, Q9 55.2/7, Q10 108.1/23. Mean ≈ 66 s, ≈ 10 tool calls/question.

### Problems identified
- **P1 — `get_spoke_schema` is huge, unordered, and surfaces useless data first.**
  Raw `apoc.meta.schema()` dumps all 264 rel types + every property; the first
  thing the model sees is `PARTOF_LpL` ZIP-code/location junk. Agent calls it in
  8/10 questions, burns a slow call + thousands of tokens, and *still* writes
  wrong queries. Almost certainly the cause of **P8**.
- **P2 — Name case-sensitivity wrecks every lookup.** Exact `{name:…}` is
  case-sensitive. Agent tried `{name:'warfarin'}` (real: `Warfarin`),
  `{name:'Asthma'}` (real: `asthma`), `{name:"Type 2 diabetes"}`, etc. → empty
  results → 3–6 wasted calls per question rediscovering capitalization. Biggest
  single time sink.
- **P3 — Apostrophe quoting hell.** Q4 "Parkinson's disease": the agent spent
  **17 calls** fighting `'Parkinson''s disease'`, even trying `chr(110)` and
  regex. It never used the tool's `parameters` argument, which would make this
  trivial. Quoting of literals is a recurring failure.
- **P4 — Guessing nonexistent / wrongly-cased edges.** Q8 tried `:ParticipatesIn`
  (real: `PARTICIPATES_GpBP`) before checking. Agent guesses camelCase/generic
  names first.
- **P5 — No compact "which edge connects X to Y" directory.** All edge discovery
  is trial-and-error against the giant schema blob.
- **P6 — Verbose query results.** Nodes come back with an HTML `Linkout` field +
  `omim_list`/`mesh_list`/`url`/`license` noise → wasted tokens.
- **P7 — No anchoring/limit guard.** Q10 ran 23 queries; nothing stops an
  unanchored/over-broad query. (Perf risk on a 43M-node graph.)
- **P8 — Large schema response appears to crash a run.** Q5 = get_spoke_schema
  then process SIGTERM (rc=-15), no answer.

### Fixes applied after batch 1 (extension source) — see CHANGELOG_FIXES.md
- **F1 Compact, cached, curated `get_spoke_schema`** — node table by count + an
  edge directory `Source →REL→ Target (count)` derived live from
  `apoc.meta.stats()`, expensive (>1M) edges flagged, kept small. (P1,P5,P6,P8)
- **F2 New `resolve_entity` tool** — name/identifier → canonical node(s) via
  range + per-label/global fulltext indexes, server-side parameterized. Resolves
  case, apostrophes, synonyms, and cross-namespace ids (DOID/OMIM/DrugBank/
  Ensembl/UMLS/UBERON). (P2,P3, + batch-6 identifier cases)
- **F3 Hardened `query_spoke`** — strips `Linkout`/noise, caps output size,
  auto-applies a safety `LIMIT` to unbounded non-aggregate queries, friendlier
  empty-result hint pointing at `resolve_entity`. (P6,P7)
- **F4 Rewrote `SKILL.md`** — correct edge cheat-sheet, resolve-first workflow,
  `parameters` usage, identifier namespaces, `vestige` filtering, performance
  rules; removed the wrong `TARGETS_CtG` / `r.score` examples. (P2,P3,P4,P5,P7)

---

## Batch 2 (Q11–Q20: drug-target / repurposing) — with v0.3.0 (4-tool)
Per-Q (s/calls): Q11 40.8/8, Q12 52.0/10, Q13 26.9/7, Q14 65.6/12, Q15 83.0/18,
Q16 100.9/19, Q17 67.9/17, Q18 27.9/5, Q19 103.9/25, Q20 106.2/12. All rc=0, no
crashes, no timeouts.

### What worked (core fixes validated)
- **Drug-target traversal is correct**: Q11/Q16 used `(:Compound)-[:BINDS_CbP]->
  (:Protein)`, Q12 added `<-[:ENCODES_GeP]-(:Gene)`. No fictional `TARGETS_CtG`. The
  removed-edge fix from Batch 1 holds across the whole drug-target batch.
- resolve_entity used on every question; identifiers/case all clean.

### New problems
- **P9 — vocabulary gaps**: resolve_entity("beta blockers", PharmacologicClass)=[]
  (class is "Adrenergic beta-Antagonists"); agent thrashed (Q15, 18 calls). Strict
  full-text *phrase* match too narrow for common names.
- **P10 — edge-property name discovery**: Q16 guessed `r.affinity`/`r.kd`/`r.ic50`
  (don't exist), then `keys(r)` revealed `bindingdb_k`/`bindingdb_ic50s`; ~6 wasted
  calls. Edge props weren't surfaced anywhere.
- **P11 — candidate enumeration instead of one graph query**: Q19 resolved 11 NSAIDs
  one-by-one to find ibuprofen's shared targets, instead of a single co-occurrence
  query. Partly model strategy.

### Fixes applied (v0.3.1)
- **F5** resolve_entity **OR-token full-text fallback** when the strict phrase returns
  nothing → "beta blockers" now surfaces the Adrenergic beta-Antagonist classes.
- **F6** get_spoke_schema **curated edge-property notes** (BINDS_CbP affinities,
  ASSOCIATES_DaG scores/gwas_pvalue, regulation zscore/pvalue, TREATS phase, RESEMBLES
  stats) + "use keys(r) to discover others". (schema 11.7→12.2 KB, still cached/small)
- **F7** SKILL.md: shared/common-neighbour questions → one anchored co-occurrence
  query, not per-candidate resolution; edge-property discovery tip.

---

## Batch 3 (Q21–Q30: side effects / contraindications / trials / pharm-class) — v0.3.1
Per-Q (s/calls): Q21 56.4/10, Q22 17.6/4, Q23 42.8/8, Q24 65.2/17, Q25 16.9/3,
Q26 54.3/10, Q27 44.1/9, Q28 39.0/6, Q29 110.9/21, Q30 26.7/6. Mean 47.4 s / 9.4
calls. All rc=0. Correctness good (reverse side-effect lookups, marker genes,
treats-vs-trials distinction, food interactions all used the right edges).

### New problem
- **P12 — variant-node ambiguity**: Q29 "proteins that transport glucose" resolved
  "glucose" → a low-degree node (CHEBI:17234, degree 7) while the transport edges sit
  on a different, high-degree glucose node; agent thrashed across glucose variants (21
  calls). Same family as the Warfarin/warfarin case.

### Fix applied (v0.3.2)
- **F8** resolve_entity now returns a **`degree`** (relationship count) per candidate
  and ranks same-name candidates by degree, so the canonical, well-connected node wins
  (Warfarin deg 1100 > warfarin deg 7; Aspirin deg 695 first). SKILL.md: prefer the
  higher-degree variant when a traversal is empty. (Q24's prevalence thrash was a
  complex US-state aggregation — model strategy, not a tool gap.)
