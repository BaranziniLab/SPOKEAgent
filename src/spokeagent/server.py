"""
SPOKEAgent - SPOKE Knowledge Graph MCP Server

An MCP server for querying the SPOKE biomedical knowledge graph
for rapid biomedical knowledge inference.

This server is structure-aware: it introspects the live SPOKE schema (node
labels, relationship types and counts, indexes) at runtime, so it tolerates
schema changes (new/renamed labels or edges, added properties) without code
changes. Its discovery and query tools include:

  * get_spoke_schema  - compact, cached, curated schema (node table + a
                        Source->REL->Target edge directory with counts and
                        cost flags). Derived live from apoc.meta.stats.
  * resolve_entity    - turn a free-text name / synonym / identifier into the
                        canonical SPOKE node(s), using the range + full-text
                        indexes. Handles case, apostrophes, synonyms and the
                        DOID / Entrez / Ensembl / DrugBank / UMLS / UBERON / GO
                        identifier namespaces. ALWAYS use this before querying.
  * query_spoke       - run a read-only Cypher query (parameterised), with a
                        safety LIMIT, a transaction timeout, and trimmed output.
"""
import base64
import json
import logging
import os
import re
import sys
import time
from typing import Any, Literal, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools import ToolResult
from mcp.types import TextContent, ToolAnnotations
from neo4j import Driver, GraphDatabase, Result, Transaction
from neo4j.exceptions import AuthError, ClientError, Neo4jError, ServiceUnavailable, SessionExpired
from pydantic import BaseModel, Field

from spokeagent.jobs import INSTRUCTIONS as JOB_INSTRUCTIONS, register_job_tools

logger = logging.getLogger("SPOKEAgent")

# SPOKE configuration
from spokeagent.query_backend import from_environment

# --- query-handling constants -------------------------------------------------
DEFAULT_SAFETY_LIMIT = 200       # appended to unbounded, non-aggregate queries
QUERY_TIMEOUT_S = 45             # transaction timeout so blow-ups fail fast
MAX_RESULT_CHARS = 60000         # cap serialized result size (token / crash guard)
NOISE_PROP_KEYS = {"Linkout", "license", "vestige_url"}  # pure-noise node props
MAX_STR_LEN = 600                # truncate very long string property values

# Identifier-prefix -> candidate node labels (for resolve_entity). SPOKE uses
# community-standard vocabularies; these prefixes are stable across releases.
ID_PREFIX_LABELS = {
    "DOID:": ["Disease"],
    "UBERON:": ["Anatomy"],
    "GO:": ["BiologicalProcess", "MolecularFunction", "CellularComponent"],
    "CL:": ["CellType"],
    "CHEBI:": ["Compound"],
    "INCHIKEY:": ["Compound"],
    "FOODON:": ["Food"],
    "REACT": ["Pathway"],
    "R-HSA": ["Pathway"],
    "WP": ["Pathway"],
    "SNOMED_": ["SDoH"],
}

_WRITE_RE = re.compile(
    r"\b(MERGE|CREATE|SET|DELETE|REMOVE|ADD|INSERT|UPDATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|SP_)\b",
    re.IGNORECASE,
)
_AGG_RE = re.compile(r"\b(count|collect|sum|avg|min|max|stdev|percentile\w*)\s*\(", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\blimit\b", re.IGNORECASE)


class SPOKEConfig(BaseModel):
    """SPOKE knowledge graph configuration"""
    uri: str = Field(..., description="SPOKE knowledge graph connection URI")
    username: str = Field(..., description="SPOKE username")
    password: str = Field(..., description="SPOKE password")
    database: str = Field(..., description="SPOKE database name")
    log_level: str = Field("INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")


def _is_write_query(query: str) -> bool:
    """Check if the query contains write operations"""
    return _WRITE_RE.search(query) is not None


def _maybe_add_limit(query: str, limit: int = DEFAULT_SAFETY_LIMIT) -> tuple[str, bool]:
    """Append a safety LIMIT to unbounded, non-aggregate read queries.

    Leaves aggregations (count/collect/...), explicit LIMIT/CALL/SKIP queries,
    and anything that already looks bounded untouched. Returns (query, added)."""
    q = query.strip().rstrip(";").rstrip()
    ql = q.lower()
    if (_LIMIT_RE.search(ql) or _AGG_RE.search(ql) or ql.startswith("call")
            or "\nskip" in ql or " skip " in ql or "return" not in ql):
        return query, False
    return f"{q}\nLIMIT {limit}", True


def _safe_tool_error(error: Exception) -> ToolError:
    if isinstance(error, ToolError):
        return error
    logger.error("%s", type(error).__name__)
    code = getattr(error, "code", "") or ""
    if isinstance(error, AuthError) or ".Security." in code:
        return ToolError("SPOKE authentication or authorization failed. Check credentials and database permissions through the trusted configuration UI.")
    if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__ or "TimedOut" in code or "Timeout" in code:
        return ToolError("SPOKE query timed out. Narrow indexed filters or reduce path hops. For a legitimately long query use spoke-submit_query_job and monitor spoke-query_job_status.")
    if code == "Neo.ClientError.Procedure.ProcedureNotFound":
        return ToolError("SPOKE schema procedure unavailable. Check that the APOC plugin is installed and enabled.")
    if isinstance(error, (ServiceUnavailable, SessionExpired)):
        return ToolError("SPOKE connection unavailable. Check the database endpoint, network and service availability.")
    if ".Statement." in code or ".Schema." in code:
        return ToolError("SPOKE query or schema error. Check the query syntax, parameters and live schema before retrying.")
    return ToolError("SPOKE operation failed. Check database connectivity, configuration, query and permissions. Raw error details are withheld to protect data.")


def _remaining_budget(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ToolError("SPOKE tool time budget exhausted. Narrow the entity lookup or use a monitored query job.")
    return remaining


def _safe_label(label: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", label):
        raise ToolError("Invalid SPOKE label; use a label returned by get_spoke_schema.")
    return label


def _optional_lookup_error(error: ClientError) -> None:
    # Optional indexes and heterogeneous legacy properties may be unavailable.
    if error.code not in {
        "Neo.ClientError.Schema.IndexNotFound",
        "Neo.ClientError.Procedure.ProcedureNotFound",
        "Neo.ClientError.Statement.TypeError",
    }:
        raise error


def _trim(obj: Any) -> Any:
    """Recursively drop noisy node properties and truncate huge strings."""
    if isinstance(obj, dict):
        return {k: _trim(v) for k, v in obj.items() if k not in NOISE_PROP_KEYS}
    if isinstance(obj, list):
        return [_trim(v) for v in obj]
    if isinstance(obj, str) and len(obj) > MAX_STR_LEN:
        return obj[:MAX_STR_LEN] + "…(truncated)"
    return obj


def create_spoke_server(config: SPOKEConfig) -> FastMCP:
    """Create SPOKEAgent server with SPOKE knowledge graph tools"""

    logging.basicConfig(level=getattr(logging, config.log_level.upper()))
    mcp = FastMCP("SPOKEAgent", instructions=JOB_INSTRUCTIONS)

    # Knowledge graph driver initialization
    try:
        kg_driver = GraphDatabase.driver(config.uri, auth=(config.username, config.password))
        logger.info("SPOKE knowledge graph driver initialized")
    except Exception as e:
        raise _safe_tool_error(e) from None

    _schema_cache: dict[str, Any] = {}

    # ---- low-level helpers ---------------------------------------------------
    def _read(cypher: str, params: Optional[dict] = None, timeout: int = QUERY_TIMEOUT_S, max_rows: int | None = None):
        """Run a read query in a time-bounded read transaction; return rows."""
        with kg_driver.session(database=config.database, default_access_mode="READ") as session:
            with session.begin_transaction(timeout=timeout) as tx:
                result = tx.run(cypher, params or {})
                rows = [r.data() for r in (result.fetch(max_rows) if max_rows is not None else result)]
                tx.commit()
                return rows

    def _build_schema() -> dict:
        """Compact, curated schema derived live from apoc.meta.stats (fast)."""
        rec = _read(
            "CALL apoc.meta.stats() YIELD labels, relTypes, relTypesCount "
            "RETURN labels AS labels, relTypes AS relTypes, relTypesCount AS relTypesCount"
        )[0]
        labels, relTypes, relTypesCount = rec["labels"], rec["relTypes"], rec["relTypesCount"]

        node_table = [{"label": k, "count": v} for k, v in
                      sorted(labels.items(), key=lambda x: -x[1])]

        # reconstruct Source->Target per relationship type from the pattern keys
        pat_src = re.compile(r"^\(:(\w+)\)-\[:(\w+)\]->\(\)$")
        pat_tgt = re.compile(r"^\(\)-\[:(\w+)\]->\(:(\w+)\)$")
        src, tgt = {}, {}
        for key in relTypes:
            m = pat_src.match(key)
            if m:
                src[m.group(2)] = m.group(1)
            m = pat_tgt.match(key)
            if m:
                tgt[m.group(1)] = m.group(2)
        edges = []
        for rt, cnt in sorted(relTypesCount.items(), key=lambda x: -x[1]):
            edges.append({
                "source": src.get(rt, "?"),
                "rel": rt,
                "target": tgt.get(rt, "?"),
                "count": cnt,
                "expensive": cnt > 1_000_000,
            })

        return {
            "node_labels": node_table,
            "edge_directory": edges,
            "usage_notes": [
                "Edge codes are Hetionet-style VERB_AbC where A=source-label "
                "initial, b=verb, C=target-label initial (e.g. ASSOCIATES_DaG = "
                "Disease->Gene, BINDS_CbP = Compound->Protein).",
                "ALWAYS call resolve_entity first to map a name/identifier to a "
                "canonical node, then query by the returned exact name or identifier "
                "using the `parameters` argument (avoids case/apostrophe errors).",
                "Edges flagged expensive (>1,000,000) must always be traversed from "
                "an anchored, indexed node (a resolved name/identifier) - never scan "
                "them unanchored. Never filter Protein by organism property "
                "(no index, 39M nodes); reach proteins via Gene-[:ENCODES_GeP]->Protein.",
                "There is NO Compound->Gene 'TARGETS' edge. A drug's gene targets = "
                "(Compound)-[:BINDS_CbP]->(Protein)<-[:ENCODES_GeP]-(Gene).",
                "Identifier namespaces: Disease=DOID (+omim_list/mesh_list), "
                "Gene.identifier=Entrez INTEGER & Gene.name=HGNC symbol (match genes "
                "by name), Compound.identifier=inchikey:/CHEBI: (DrugBank/ChEMBL in "
                "xrefs), Protein=UniProt, SideEffect=UMLS CUI, Symptom=MeSH, "
                "Anatomy=UBERON, GO terms for BiologicalProcess/MolecularFunction/"
                "CellularComponent.",
                "Many Pathway nodes (and some others) are deprecated: filter "
                "WHERE NOT coalesce(n.vestige, false). Some list properties are "
                "stored as native arrays, others as strings - prefer name/identifier "
                "matching over property-list membership when unsure.",
                "Key edge properties (often NULL on a given edge): ASSOCIATES_DaG "
                "has diseases_scores / gwas_pvalue (NOT 'score'); TREATS_CtD has "
                "phase / purpose; BINDS_CbP has bindingdb_k / bindingdb_ic50s / "
                "chembl_action_type; UPREGULATES_*G / DOWNREGULATES_*G have zscore / "
                "pvalue; RESEMBLES_DrD has fisher / odds / enrichment; PREVALENCE_DpL "
                "has data_value / location_name. To discover an edge's real "
                "properties, run `MATCH ()-[r:REL]->() WITH r LIMIT 1 RETURN keys(r)`.",
            ],
        }

    def get_schema(force: bool = False) -> dict:
        if force or not _schema_cache:
            refreshed = _build_schema()
            _schema_cache.clear()
            _schema_cache.update(refreshed)
        return _schema_cache

    # ---- tools ---------------------------------------------------------------
    @mcp.tool(
        name="get_spoke_schema",
        annotations=ToolAnnotations(
            title="Get SPOKE Knowledge Graph Schema (compact)",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    def get_spoke_schema(refresh: bool = Field(
        default=False,
        description="Force a re-read of the live schema (otherwise a cached copy is returned)."
    )) -> ToolResult:
        """
        Return a COMPACT, curated map of the current SPOKE graph: node labels with
        counts and a Source->REL->Target edge directory with counts and cost flags.

        This is derived live from the database, so it reflects the real, current
        schema (robust to new/renamed labels or edges). It is small and cached -
        call it ONCE near the start of a task, then rely on resolve_entity +
        query_spoke. Use the edge_directory to pick the exact relationship type
        that connects two entity types before writing Cypher.
        """
        try:
            schema = get_schema(force=bool(refresh))
            return ToolResult(content=[TextContent(type="text", text=json.dumps(schema))])
        except Exception as error:
            raise _safe_tool_error(error) from None

    def _resolve_candidates(q: str, label: Optional[str], limit: int, deadline: float | None = None) -> list[dict]:
        """Shared resolver used by resolve_entity and describe_node. Returns a
        ranked list of {label, name, identifier, matched_on, score}."""
        # Models sometimes pass the literal strings "None"/"null"/"any" for "no
        # label"; treat those as unset rather than a (nonexistent) label.
        if label is not None and str(label).strip().lower() in ("", "none", "null", "any", "all"):
            label = None
        if label is not None:
            label = _safe_label(label)
        if not q:
            raise ToolError("Entity query must not be empty.")
        limit = max(1, min(int(limit), 25))
        deadline = deadline if deadline is not None else time.monotonic() + 35

        def lookup(cypher, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ToolError("Entity resolution exceeded its time budget. Specify a label and exact identifier.")
            return _read(cypher, params, timeout=min(QUERY_TIMEOUT_S, remaining))

        results: list[dict] = []
        seen: set = set()

        def add(rows, matched_on):
            for row in rows:
                if label and row.get("l") != label:
                    continue
                key = (row.get("l"), str(row.get("id")), row.get("name"))
                if key in seen:
                    continue
                seen.add(key)
                results.append({
                    "label": row.get("l"),
                    "name": row.get("name"),
                    "identifier": row.get("id"),
                    "matched_on": matched_on,
                    "score": round(row["score"], 2) if row.get("score") is not None else None,
                })

        looks_like_id = (bool(re.search(r"[:_]", q))
                         or bool(re.match(r"^(ENSG\d|DB\d|WP\d|C\d{5,}|D\d{5,})", q, re.I))
                         or q.isdigit())

        # ---- identifier strategies (each index-backed / cheap) ---------------
        if looks_like_id:
            up = q.upper()
            id_labels: list[str] = []
            for pref, labs in ID_PREFIX_LABELS.items():
                if up.startswith(pref):
                    id_labels = labs
                    break
            if label:
                id_labels = [label]
            if not id_labels and re.match(r"^C\d{5,}$", up):      # UMLS CUI
                id_labels = ["SideEffect"]
            if not id_labels and re.match(r"^D\d{6}$", up):       # MeSH
                id_labels = ["Symptom"]
            if not id_labels:                                     # generic indexed fallback
                id_labels = ["Disease", "Gene", "Compound", "Anatomy", "Pathway",
                             "SideEffect", "Symptom", "BiologicalProcess", "Protein"]
            for lab in id_labels:
                try:
                    add(lookup(
                        f"MATCH (n:{lab}) WHERE n.identifier = $q "
                        "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                        {"q": q, "lim": limit}), f"identifier:{lab}")
                except ClientError as error:
                    _optional_lookup_error(error)
            if q.isdigit() and label in (None, "Gene", "Organism"):  # Entrez integer ids
                for lab in ([label] if label else ["Gene", "Organism"]):
                    try:
                        add(lookup(
                            f"MATCH (n:{lab}) WHERE n.identifier = $qi "
                            "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                            {"qi": int(q), "lim": limit}), f"entrez:{lab}")
                    except ClientError as error:
                        _optional_lookup_error(error)
            if label in (None, "Gene") and re.match(r"^ENSG\d+", up):                         # Ensembl gene id
                try:
                    add(lookup("MATCH (g:Gene) WHERE g.ensembl = $q "
                              "RETURN 'Gene' AS l, g.name AS name, g.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "ensembl")
                except ClientError as error:
                    _optional_lookup_error(error)
            if label in (None, "Compound") and re.match(r"^DB\d+$", up):                          # DrugBank xref (unindexed)
                try:
                    add(lookup("MATCH (c:Compound) WHERE any(x IN c.xrefs WHERE x ENDS WITH $q) "
                              "RETURN 'Compound' AS l, c.name AS name, c.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "xref:drugbank")
                except ClientError as error:
                    _optional_lookup_error(error)
            if q.isdigit() and label in (None, "Disease"):          # OMIM on Disease.omim_list
                try:
                    add(lookup("MATCH (d:Disease) WHERE $q IN d.omim_list "
                              "RETURN 'Disease' AS l, d.name AS name, d.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "omim")
                except ClientError as error:
                    _optional_lookup_error(error)

        # ---- name strategies -------------------------------------------------
        if label:                                                # exact, case-sensitive (index)
            try:
                add(lookup(f"MATCH (n:{label}) WHERE n.name = $q "
                          "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                          {"q": q, "lim": limit}), "name:exact")
            except ClientError as error:
                _optional_lookup_error(error)
            # Also try identifier-exact within the label (indexed). Essential for
            # nodes keyed by identifier with no `name`/full-text index, e.g. MiRNA
            # whose identifier IS the query (hsa-miR-21-5p).
            try:
                add(lookup(f"MATCH (n:{label}) WHERE n.identifier = $q "
                          "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                          {"q": q, "lim": limit}), "identifier:exact")
            except ClientError as error:
                _optional_lookup_error(error)
        # full-text phrase (case-insensitive, includes synonyms); prioritise ci-exact
        idx = (label + "NamesAndIds") if label else "anyNamesAndIds"
        phrase = '"' + q.replace('"', " ") + '"'
        try:
            ft = lookup(
                "CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score "
                "RETURN labels(node)[0] AS l, node.name AS name, node.identifier AS id, score "
                "LIMIT 25", {"idx": idx, "q": phrase})
            ci_exact = [r for r in ft if (r.get("name") or "").lower() == q.lower()]
            add(ci_exact, "name:exact-ci")
            add(ft, "name:fulltext")
        except ClientError as error:
            _optional_lookup_error(error)

        # Fallback: if the strict phrase matched nothing, retry full-text with the
        # bare tokens (OR semantics). Catches common names that don't appear verbatim
        # (e.g. "beta blockers" -> "Adrenergic beta-Antagonists").
        if not results and len(q) >= 3 and not looks_like_id:
            try:
                loose = lookup(
                    "CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score "
                    "RETURN labels(node)[0] AS l, node.name AS name, node.identifier AS id, score "
                    "LIMIT 15", {"idx": idx, "q": q})
                add(loose, "name:fulltext-loose")
            except ClientError as error:
                _optional_lookup_error(error)

        def exact_rank(c):
            mo = c.get("matched_on", "")
            return 0 if ("exact" in mo or mo.startswith(("identifier", "entrez", "ensembl",
                         "xref", "omim"))) else 1

        results.sort(key=lambda c: (exact_rank(c), -(c.get("score") or 0)))

        # Annotate the top candidates with node degree so the caller can pick the
        # canonical, well-connected node when an entity has several variant nodes
        # (e.g. "glucose" deg 7 vs "D-Glucose" deg ~52000). Degree is index-cheap.
        top = results[: max(limit, 8)]
        if len(top) > 1:
            for c in top:
                lab = c.get("label")
                if not lab:
                    continue
                lab = _safe_label(lab)
                anchor = "n.identifier = $v" if c.get("identifier") is not None else "n.name = $v"
                val = c.get("identifier") if c.get("identifier") is not None else c.get("name")
                # f-string: {lab} interpolates, {{ }} become literal braces for COUNT{...}
                cy = f"MATCH (n:{lab}) WHERE {anchor} RETURN COUNT{{(n)--()}} AS d LIMIT 1"
                try:
                    d = lookup(cy, {"v": val})
                    c["degree"] = d[0]["d"] if d else None
                except ClientError as error:
                    _optional_lookup_error(error)
                    c["degree"] = None
            # re-rank: exact matches first, then most-connected, then full-text score
            top.sort(key=lambda c: (exact_rank(c), -(c.get("degree") or 0), -(c.get("score") or 0)))
            return top[:limit]
        return results[:limit]

    @mcp.tool(
        name="resolve_entity",
        annotations=ToolAnnotations(
            title="Resolve a name/identifier to canonical SPOKE node(s)",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    def resolve_entity(
        query: str = Field(..., description="Free-text name, synonym, or identifier to resolve "
                           "(e.g. 'multiple sclerosis', \"Parkinson's disease\", 'Tylenol', "
                           "'EGFR', 'DOID:9352', 'ENSG00000130203', 'DB00619')."),
        label: Optional[str] = Field(default=None, description="Optional node label to restrict to "
                           "(e.g. 'Disease', 'Gene', 'Compound', 'Anatomy', 'SideEffect'). "
                           "Strongly recommended when you know the entity type - it is faster and "
                           "more accurate."),
        limit: int = Field(default=8, description="Max candidates to return."),
    ) -> ToolResult:
        """
        Map a free-text name, synonym, or identifier to the canonical SPOKE node(s).

        Use this BEFORE query_spoke. It handles the things that make naive queries
        fail: case-sensitivity (exact {name:...} is case-sensitive), apostrophes,
        synonyms/brand names, and cross-vocabulary identifiers (DOID, Entrez,
        Ensembl, DrugBank, UMLS CUI, UBERON, GO). It uses SPOKE's range and
        full-text indexes where available. DrugBank/OMIM cross-reference lookups
        can scan their label; specify a label to narrow the search.

        Returns ranked candidates: {label, name, identifier, matched_on, score}.
        Then query by the returned exact `name` or `identifier` via the
        `parameters` argument of query_spoke. If several candidates look plausible,
        state which one you picked and why.
        """
        q = (query or "").strip()
        if not q:
            raise ToolError("resolve_entity: empty query")
        try:
            results = _resolve_candidates(q, label, limit)
            if not results:
                return ToolResult(content=[TextContent(type="text", text=json.dumps({
                    "query": q, "label": label, "candidates": [],
                    "hint": "No match. Try without a label, a shorter/alternate spelling or a "
                            "known synonym, or call get_spoke_schema to confirm the label exists.",
                }))])
            return ToolResult(content=[TextContent(type="text", text=json.dumps({
                "query": q, "label": label, "candidates": results}))])
        except Exception as error:
            raise _safe_tool_error(error) from None

    @mcp.tool(
        name="describe_node",
        annotations=ToolAnnotations(
            title="Describe a SPOKE node's actual relationships (degree profile)",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    def describe_node(
        query: str = Field(..., description="Name or identifier of the node to profile "
                           "(e.g. \"Parkinson's disease\", 'TP53', 'DOID:8778')."),
        label: Optional[str] = Field(default=None, description="Optional node label to disambiguate "
                           "(e.g. 'Disease', 'Gene', 'Compound')."),
    ) -> ToolResult:
        """
        Show what a node is ACTUALLY connected to: its relationship types, the
        neighbour label on the other end, the direction, and the count for each.

        Use this to (a) decide which relationship to traverse for a question, and
        (b) avoid thrashing - if a node has no edge of the type you expected (e.g.
        a disease with no PRESENTS_DpS symptoms, or no LOCALIZES_DlA anatomy), this
        helps establish graph coverage. Check the returned truncation flag before
        concluding a relationship is absent. Also ideal for open-ended "how is X connected / what is near X"
        questions. The node is resolved first (handles case / apostrophes / ids).

        Returns {node:{label,name,identifier}, relationships:[{dir, rel, neighbor_label, count}]}.
        """
        q = (query or "").strip()
        if not q:
            raise ToolError("describe_node: empty query")
        try:
            deadline = time.monotonic() + QUERY_TIMEOUT_S
            cands = _resolve_candidates(q, label, 1, deadline)
            if not cands:
                return ToolResult(content=[TextContent(type="text", text=json.dumps({
                    "query": q, "label": label, "node": None,
                    "hint": "Could not resolve the node; check spelling or call resolve_entity."}))])
            node = cands[0]
            lab = _safe_label(node["label"])
            anchor = "n.identifier = $id" if node.get("identifier") is not None else "n.name = $nm"
            params = {"id": node.get("identifier"), "nm": node.get("name")}
            rels = _read(
                f"MATCH (n:{lab}) WHERE {anchor} WITH n LIMIT 1 "
                "MATCH (n)-[r]-(m) "
                "RETURN type(r) AS rel, labels(m)[0] AS neighbor_label, "
                "CASE WHEN startNode(r)=n THEN '->' ELSE '<-' END AS dir, count(*) AS count "
                "ORDER BY count DESC LIMIT 61",
                params, timeout=_remaining_budget(deadline))
            out = {
                "node": {"label": lab, "name": node.get("name"), "identifier": node.get("identifier")},
                "relationships": rels[:60],
                "truncated": len(rels) > 60,
                "note": ("Top 60 relationship groups shown; omitted groups may exist. Query a specific "
                         "relationship type to check absence." if len(rels) > 60 else
                         "These are the relationship groups on the resolved node. Absence here is "
                         "absence in this graph, not evidence of biological absence."),
            }
            return ToolResult(content=[TextContent(type="text", text=json.dumps(out, default=str))])
        except Exception as error:
            raise _safe_tool_error(error) from None

    @mcp.tool(
        name="find_path",
        annotations=ToolAnnotations(
            title="Find shortest path(s) between two SPOKE nodes",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    def find_path(
        source: str = Field(..., description="Source entity name or identifier (e.g. 'APOE', "
                            "'aspirin', 'DOID:9256')."),
        target: str = Field(..., description="Target entity name or identifier."),
        source_label: Optional[str] = Field(default=None, description="Optional label for the source "
                            "(e.g. 'Gene', 'Compound', 'Disease')."),
        target_label: Optional[str] = Field(default=None, description="Optional label for the target."),
        max_hops: int = Field(default=4, description="Maximum path length to search (1-5; clamped)."),
        max_paths: int = Field(default=5, description="Maximum number of shortest paths to return."),
    ) -> ToolResult:
        """
        Find the shortest connecting path(s) between two entities in SPOKE - the right
        tool for "how are X and Y connected / what links X to Y / shortest path" and
        subgraph-bridge questions. Both endpoints are resolved first (case / apostrophe
        / id safe), then a bounded bidirectional allShortestPaths search runs (anchored,
        but high-degree endpoints can still make expansion expensive). Returns each path as an ordered list
        of nodes and the relationship types between them - so you can read off the
        intermediate nodes and mechanism in ONE call instead of probing many queries.

        If no path is found within max_hops, that is reported (try a larger max_hops, or
        the entities are only distantly connected). Returns
        {source, target, max_hops, paths:[{hops, nodes:[...], rels:[...]}]}.
        """
        mh = max(1, min(int(max_hops or 4), 5))
        kpaths = max(1, min(int(max_paths or 5), 15))
        try:
            deadline = time.monotonic() + QUERY_TIMEOUT_S
            sc = _resolve_candidates((source or "").strip(), source_label, 1, deadline)
            tc = _resolve_candidates((target or "").strip(), target_label, 1, deadline)
            if not sc or not tc:
                return ToolResult(content=[TextContent(type="text", text=json.dumps({
                    "source": source, "target": target,
                    "error": "Could not resolve " + ("source" if not sc else "target") +
                             "; call resolve_entity to find the right node."}))])
            s, t = sc[0], tc[0]
            _safe_label(s["label"])
            _safe_label(t["label"])

            def anchor(node, var):
                if node.get("identifier") is not None:
                    return f"{var}.identifier = ${var}id", {f"{var}id": node["identifier"]}
                return f"{var}.name = ${var}nm", {f"{var}nm": node.get("name")}

            sa, sp = anchor(s, "a")
            ta, tp = anchor(t, "b")
            params = {**sp, **tp}
            cy = (f"MATCH (a:{s['label']}),(b:{t['label']}) WHERE {sa} AND {ta} "
                  "WITH a, b LIMIT 1 "
                  f"MATCH path = allShortestPaths((a)-[*..{mh}]-(b)) "
                  f"WITH path LIMIT {kpaths} "
                  "RETURN [n IN nodes(path) | labels(n)[0] + ':' + coalesce(n.name, toString(n.identifier))] AS nodes, "
                  "[r IN relationships(path) | type(r)] AS rels, length(path) AS hops")
            rows = _read(cy, params, timeout=_remaining_budget(deadline))
            # dedupe identical (nodes, rels) sequences (parallel edges produce repeats)
            seen, paths = set(), []
            for r in rows:
                key = (tuple(r["nodes"]), tuple(r["rels"]))
                if key in seen:
                    continue
                seen.add(key)
                paths.append({"hops": r["hops"], "nodes": r["nodes"], "rels": r["rels"]})
            out = {
                "source": {"label": s["label"], "name": s.get("name"), "identifier": s.get("identifier")},
                "target": {"label": t["label"], "name": t.get("name"), "identifier": t.get("identifier")},
                "max_hops": mh,
                "paths": paths,
            }
            if not paths:
                out["note"] = (f"No path within {mh} hops between the resolved nodes. Try a larger "
                               "max_hops, or they may be only distantly/indirectly connected.")
            return ToolResult(content=[TextContent(type="text", text=json.dumps(out, default=str))])
        except Exception as error:
            raise _safe_tool_error(error) from None

    @mcp.tool(
        name="query_spoke",
        annotations=ToolAnnotations(
            title="Query SPOKE Biomedical Knowledge Graph",
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
        ),
    )
    def query_spoke(
        cypher_query: str = Field(..., description="A read-only Cypher query. Anchor it on a node "
                           "resolved via resolve_entity (match by exact name or identifier) and "
                           "pass string literals through `parameters` rather than inlining them "
                           "(this avoids case and apostrophe errors, e.g. \"Parkinson's disease\")."),
        parameters: dict[str, Any] = Field(default_factory=dict,
                           description="Query parameters, e.g. {\"name\": \"Parkinson's disease\"} "
                           "used as $name in the query. Strongly preferred over inlining literals."),
    ) -> ToolResult:
        """
        Execute a read-only Cypher query on SPOKE for biomedical knowledge inference.

        Behaviour built in for you:
          * Only read queries are allowed (writes are rejected).
          * An unbounded, non-aggregate query gets a safety LIMIT appended so it
            bounds returned rows; it does not bound scans, sorting or aggregation.
            Aggregations and queries with your own LIMIT are left as-is.
          * A transaction timeout aborts pathological queries instead of hanging.
          * Output is trimmed (noisy HTML/link fields removed, long strings cut)
            and capped in size to stay efficient.

        Tips: resolve names first with resolve_entity; use the edge_directory from
        get_spoke_schema to choose relationship types; pass literals via parameters.
        """
        from .jobs import validate_query
        try:
            validate_query(cypher_query, "cypher")
        except ValueError as error:
            raise ToolError(str(error)) from None

        effective, added_limit = _maybe_add_limit(cypher_query)
        try:
            rows = _read(effective, parameters, max_rows=2001)
            row_cap_reached = len(rows) > 2000
            rows = rows[:2000]
            rows = _trim(rows)
            payload = json.dumps(rows, default=str)
            truncated = False
            if len(payload) > MAX_RESULT_CHARS:
                kept, size = [], 0
                for row in rows:
                    s = json.dumps(row, default=str)
                    if size + len(s) > MAX_RESULT_CHARS:
                        break
                    kept.append(row)
                    size += len(s)
                payload = json.dumps(kept, default=str)
                truncated = True

            meta = {}
            if row_cap_reached:
                meta["row_cap"] = "Preview limited to 2000 rows. Use spoke-submit_query_job for a complete local export."
            if added_limit:
                meta["note"] = (f"No LIMIT was given; a safety LIMIT {DEFAULT_SAFETY_LIMIT} was "
                                "applied. Add your own LIMIT/aggregation for full control.")
            if truncated:
                meta["truncated"] = (f"Result truncated to ~{MAX_RESULT_CHARS} chars; refine the "
                                     "query (narrower filter, fewer returned properties, or LIMIT).")
            if not rows:
                meta["empty"] = ("0 rows. If you matched by name, the name may differ in case or "
                                 "spelling - call resolve_entity to get the canonical name/identifier, "
                                 "or check the relationship direction/type in get_spoke_schema.")
            text = payload if not meta else json.dumps({"results": json.loads(payload), "meta": meta})
            return ToolResult(content=[TextContent(type="text", text=text)])
        except Exception as error:
            raise _safe_tool_error(error) from None

    register_job_tools(mcp, "spokeagent", config.model_dump(), prefix="spoke-")
    return mcp


def main(
    transport: Literal["stdio", "sse", "http"] = "stdio",
    log_level: str = "INFO",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp/",
) -> None:
    """Main entry point for the SPOKEAgent server"""
    config = SPOKEConfig(**from_environment(), log_level=log_level)
    logger.info("Starting SPOKEAgent - SPOKE Knowledge Graph MCP Server")
    mcp = create_spoke_server(config)
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=transport, host=host, port=port, path=path)


if __name__ == "__main__":
    main(log_level=os.getenv("SPOKE_LOG_LEVEL", "INFO"))
