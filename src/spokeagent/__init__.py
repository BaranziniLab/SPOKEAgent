"""
SPOKEAgent - SPOKE Knowledge Graph MCP Server

An MCP server for querying the SPOKE biomedical knowledge graph
for rapid biomedical knowledge inference.
"""

__version__ = "0.1.0"

from spokeagent.server import create_spoke_server, main, SPOKEConfig

__all__ = ["create_spoke_server", "main", "SPOKEConfig", "__version__"]
