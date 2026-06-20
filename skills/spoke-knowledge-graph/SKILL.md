---
name: spoke-knowledge-graph
description: Traverse the SPOKE biomedical knowledge graph to find relationships between diseases, genes, compounds, proteins, pathways, anatomy, side effects and other biological entities
---

Use this skill whenever the user wants to explore relationships between biomedical
entities — diseases, genes, drugs/compounds, proteins, pathways, anatomy, cell
types, side effects, symptoms, biological processes — in the **SPOKE** knowledge
graph (a 43M-node Neo4j graph queried with Cypher).

## The golden workflow (do this every time)

1. **Schema once.** Call `get_spoke_schema` a single time at the start. It returns
   a compact node table + an `edge_directory` of `Source →REL→ Target (count)` with
   cost flags. It is cached — do not call it again unless you suspect the schema
   changed (then pass `refresh=true`). Use the `edge_directory` to pick the exact
   relationship type that connects two entity types.

2. **Resolve names before querying.** NEVER hand-type a node name into a `MATCH`.
   Call `resolve_entity("<name or id>", label="<Type>")` first. Exact `{name: …}`
   matching in SPOKE is **case-sensitive** ("Warfarin" not "warfarin", "asthma" not
   "Asthma"), and names often contain apostrophes ("Parkinson's disease").
   `resolve_entity` returns the canonical `{label, name, identifier}` and also
   resolves synonyms/brand names and identifiers (DOID, Entrez, Ensembl, DrugBank,
   UMLS CUI, UBERON, GO). Pick the best candidate and say which one you chose.

3. **Query by the resolved value, using `parameters`.** Pass string literals through
   the `parameters` argument, never inline them:
   ```
   query_spoke(
     cypher_query="MATCH (d:Disease {name:$n})-[:ASSOCIATES_DaG]->(g:Gene) RETURN g.name AS gene LIMIT 20",
     parameters={"n": "multiple sclerosis"}
   )
   ```
   This eliminates case/quoting errors entirely. Matching by `identifier` (e.g.
   `{identifier:$id}`) is equally good — but note **Gene.identifier is an integer**
   (Entrez); match genes by their `name` (HGNC symbol) instead.
   Each candidate includes a `degree` (its number of relationships). When an entity
   has several variant nodes (e.g. "glucose" deg 7 vs the canonical high-degree node),
   prefer the higher-degree one — especially if a traversal on your first pick is empty.

4. **Interpret + surface assumptions.** Explain the biological meaning, and state
   which node you resolved to (name + identifier), which relationship/direction you
   traversed, and any limitation (e.g. "SPOKE has no LOCALIZES edge for this disease").

**When a query returns 0 rows, or for "how is X connected / what is near X"
questions, call `describe_node`.** It lists the relationship types a node actually
has (with direction, neighbour label, and count). If the edge you expected isn't
there (e.g. Parkinson's disease has no `PRESENTS_DpS`, Crohn's has no
`LOCALIZES_DlA`), report the absence immediately — do **not** keep trying query
variations. It is also the fastest way to scope an open-ended exploration.

## Edge cheat-sheet (verify against the live edge_directory)

| Question | Pattern |
|---|---|
| genes associated with a disease | `(:Disease)-[:ASSOCIATES_DaG]->(:Gene)` |
| drugs that treat a disease | `(:Compound)-[:TREATS_CtD]->(:Disease)` (edge has `phase`,`purpose`) |
| drugs contraindicated in a disease | `(:Compound)-[:CONTRAINDICATES_CcD]->(:Disease)` |
| drugs in clinical trials for a disease | `(:Compound)-[:IN_CLINICAL_TRIALS_FOR_CictD]->(:Disease)` |
| **drugs that target a gene** | `(:Compound)-[:BINDS_CbP]->(:Protein)<-[:ENCODES_GeP]-(:Gene)` — there is **NO** `TARGETS_CtG` edge |
| side effects of a drug | `(:Compound)-[:CAUSES_CcSE]->(:SideEffect)` |
| symptoms of a disease | `(:Disease)-[:PRESENTS_DpS]->(:Symptom)` |
| genes/symptoms | `(:Gene)-[:ASSOCIATES_GaS]->(:Symptom)` |
| disease localised to anatomy | `(:Disease)-[:LOCALIZES_DlA]->(:Anatomy)` |
| genes expressed in anatomy | `(:Anatomy)-[:EXPRESSES_AeG]->(:Gene)` |
| gene → pathway / process / function | `[:PARTICIPATES_GpPW]` / `[:PARTICIPATES_GpBP]` / `[:PARTICIPATES_GpMF]` |
| gene → protein | `(:Gene)-[:ENCODES_GeP]->(:Protein)` |
| compound up/down-regulates gene | `[:UPREGULATES_CuG]` / `[:DOWNREGULATES_CdG]` (have `zscore`,`pvalue`) |
| disease resembles / is-a disease | `[:RESEMBLES_DrD]` / `[:ISA_DiD]` |
| pharmacologic class → compounds | `(:PharmacologicClass)-[:INCLUDES_PCiC]->(:Compound)` |
| miRNA → gene | `(:MiRNA)-[:TARGETS_MtG]->(:Gene)` |

## Identifier namespaces

Disease = DOID (also `omim_list`, `mesh_list`); Gene = Entrez integer `identifier`,
HGNC symbol `name`, `ensembl`; Compound = `inchikey:`/`CHEBI:` identifier, DrugBank/
ChEMBL/PubChem in `xrefs`; Protein = UniProt; SideEffect = UMLS CUI; Symptom = MeSH;
Anatomy = UBERON; BiologicalProcess/MolecularFunction/CellularComponent = GO.
`resolve_entity` understands all of these — feed it the id directly.

## Performance & correctness rules

- **Always anchor** on a resolved node and **always traverse from it**. The graph
  has 43M nodes; an unanchored `MATCH` over Protein/Compound or an expensive edge
  (flagged in the schema, >1M: `INTERACTS_PiC`, `PARTOF_PDpP`, `ENCODES_OeP`,
  `INTERACTS_PiP`, `TARGETS_MtG`) can time out.
- **Never filter Protein by organism** (`org_ncbi_id`) — it is unindexed over 39M
  nodes (≈26 s). Reach human proteins via `(:Gene)-[:ENCODES_GeP]->(:Protein)`.
- `query_spoke` auto-applies a safety `LIMIT` to unbounded non-aggregate queries and
  enforces a transaction timeout, but you should still add explicit `LIMIT`/filters.
- **Filter deprecated nodes**: many Pathway (and some other) nodes have
  `vestige = true`. Add `WHERE NOT coalesce(p.vestige, false)` for pathways.
- For "shortest path / how connected" questions use bounded paths, e.g.
  `MATCH p = shortestPath((a)-[*..4]-(b))` with both ends anchored by identifier.
- If a query returns 0 rows, don't thrash: re-`resolve_entity` the names, confirm the
  edge type/direction in the `edge_directory`, then retry once.
- For **"how are X and Y connected / shortest path / what links X to Y"** questions,
  use the **`find_path`** tool (not hand-written `shortestPath` queries). It resolves
  both endpoints and returns the shortest path(s) as node + relationship sequences in
  one call — read the mechanism straight off the result. Increase `max_hops` only if
  no path is found. Don't keep probing individual multi-hop patterns by hand.
- For **"shared / common / same-as" questions** (e.g. "other drugs that bind the same
  target", "genes shared by two diseases"), write ONE anchored graph query with a
  co-occurrence pattern — do **not** resolve and test candidate entities one-by-one:
  `MATCH (a {name:$a})-[:BINDS_CbP]->(p:Protein)<-[:BINDS_CbP]-(b:Compound) WHERE b<>a RETURN DISTINCT b.name LIMIT 10`.
- Edge properties are often needed for ranking (binding affinity, GWAS p-value,
  regulation z-score). The schema `usage_notes` lists the important ones; to confirm
  an edge's properties run `MATCH ()-[r:REL]->() WITH r LIMIT 1 RETURN keys(r)` once.

## Worked example

> "What drugs target the protein encoded by EGFR?"

1. `get_spoke_schema` → confirm `BINDS_CbP` (Compound→Protein) and `ENCODES_GeP` (Gene→Protein).
2. `resolve_entity("EGFR", label="Gene")` → `{name:"EGFR", identifier:1956}`.
3. `query_spoke("MATCH (g:Gene {name:$g})-[:ENCODES_GeP]->(p:Protein)<-[:BINDS_CbP]-(c:Compound) RETURN DISTINCT c.name AS drug LIMIT 25", {"g":"EGFR"})`.
4. Report the drugs and note you traversed Gene→Protein→Compound (binding), anchored on EGFR.
