# SPOKEAgent

A **structure-aware** MCP (Model Context Protocol) server for querying the SPOKE
biomedical knowledge graph for rapid biomedical knowledge inference. Points to the
official release of SPOKE.

SPOKEAgent doesn't just expose raw Cypher — it understands SPOKE's structure. It
introspects the live schema (so it tolerates schema changes), resolves entity names /
synonyms / identifiers to canonical nodes via the graph's indexes, profiles a node's
real relationships, finds shortest paths between entities, and guards every query
against the pitfalls of a 43-million-node graph (case-sensitivity, expensive edges,
unbounded scans). See [`docs/CHANGELOG.md`](docs/CHANGELOG.md) and
[`docs/TEST_FINDINGS.md`](docs/TEST_FINDINGS.md) for the design rationale, validated
over 100 natural-language questions through BioRouter.

## BioRouter Extension

**[Download spokeagent.brxt](https://github.com/BaranziniLab/SPOKEAgent/releases/latest/download/spokeagent-0.4.1.brxt)**

Drag the `.brxt` file into BioRouter's **Extensions → Add extension** dialog. BioRouter will install the virtual environment automatically and prompt for required credentials.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SPOKEAGENT_PASSCODE` | ✅ | — | Passcode from the SPOKEAgent credentials page |

## Features

- **Compact, cached schema** — a curated node table + `Source-[:REL]->Target` edge
  directory with counts and cost flags, derived live from the database.
- **Entity resolution** — name / synonym / brand / identifier → canonical SPOKE
  node(s), case- and apostrophe-safe, across DOID / Entrez / Ensembl / DrugBank /
  UMLS / UBERON / GO, ranked by connectivity.
- **Node profiling** — a node's real relationship types, directions, and counts.
- **Path finding** — shortest connecting path(s) between two entities.
- **Guarded querying** — read-only Cypher with auto safety-LIMIT, transaction
  timeout, and trimmed output.

## Alternative install (custom extension via `uvx`)

If you prefer not to use the `.brxt` bundle, you can register SPOKEAgent as a custom extension command:

1. In BioRouter, go to **Add custom extension**

2. Fill in the extension name and description

3. For the command, use the following:

```bash
uvx --from git+https://github.com/BaranziniLab/SPOKEAgent spokeagent
```

4. Add an environment variable:

   a. Variable name = `SPOKEAGENT_PASSCODE`

   b. Value = `<your-passcode>` (from the credentials page)

   c. Click **+ Add** to add the variable.

5. Click **Add extension** — you're ready to go

## Available Tools

The recommended workflow is **schema once → `resolve_entity` → query / describe /
find_path**, passing string literals through `parameters`.

### 1. `get_spoke_schema(refresh=false)`

Returns a compact, cached map of the current graph: `node_labels` (by count), an
`edge_directory` of `{source, rel, target, count, expensive}`, and `usage_notes`
(identifier namespaces, edge properties, `vestige` filtering, performance rules).
Call once near the start of a task.

### 2. `resolve_entity(query, label?, limit?)`

Maps a free-text name, synonym, brand, or identifier to canonical node(s). Handles
case-sensitivity, apostrophes, and cross-vocabulary identifiers (DOID, Entrez,
Ensembl, DrugBank, UMLS CUI, UBERON, GO). Returns ranked candidates
`{label, name, identifier, matched_on, score, degree}`. Use it **before** querying.

### 3. `describe_node(query, label?)`

Returns a node's real relationship profile `{dir, rel, neighbor_label, count}` — to
pick the right edge, or to confirm (and report) that an expected edge is absent
instead of guessing more queries.

### 4. `find_path(source, target, source_label?, target_label?, max_hops?, max_paths?)`

Resolves both endpoints and returns the shortest connecting path(s) as node +
relationship-type sequences — the right tool for "how are X and Y connected".

### 5. `query_spoke(cypher_query, parameters?)`

Execute a read-only Cypher query. Behaviour built in: writes rejected; a safety
`LIMIT` auto-applied to unbounded non-aggregate queries; a transaction timeout;
trimmed, size-capped output; coaching metadata on empty/limited results.

**Example** (resolve first, then query by the resolved value via `parameters`):

```cypher
MATCH (d:Disease {name: $name})-[:ASSOCIATES_DaG]->(g:Gene)
RETURN g.name AS gene, g.identifier AS entrez
LIMIT 10
```
`parameters = {"name": "Alzheimer's disease"}`. Note `ASSOCIATES_DaG` carries
`diseases_scores`/`gwas_pvalue` (not a `score` property), and drug→gene targets go
`(:Compound)-[:BINDS_CbP]->(:Protein)<-[:ENCODES_GeP]-(:Gene)` — there is no
`TARGETS_CtG` edge.

## Security

This server enforces read-only access to the SPOKE knowledge graph. Write operations (CREATE, MERGE, DELETE, etc.) are not permitted.

## License

MIT

## Authors

- Wanjun Gu ([wanjun.gu@ucsf.edu](mailto:wanjun.gu@ucsf.edu))

- Gianmarco Bellucci ([gianmarco.bellucci@ucsf.edu](mailto:gianmarco.bellucci@ucsf.edu))

## Editors

- Ilan Ladabaum ([ilan.ladabaum@ucsf.edu](mailto:ilan.ladabaum@ucsf.edu))

## About SPOKE

SPOKE (Scalable Precision medicine Oriented Knowledge Engine) is a large-scale biomedical knowledge graph that integrates data from multiple sources to support precision medicine research.
