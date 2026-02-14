"""
SPOKEAgent - SPOKE Knowledge Graph MCP Server

Command-line interface for the SPOKEAgent server.
"""

import logging
import os
from typing import Optional

from spokeagent.server import main as server_main


logger = logging.getLogger("SPOKEAgent")


def main() -> None:
    """
    Main entry point for the SPOKEAgent CLI.

    Reads configuration from environment variables and starts the server.
    Environment variables are typically set by the MCP client.
    """

    # Set up logging
    log_level = os.getenv("SPOKE_LOG_LEVEL", "INFO")
    logging.basicConfig(level=getattr(logging, log_level.upper()))

    logger.info("Starting SPOKEAgent - SPOKE Knowledge Graph MCP Server")

    # Run the server
    server_main(log_level=log_level)


if __name__ == "__main__":
    main()
