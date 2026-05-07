# SPOKEAgent

An MCP (Model Context Protocol) server for querying the SPOKE biomedical knowledge graph for rapid biomedical knowledge inference. Points to the official release of SPOKE.

## Features

- **Query SPOKE Knowledge Graph**: Execute Cypher queries on the SPOKE biomedical knowledge graph

- **Get SPOKE Schema**: Retrieve the complete schema of the SPOKE knowledge graph including nodes, relationships, and properties

## Access

SPOKEAgent is currently in internal testing. Updates to follow soon.

## Usage / Installation

SPOKEAgent is designed to be used with **[BioRouter](https://biorouterapp.com)**. To add it:

1. In BioRouter, go to **Add custom extension**

2. Fill in the extension name and description

3. For the one-liner command, use the following (replacing `<your-passcode>` with the value from the credentials page above):

```bash
SPOKEAGENT_PASSCODE=<your-passcode> uvx --from git+https://github.com/IlanLadabaum/SPOKEAgent spokeagent
```

1. Click **Add extension** — you're ready to go

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

- Wanjun Gu ([wanjun.gu@ucsf.edu](mailto:wanjun.gu@ucsf.edu))

- Gianmarco Bellucci ([gianmarco.bellucci@ucsf.edu](mailto:gianmarco.bellucci@ucsf.edu))

## Editors

- Ilan Ladabaum ([ilan.ladabaum@ucsf.edu](mailto:ilan.ladabaum@ucsf.edu))

## About SPOKE

SPOKE (Scalable Precision medicine Oriented Knowledge Engine) is a large-scale biomedical knowledge graph that integrates data from multiple sources to support precision medicine research.
