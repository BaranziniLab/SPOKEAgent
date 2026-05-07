"""
SPOKEAgent - SPOKE Knowledge Graph MCP Server

An MCP server for querying the SPOKE biomedical knowledge graph
for rapid biomedical knowledge inference.
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


class SPOKEConfig(BaseModel):
    """SPOKE knowledge graph configuration"""
    uri: str = Field(default=SPOKE_URI, description="SPOKE knowledge graph connection URI")
    username: str = Field(default=SPOKE_USERNAME, description="SPOKE username")
    password: str = Field(default=SPOKE_PASSWORD, description="SPOKE password")
    database: str = Field(default=SPOKE_DATABASE, description="SPOKE database name")
    log_level: str = Field("INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")


def _read_knowledge_graph(tx: Transaction, cypher_query: str, params: dict[str, Any]) -> str:
    """Execute read-only knowledge graph transaction"""
    raw_results = tx.run(cypher_query, params)
    eager_results = raw_results.to_eager_result()
    return json.dumps([r.data() for r in eager_results.records], default=str)


def _is_write_query(query: str) -> bool:
    """Check if the query contains write operations"""
    return re.search(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|ADD|INSERT|UPDATE|DROP|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|SP_)\b", query, re.IGNORECASE) is not None


def create_spoke_server(config: SPOKEConfig) -> FastMCP:
    """Create SPOKEAgent server with SPOKE knowledge graph tools"""

    # Set up logging
    logging.basicConfig(level=getattr(logging, config.log_level.upper()))

    mcp = FastMCP("SPOKEAgent")

    # Knowledge graph driver initialization
    kg_driver = None
    try:
        kg_driver = GraphDatabase.driver(
            config.uri,
            auth=(config.username, config.password)
        )
        logger.info(f"SPOKE knowledge graph driver initialized for {config.uri}")
    except Exception as e:
        logger.error(f"Failed to initialize SPOKE driver: {e}")
        raise ToolError(f"SPOKE initialization failed: {e}")

    def clean_schema(schema: dict) -> dict:
        """Clean and simplify schema output"""
        cleaned = {}
        for key, entry in schema.items():
            new_entry = {"type": entry["type"]}

            if "count" in entry:
                new_entry["count"] = entry["count"]

            if "labels" in entry and entry["labels"]:
                new_entry["labels"] = entry["labels"]

            # Clean properties
            if "properties" in entry:
                clean_props = {}
                for pname, pinfo in entry["properties"].items():
                    cp = {}
                    for attr in ["indexed", "type"]:
                        if attr in pinfo:
                            cp[attr] = pinfo[attr]
                    if cp:
                        clean_props[pname] = cp
                if clean_props:
                    new_entry["properties"] = clean_props

            # Clean relationships
            if "relationships" in entry:
                rels_out = {}
                for rel_name, rel in entry["relationships"].items():
                    cr = {}
                    if "direction" in rel:
                        cr["direction"] = rel["direction"]
                    if "labels" in rel and rel["labels"]:
                        cr["labels"] = rel["labels"]

                    # Clean relationship properties
                    if "properties" in rel:
                        clean_rprops = {}
                        for rpname, rpinfo in rel["properties"].items():
                            crp = {}
                            for attr in ["indexed", "type"]:
                                if attr in rpinfo:
                                    crp[attr] = rpinfo[attr]
                            if crp:
                                clean_rprops[rpname] = crp
                        if clean_rprops:
                            cr["properties"] = clean_rprops

                    if cr:
                        rels_out[rel_name] = cr

                if rels_out:
                    new_entry["relationships"] = rels_out

            cleaned[key] = new_entry

        return cleaned

    @mcp.tool(
        name="get_spoke_schema",
        annotations=ToolAnnotations(
            title="Get SPOKE Knowledge Graph Schema",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True
        )
    )
    def get_spoke_schema() -> ToolResult:
        """
        List all nodes, their attributes and their relationships in the SPOKE biomedical knowledge graph.
        This provides the schema for drug-disease associations, protein interactions, pathways,
        and other biomedical entities. Requires APOC plugin to be installed and enabled.
        """

        get_schema_query = "CALL apoc.meta.schema();"

        try:
            with kg_driver.session(database=config.database) as session:
                results_json_str = session.execute_read(_read_knowledge_graph, get_schema_query, {})

                schema = json.loads(results_json_str)[0].get('value')
                schema_clean = clean_schema(schema)

                return ToolResult(content=[TextContent(type="text", text=json.dumps(schema_clean))])

        except ClientError as e:
            if "Neo.ClientError.Procedure.ProcedureNotFound" in str(e):
                raise ToolError("SPOKE APOC plugin not installed. Please install and enable APOC for biomedical knowledge inference.")
            else:
                raise ToolError(f"SPOKE client error: {e}")
        except Neo4jError as e:
            raise ToolError(f"SPOKE error: {e}")
        except Exception as e:
            logger.error(f"Error retrieving SPOKE schema: {e}")
            raise ToolError(f"Unexpected error retrieving SPOKE schema: {e}")

    @mcp.tool(
        name="query_spoke",
        annotations=ToolAnnotations(
            title="Query SPOKE Biomedical Knowledge Graph",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True
        )
    )
    def query_spoke(
        cypher_query: str = Field(..., description="The Cypher query for biomedical knowledge inference (e.g., drug-disease associations, protein interactions)"),
        parameters: dict[str, Any] = Field(default_factory=dict, description="Parameters to pass to the SPOKE query")
    ) -> ToolResult:
        """Execute a read-only Cypher query on the SPOKE biomedical knowledge graph for fast knowledge inference."""

        if _is_write_query(cypher_query):
            raise ToolError("Only read queries (MATCH, RETURN, etc.) are allowed for SPOKE queries")

        try:
            with kg_driver.session(database=config.database) as session:
                results_json_str = session.execute_read(_read_knowledge_graph, cypher_query, parameters)

                logger.debug(f"SPOKE query returned {len(results_json_str)} characters")

                return ToolResult(content=[TextContent(type="text", text=results_json_str)])

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

    # Create config with hardcoded values
    config = SPOKEConfig(log_level=log_level)

    logger.info("Starting SPOKEAgent - SPOKE Knowledge Graph MCP Server")
    logger.info(f"SPOKE URI: {config.uri}")
    logger.info(f"SPOKE Database: {config.database}")

    mcp = create_spoke_server(config)
    mcp.run()


if __name__ == "__main__":
    # Configuration provided by MCP client through environment variables
    main(
        log_level=os.getenv("SPOKE_LOG_LEVEL", "INFO")
    )
