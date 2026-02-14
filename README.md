# SPOKEAgent

An MCP (Model Context Protocol) server for querying the SPOKE biomedical knowledge graph for rapid biomedical knowledge inference.

## Features

- **Query SPOKE Knowledge Graph**: Execute Cypher queries on the SPOKE biomedical knowledge graph
- **Get SPOKE Schema**: Retrieve the complete schema of the SPOKE knowledge graph including nodes, relationships, and properties
- **Pre-configured Access**: All SPOKE connection parameters are pre-configured - no setup required!

## Installation

### From GitHub (using uvx)

```bash
uvx --from git+https://github.com/BaranziniLab/SPOKEAgent spokeagent
```

### Local Installation

```bash
cd SPOKEAgent
pip install -e .
```

## Usage

### As an MCP Server

Add to your MCP client configuration (e.g., Claude Desktop):

```json
{
  "mcpServers": {
    "spokeagent": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/BaranziniLab/SPOKEAgent", "spokeagent"]
    }
  }
}
```

### Direct Command Line

```bash
spokeagent
```

## Configuration

All SPOKE connection parameters are pre-configured:
- **URI**: bolt://spokedev.cgl.ucsf.edu:7687
- **Username**: neo4j
- **Password**: SPOKEdev
- **Database**: spoke

No additional configuration is required!

Optional environment variable:
- `SPOKE_LOG_LEVEL`: Set logging level (DEBUG, INFO, WARNING, ERROR) - defaults to INFO

## Available Tools

### 1. `query_spoke`

Execute a read-only Cypher query on the SPOKE biomedical knowledge graph.

**Parameters:**
- `cypher_query` (string, required): The Cypher query for biomedical knowledge inference
- `parameters` (dict, optional): Parameters to pass to the SPOKE query

**Example:**
```cypher
MATCH (d:Disease)-[r:ASSOCIATES_DaG]->(g:Gene)
WHERE d.name = "Alzheimer's disease"
RETURN g.name, r.score
LIMIT 10
```

### 2. `get_spoke_schema`

List all nodes, their attributes, and their relationships in the SPOKE biomedical knowledge graph.

**Returns:** Complete schema including node types, properties, and relationships.

## Security

This server enforces read-only access to the SPOKE knowledge graph. Write operations (CREATE, MERGE, DELETE, etc.) are not permitted.

## License

MIT

## Authors

- Wanjun Gu (wanjun.gu@ucsf.edu)
- Gianmarco Bellucci (gianmarco.bellucci@ucsf.edu)

## About SPOKE

SPOKE (Scalable Precision medicine Oriented Knowledge Engine) is a large-scale biomedical knowledge graph that integrates data from multiple sources to support precision medicine research.
