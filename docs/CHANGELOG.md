# Changelog

All notable changes to SPOKEAgent. The 0.3.x–0.4.x line is a structure-aware
overhaul developed and validated by driving the **real BioRouter CLI + the XiaoMi
MiMo model + this extension** over a 100-question suite (10 themed batches). Each
fix below is tied to a problem observed in those runs; see
[`docs/TEST_FINDINGS.md`](TEST_FINDINGS.md) for the per-batch problem→fix log and
[`evaluation/`](../evaluation) for the questions, harness, and raw results.

## [0.4.1]
### Fixed
- `resolve_entity` now also does an **identifier-exact match within the given
  label** (indexed). Essential for identifier-keyed nodes that have no `name` and
  no full-text index — e.g. `MiRNA`, whose identifier *is* the query
  (`hsa-miR-21-5p`). (Batch 10 / Q92.)

## [0.4.0]
### Added
- **`find_path`** — a 5th tool. Resolves two endpoints and runs a bounded, anchored
  `allShortestPaths` (≤5 hops, deduped), returning each path as an ordered list of
  nodes and the relationship types between them. Answers "how are X and Y connected
  / shortest path / what links X to Y" in one call instead of many hand-written
  `shortestPath`/multi-hop probes. (Batch 8; e.g. aspirin→colorectal cancer
  157 s/29 calls → 24 s/6 calls.)

## [0.3.3]
### Fixed
- `resolve_entity`/`describe_node` normalise a `label` of the literal string
  `"None"`/`"null"`/`"any"`/`""` to "no label" (models sometimes pass these),
  instead of building a nonexistent `<None>NamesAndIds` index. (Batch 4 / Q31.)

## [0.3.2]
### Added
- `resolve_entity` returns a **`degree`** (relationship count) per candidate and
  ranks same-name variants by connectivity, so the canonical, well-connected node
  wins (e.g. `Warfarin` deg 1100 over `warfarin` deg 7). (Batch 3 / Q29.)

## [0.3.1]
### Added
- `resolve_entity` **OR-token full-text fallback** when the strict phrase match
  returns nothing, so common names resolve (e.g. "beta blockers" →
  "Adrenergic beta-Antagonists"). (Batch 2 / Q15.)
- `get_spoke_schema` now lists **key edge properties** (e.g. `BINDS_CbP` →
  `bindingdb_k`/`bindingdb_ic50s`; `ASSOCIATES_DaG` → `diseases_scores`/`gwas_pvalue`;
  regulation edges → `zscore`/`pvalue`) plus a "use `keys(r)`" tip. (Batch 2 / Q16.)
### Changed
- `SKILL.md`: route shared/common-neighbour questions to a single anchored
  co-occurrence query rather than per-candidate resolution.

## [0.3.0] — structure-aware overhaul
### Added
- **`resolve_entity(query, label?, limit?)`** — maps a free-text name / synonym /
  brand / identifier to canonical node(s) using SPOKE's range + full-text indexes.
  Handles case-sensitivity, apostrophes, and the DOID / Entrez / Ensembl / DrugBank /
  UMLS CUI / UBERON / GO namespaces. Always fast, never scans the graph.
- **`describe_node(query, label?)`** — returns a node's real relationship profile
  (`{dir, rel, neighbor_label, count}`), so the agent picks the right edge and, when
  an expected edge is absent (e.g. Parkinson's has no `PRESENTS_DpS`), reports the
  absence instead of thrashing. (Crohn's localization 83 s/22 calls → 28 s/7.)
### Changed
- **`get_spoke_schema`** is now compact, curated, and cached: a node table by count
  + a `Source-[:REL]->Target` edge directory with counts and `>1M` "expensive" flags,
  plus usage notes (identifier namespaces, `vestige` filtering, performance rules).
  ~12 KB derived live from `apoc.meta.stats`, vs the previous tens-of-KB unordered
  `apoc.meta.schema` dump. Schema-change tolerant (everything is introspected).
- **`query_spoke`** hardened: a safety `LIMIT` is auto-applied to unbounded,
  non-aggregate queries; a per-query transaction timeout aborts blow-ups; output is
  trimmed (drops `Linkout`/`license` noise, truncates long strings, caps total size);
  empty/limited/truncated results carry coaching metadata. Read-only guard unchanged.
- **`SKILL.md`** rewritten around a resolve-first workflow with a *correct* edge
  cheat-sheet. Removed the fictional `(:Compound)-[:TARGETS_CtG]->(:Gene)` edge and
  the non-existent `r.score` property; documented the real drug→gene path
  `(:Compound)-[:BINDS_CbP]->(:Protein)<-[:ENCODES_GeP]-(:Gene)`, identifier
  namespaces, `parameters` usage, `vestige` filtering, and the "never scan Protein by
  organism" rule.

## [0.2.0] — baseline
- Two tools: `get_spoke_schema` (raw `apoc.meta.schema()`) and `query_spoke`
  (arbitrary read-only Cypher). Bundled `spoke-knowledge-graph` skill.
