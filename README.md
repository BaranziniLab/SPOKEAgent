# SPOKEAgent

Current version: **0.5.0**. Small queries stay on MCP; long queries and full
exports run as monitored local jobs without tying up an MCP request.

## Install in BioRouter

1. Download **[spokeagent.brxt](https://github.com/BaranziniLab/SPOKEAgent/releases/latest/download/spokeagent.brxt)**
   from [Releases](https://github.com/BaranziniLab/SPOKEAgent/releases/latest).
   The same current bundle is committed under [extensions/](extensions/).
2. In BioRouter, open **Extensions → Add extension**, select the BRXT and install.
   BioRouter creates the Python environment; `uv` and Python 3.11+ are required.
3. Enter credentials in BioRouter's own configuration dialog, never in chat.
4. Enable the extension in your chat. Verify a small query before a larger export.

Terminal installation uses the same installer:

```bash
biorouter extension install ./extensions/spokeagent.brxt
biorouter extension configure spokeagent
```

Configure `SPOKEAGENT_PASSCODE`. Alternatively provide `KNOWLEDGE_GRAPH_URI`, `KNOWLEDGE_GRAPH_USERNAME`, `KNOWLEDGE_GRAPH_PASSWORD`, and optionally `KNOWLEDGE_GRAPH_DATABASE` (default `neo4j`). Direct settings take precedence; use one route consistently. Because either route is valid, manifest fields are optional individually; startup validates that one complete route exists.

The Desktop installer discovers bundled `skills/*/SKILL.md`. If using a BioRouter
CLI version that does not copy bundled skills, the MCP server still provides the
job-routing instructions; the skill folders can also be installed separately.

## Long queries, CLI and progress

Use `spoke-submit_query_job` for an export or a query that could exceed the
interactive timeout. It returns a job ID immediately. Poll
`spoke-query_job_status` at its recommended interval; use
`spoke-cancel_query_job` to stop. Only `completed` means the file is complete.
Status includes rows, bytes, elapsed time, phase and an advisory ETA when known.
Set `mode="explain"` to obtain a plan without running the query.

From a source checkout, or BioRouter's installed extension directory:

```bash
uv sync --locked
# Standalone CLI only: configure its OS-keyring profile interactively once.
# BioRouter MCP jobs already receive credentials and do not need this command.
uv run spokeagent auth
uv run spokeagent submit --query-file query.cypher --format jsonl --timeout-seconds 3600
uv run spokeagent watch JOB_ID
```

No-argument `uv run spokeagent` continues to start the MCP server. `status`, `watch`,
`list`, `cancel` and `purge` need no database credentials. Results remain in the
local private job directory and are not sent to chat. Database/server limits can
still fail a query; jobs report those failures instead of silently truncating.

See [architecture, storage, security and release details](docs/QUERY_JOBS.md).
To rebuild the tracked bundle: `uv run python scripts/build_brxt.py`.


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
`{label, name, identifier, matched_on, score}` (degree may also be present). Use it **before** querying.

### 3. `describe_node(query, label?)`

Returns a node's real relationship profile `{dir, rel, neighbor_label, count}` — to
pick the right edge. If `truncated=true`, the profile contains only the top 60 groups;
do not conclude that an unlisted edge is absent.

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

A conservative query guard rejects writes and procedure calls in user-supplied
Cypher. Use a database principal with read-only permissions: the guard supplements
server authorization. Entity labels are validated before interpolation, and
resolver steps share a total timeout budget rather than accumulating long waits.

## License

Apache-2.0

## Authors

- Wanjun Gu ([wanjun.gu@ucsf.edu](mailto:wanjun.gu@ucsf.edu))

- Gianmarco Bellucci ([gianmarco.bellucci@ucsf.edu](mailto:gianmarco.bellucci@ucsf.edu))

## Editors

- Ilan Ladabaum ([ilan.ladabaum@ucsf.edu](mailto:ilan.ladabaum@ucsf.edu))

## About SPOKE

SPOKE (Scalable Precision medicine Oriented Knowledge Engine) is a large-scale biomedical knowledge graph that integrates data from multiple sources to support precision medicine research.
