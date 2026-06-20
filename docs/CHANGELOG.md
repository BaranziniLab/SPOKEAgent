# SPOKE Agent — detailed change log

All changes are to the **extension** (`~/.config/biorouter/extensions/spokeagent/`),
tested through the real BioRouter CLI + MiMo (mimo-v2.5-pro). Pristine baseline
preserved in `spoke-work/extension-orig/`.

---

## v0.3.0 — fixes after Batch 1 (Q1–Q10)

### Files changed
- `src/spokeagent/server.py` — rewritten (2 tools → 3 tools; ~140 → ~430 lines)
- `skills/spoke-knowledge-graph/SKILL.md` — rewritten with correct, verified guidance
- `manifest.json` — `version` 0.2.0→0.3.0, `tools_count` 2→3

### server.py — exactly what changed

**1. `get_spoke_schema` — was a raw dump, now compact/curated/cached.**
- *Before:* returned `clean_schema(apoc.meta.schema())` — every one of 264 rel types
  and all properties, unordered, with ZIP/location junk (`PARTOF_LpL`) surfaced
  first. Tens of KB. Re-fetched on every call. Strongly correlated with the Q5
  crash (rc=-15, oversized response).
- *After:* a single fast `apoc.meta.stats()` call → `{node_labels:[{label,count}…]
  (sorted desc), edge_directory:[{source,rel,target,count,expensive}…] (sorted desc,
  >1M flagged), usage_notes:[…]}`. Source/target reconstructed by regex-parsing the
  `(:X)-[:R]->()` / `()-[:R]->(:Y)` pattern keys. **11.7 KB, 0.13 s, cached in
  memory** (`refresh=true` to force re-read). Measured: shows `ASSOCIATES_DaG =
  Disease→Gene` directly so the model stops guessing edge names. (Fixes P1, P5, P6, P8.)

**2. NEW tool `resolve_entity(query, label?, limit?)` — canonical-node resolver.**
- Strategy stack, all index-backed (never scans the 43M graph):
  - identifier lookups, label inferred from prefix (DOID:/UBERON:/GO:/CL:/CHEBI:/
    inchikey:/FOODON:/Reactome/WP/SNOMED_), UMLS `C#####`→SideEffect, MeSH
    `D######`→Symptom, Entrez integer ids → Gene/Organism, `ENSG…`→Gene.ensembl,
    `DB#####`→Compound.xrefs, numeric→Disease.omim_list.
  - exact case-sensitive name (range index) + full-text phrase query on
    `<Label>NamesAndIds` / global `anyNamesAndIds` (case-insensitive, synonym-aware),
    with case-insensitive-exact matches prioritised.
- Returns ranked `{label, name, identifier, matched_on, score}`. Measured: warfarin→
  **Warfarin** (the rich DrugBank node, not the empty lowercase one), "Parkinson's
  disease"→DOID:14330, DOID:9352→type 2 diabetes, ENSG00000130203→APOE, DB00619→
  Imatinib, EGFR→Gene 1956 — all in 0.08–3 s. (Fixes P2, P3; pre-empts Batch 6.)

**3. `query_spoke` — hardened.**
- *Safety LIMIT:* `_maybe_add_limit` appends `LIMIT 200` to unbounded, non-aggregate
  read queries (skips ones with LIMIT/SKIP/CALL or count/collect/sum/avg/min/max),
  returning a `meta.note`. Stops accidental full-graph scans. (Fixes P7.)
- *Transaction timeout:* all reads now run in `session.begin_transaction(timeout=45)`
  so pathological queries abort instead of hanging the agent; timeout maps to a clear
  "anchor + avoid expensive edges" message.
- *Output trimming:* `_trim` recursively drops noise props (`Linkout` HTML, `license`)
  and truncates >600-char strings; total payload capped at 60 KB with a `meta.truncated`
  note. (Fixes P6, P8.)
- *Empty-result coaching:* 0 rows → `meta.empty` telling the model to re-`resolve_entity`
  or check edge direction, instead of thrashing.
- Write-guard, read-only behaviour and the obfuscated-credential bootstrap are unchanged.

### SKILL.md — what changed
Rewrote around the **resolve-first golden workflow**, a **correct edge cheat-sheet**
(removed the fictitious `TARGETS_CtG` and `r.score`; added the real
`Compound-BINDS_CbP->Protein<-ENCODES_GeP-Gene` drug-target pattern), identifier
namespaces, `parameters` usage for apostrophes/case, `vestige` filtering, the
"never scan Protein by organism" rule, expensive-edge anchoring, and a worked example.

### Batch-1 baseline to beat
mean ≈ 66 s, ≈ 10 tool calls/question, 1 crash (Q5), repeated case/apostrophe
failures, wrong-edge guesses. Re-run results recorded below.

### Addendum — `describe_node` (4th tool), still part of the Batch-1 fix set
Batch-1 v5 (after F1–F4) removed crashes and case/apostrophe failures but Q4/Q6/Q10
still **thrashed when the data was genuinely sparse** (Parkinson's has no PRESENTS_DpS;
Crohn's has no LOCALIZES_DlA), the agent trying many edge variations.

**Added `describe_node(query, label?)`** — resolves the node then returns its real
relationship profile `{dir, rel, neighbor_label, count}` (anchored, <0.2s). The agent
can see at a glance which edges exist and conclude an absence instead of guessing.
SKILL.md updated to call `describe_node` on 0-row results and for "how is X connected"
questions; `manifest.tools_count` → 4.

Validation (4-tool extension): Q6 **82.7 s/22 calls → 28.3 s/7 calls**, correctly
reporting "Crohn's has no LOCALIZES_DlA". Q4 still ~15 calls — MiMo elects to keep
exploring Parkinson's subtypes/general knowledge even after seeing no PRESENTS_DpS;
this is largely model behaviour, mitigated but not eliminated.

### Batch-1 net result (baseline → fixed)
- Crashes: 1 (Q5) → 0.
- Case/apostrophe failures: pervasive → eliminated (resolve_entity + parameters).
- Wrong/fictional edges (`TARGETS_CtG`, `r.score`): in skill → removed/corrected.
- Mean latency ~66 s → ~50 s (and far fewer *failed* calls); sparse-data thrash
  greatly reduced via describe_node. Schema response ~tens of KB → 11.7 KB cached.
