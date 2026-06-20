"""
SPOKEAgent - SPOKE Knowledge Graph MCP Server

An MCP server for querying the SPOKE biomedical knowledge graph
for rapid biomedical knowledge inference.

This server is structure-aware: it introspects the live SPOKE schema (node
labels, relationship types and counts, indexes) at runtime, so it tolerates
schema changes (new/renamed labels or edges, added properties) without code
changes. It exposes three tools:

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
from typing import Any, Literal, Optional

from fastmcp.exceptions import ToolError
from fastmcp.server import FastMCP
from fastmcp.tools.tool import ToolResult, TextContent
from mcp.types import ToolAnnotations
from neo4j import Driver, GraphDatabase, Result, Transaction
from neo4j.exceptions import ClientError, Neo4jError
from pydantic import BaseModel, Field

logger = logging.getLogger("SPOKEAgent")

# SPOKE configuration
_pc = os.environ.get("SPOKEAGENT_PASSCODE")
if not _pc:
    raise RuntimeError("SPOKEAGENT_PASSCODE environment variable is required")
_pk: bytes = _pc.encode()
_r  = lambda s: bytes(b ^ _pk[i % len(_pk)] for i, b in enumerate(base64.b64decode(s))).decode()
SPOKE_URI      = _r("ER8DH18bTh8cHBsKRQZTDUIZEAMJRQBQFFZbRUhY")
SPOKE_USERNAME = _r("HRUAXw8=")
SPOKE_PASSWORD = _r("ICAgICBQBBo=")
SPOKE_DATABASE = _r("AAAAAAA=")

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
    uri: str = Field(default=SPOKE_URI, description="SPOKE knowledge graph connection URI")
    username: str = Field(default=SPOKE_USERNAME, description="SPOKE username")
    password: str = Field(default=SPOKE_PASSWORD, description="SPOKE password")
    database: str = Field(default=SPOKE_DATABASE, description="SPOKE database name")
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
    mcp = FastMCP("SPOKEAgent")

    # Knowledge graph driver initialization
    try:
        kg_driver = GraphDatabase.driver(config.uri, auth=(config.username, config.password))
        logger.info(f"SPOKE knowledge graph driver initialized for {config.uri}")
    except Exception as e:
        logger.error(f"Failed to initialize SPOKE driver: {e}")
        raise ToolError(f"SPOKE initialization failed: {e}")

    _schema_cache: dict[str, Any] = {}

    # ---- low-level helpers ---------------------------------------------------
    def _read(cypher: str, params: Optional[dict] = None, timeout: int = QUERY_TIMEOUT_S):
        """Run a read query in a time-bounded read transaction; return rows."""
        with kg_driver.session(database=config.database, default_access_mode="READ") as session:
            with session.begin_transaction(timeout=timeout) as tx:
                result = tx.run(cypher, params or {})
                rows = [r.data() for r in result]
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
            _schema_cache.clear()
            _schema_cache.update(_build_schema())
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
        except ClientError as e:
            if "ProcedureNotFound" in str(e):
                raise ToolError("SPOKE APOC plugin not installed. Please install and enable APOC.")
            raise ToolError(f"SPOKE client error: {e}")
        except Neo4jError as e:
            raise ToolError(f"SPOKE error: {e}")
        except Exception as e:
            logger.error(f"Error retrieving SPOKE schema: {e}")
            raise ToolError(f"Unexpected error retrieving SPOKE schema: {e}")

    def _resolve_candidates(q: str, label: Optional[str], limit: int) -> list[dict]:
        """Shared resolver used by resolve_entity and describe_node. Returns a
        ranked list of {label, name, identifier, matched_on, score}."""
        # Models sometimes pass the literal strings "None"/"null"/"any" for "no
        # label"; treat those as unset rather than a (nonexistent) label.
        if label is not None and str(label).strip().lower() in ("", "none", "null", "any", "all"):
            label = None
        results: list[dict] = []
        seen: set = set()

        def add(rows, matched_on):
            for row in rows:
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
            if label and not id_labels:
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
                    add(_read(
                        f"MATCH (n:{lab}) WHERE n.identifier = $q "
                        "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                        {"q": q, "lim": limit}), f"identifier:{lab}")
                except Exception:
                    pass
            if q.isdigit():                                       # Entrez integer ids
                for lab in ("Gene", "Organism"):
                    try:
                        add(_read(
                            f"MATCH (n:{lab}) WHERE n.identifier = $qi "
                            "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                            {"qi": int(q), "lim": limit}), f"entrez:{lab}")
                    except Exception:
                        pass
            if re.match(r"^ENSG\d+", up):                         # Ensembl gene id
                try:
                    add(_read("MATCH (g:Gene) WHERE g.ensembl = $q "
                              "RETURN 'Gene' AS l, g.name AS name, g.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "ensembl")
                except Exception:
                    pass
            if re.match(r"^DB\d+$", up):                          # DrugBank xref (unindexed)
                try:
                    add(_read("MATCH (c:Compound) WHERE any(x IN c.xrefs WHERE x ENDS WITH $q) "
                              "RETURN 'Compound' AS l, c.name AS name, c.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "xref:drugbank")
                except Exception:
                    pass
            if q.isdigit():                                       # OMIM on Disease.omim_list
                try:
                    add(_read("MATCH (d:Disease) WHERE $q IN d.omim_list "
                              "RETURN 'Disease' AS l, d.name AS name, d.identifier AS id LIMIT $lim",
                              {"q": q, "lim": limit}), "omim")
                except Exception:
                    pass

        # ---- name strategies -------------------------------------------------
        if label:                                                # exact, case-sensitive (index)
            try:
                add(_read(f"MATCH (n:{label}) WHERE n.name = $q "
                          "RETURN labels(n)[0] AS l, n.name AS name, n.identifier AS id LIMIT $lim",
                          {"q": q, "lim": limit}), "name:exact")
            except Exception:
                pass
        # full-text phrase (case-insensitive, includes synonyms); prioritise ci-exact
        idx = (label + "NamesAndIds") if label else "anyNamesAndIds"
        phrase = '"' + q.replace('"', " ") + '"'
        try:
            ft = _read(
                "CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score "
                "RETURN labels(node)[0] AS l, node.name AS name, node.identifier AS id, score "
                "LIMIT 25", {"idx": idx, "q": phrase})
            ci_exact = [r for r in ft if (r.get("name") or "").lower() == q.lower()]
            add(ci_exact, "name:exact-ci")
            add(ft, "name:fulltext")
        except Exception as e:
            logger.debug(f"fulltext resolve failed for {idx}: {e}")

        # Fallback: if the strict phrase matched nothing, retry full-text with the
        # bare tokens (OR semantics). Catches common names that don't appear verbatim
        # (e.g. "beta blockers" -> "Adrenergic beta-Antagonists").
        if not results and len(q) >= 3 and not looks_like_id:
            try:
                loose = _read(
                    "CALL db.index.fulltext.queryNodes($idx, $q) YIELD node, score "
                    "RETURN labels(node)[0] AS l, node.name AS name, node.identifier AS id, score "
                    "LIMIT 15", {"idx": idx, "q": q})
                add(loose, "name:fulltext-loose")
            except Exception as e:
                logger.debug(f"loose fulltext resolve failed for {idx}: {e}")

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
                anchor = "n.identifier = $v" if c.get("identifier") is not None else "n.name = $v"
                val = c.get("identifier") if c.get("identifier") is not None else c.get("name")
                # f-string: {lab} interpolates, {{ }} become literal braces for COUNT{...}
                cy = f"MATCH (n:{lab}) WHERE {anchor} RETURN COUNT{{(n)--()}} AS d LIMIT 1"
                try:
                    d = _read(cy, {"v": val})
                    c["degree"] = d[0]["d"] if d else None
                except Exception:
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
        full-text indexes, so it is fast and never scans the whole graph.

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
        except Neo4jError as e:
            raise ToolError(f"SPOKE resolve_entity error: {e}")
        except Exception as e:
            logger.error(f"resolve_entity error: {e}")
            raise ToolError(f"resolve_entity failed: {e}")

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
        tells you immediately so you can report the absence instead of guessing more
        queries. Also ideal for open-ended "how is X connected / what is near X"
        questions. The node is resolved first (handles case / apostrophes / ids).

        Returns {node:{label,name,identifier}, relationships:[{dir, rel, neighbor_label, count}]}.
        """
        q = (query or "").strip()
        if not q:
            raise ToolError("describe_node: empty query")
        try:
            cands = _resolve_candidates(q, label, 1)
            if not cands:
                return ToolResult(content=[TextContent(type="text", text=json.dumps({
                    "query": q, "label": label, "node": None,
                    "hint": "Could not resolve the node; check spelling or call resolve_entity."}))])
            node = cands[0]
            lab = node["label"]
            anchor = "n.identifier = $id" if node.get("identifier") is not None else "n.name = $nm"
            params = {"id": node.get("identifier"), "nm": node.get("name")}
            rels = _read(
                f"MATCH (n:{lab}) WHERE {anchor} WITH n LIMIT 1 "
                "MATCH (n)-[r]-(m) "
                "RETURN type(r) AS rel, labels(m)[0] AS neighbor_label, "
                "CASE WHEN startNode(r)=n THEN '->' ELSE '<-' END AS dir, count(*) AS count "
                "ORDER BY count DESC LIMIT 60",
                params)
            out = {
                "node": {"label": lab, "name": node.get("name"), "identifier": node.get("identifier")},
                "relationships": rels,
                "note": "Only relationship types listed here exist on this node. If the edge you "
                        "expected is absent, the data simply isn't in SPOKE for this node - report "
                        "that rather than trying more variations.",
            }
            return ToolResult(content=[TextContent(type="text", text=json.dumps(out, default=str))])
        except Neo4jError as e:
            raise ToolError(f"SPOKE describe_node error: {e}")
        except Exception as e:
            logger.error(f"describe_node error: {e}")
            raise ToolError(f"describe_node failed: {e}")

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
            cannot accidentally scan the 43M-node graph; aggregations and queries
            with your own LIMIT are left as-is.
          * A transaction timeout aborts pathological queries instead of hanging.
          * Output is trimmed (noisy HTML/link fields removed, long strings cut)
            and capped in size to stay efficient.

        Tips: resolve names first with resolve_entity; use the edge_directory from
        get_spoke_schema to choose relationship types; pass literals via parameters.
        """
        if _is_write_query(cypher_query):
            raise ToolError("Only read queries (MATCH, RETURN, CALL db.*, etc.) are allowed for SPOKE.")

        effective, added_limit = _maybe_add_limit(cypher_query)
        try:
            rows = _read(effective, parameters)
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
        except ClientError as e:
            msg = str(e)
            if "TransactionTimedOut" in msg or "timed out" in msg.lower():
                raise ToolError("SPOKE query timed out. Anchor on a resolved node (name/identifier), "
                                "add filters/LIMIT, and avoid traversing expensive (>1M) edges "
                                "unanchored. See get_spoke_schema cost flags.")
            raise ToolError(f"SPOKE query error: {e}")
        except Neo4jError as e:
            logger.error(f"SPOKE error executing query: {e}")
            raise ToolError(f"SPOKE biomedical knowledge graph error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in SPOKE query: {e}")
            raise ToolError(f"Error executing SPOKE biomedical knowledge query: {e}")

    return mcp


def main(
    transport: Literal["stdio", "sse", "http"] = "stdio",
    log_level: str = "INFO",
    host: str = "127.0.0.1",
    port: int = 8000,
    path: str = "/mcp/",
) -> None:
    """Main entry point for the SPOKEAgent server"""
    config = SPOKEConfig(log_level=log_level)
    logger.info("Starting SPOKEAgent - SPOKE Knowledge Graph MCP Server")
    logger.info(f"SPOKE URI: {config.uri}")
    logger.info(f"SPOKE Database: {config.database}")
    mcp = create_spoke_server(config)
    mcp.run()


if __name__ == "__main__":
    main(log_level=os.getenv("SPOKE_LOG_LEVEL", "INFO"))
