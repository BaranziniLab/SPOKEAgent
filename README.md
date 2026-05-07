# SPOKEdevAgent

An MCP (Model Context Protocol) server for querying the SPOKE biomedical knowledge graph for rapid biomedical knowledge inference.

## BioRouter Extension

**[Download spokeagent.brxt](https://github.com/BaranziniLab/SPOKEdevAgent/releases/latest/download/spokeagent.brxt)**

Drag the `.brxt` file into BioRouter's **Extensions → Add extension** dialog. BioRouter will install the virtual environment automatically and prompt for the required passcode.

| Variable              | Required | Default | Description                      |
| --------------------- | -------- | ------- | -------------------------------- |
| `SPOKEAGENT_PASSCODE` | ✅        | —       | Access passcode provided by UCSF |
| `SPOKE_LOG_LEVEL`     | optional | `INFO`  | Logging level                    |

## Features

- **Query SPOKE Knowledge Graph**: Execute Cypher queries on the SPOKE biomedical knowledge graph

- **Get SPOKE Schema**: Retrieve the complete schema of the SPOKE knowledge graph including nodes, relationships, and properties

## Access

SPOKEdevAgent is currently available to **UCSF affiliates only**.

To run the server, you will need a passcode (`SPOKEAGENT_PASSCODE`). Log in with your UCSF credentials at the link below to retrieve it:

**[SPOKEdevAgent Credentials (UCSF affiliates)](https://wiki.library.ucsf.edu/pages/viewpage.action?pageId=755904655&spaceKey=~Wanjun.Gu%40ucsf.edu&title=SPOKEAgent%2BCredentials)**

That page will show you how to set the environment variable and run the server.

## Usage

SPOKEdevAgent is designed to be used with **[BioRouter](https://biorouterapp.com)**. To add it:

1. In BioRouter, go to **Add custom extension**

2. Fill in the extension name and description

3. For the command, use the following:

```bash
uvx --from git+https://github.com/BaranziniLab/SPOKEdevAgent spokedevagent
```

4. Add an environment variable:

   a. Variable name = "SPOKEAGENT_PASSCODE"

   b. Value =`<your-passcode>`

```
 (replacing `<your-passcode>` with the value from the credentials page above.)
```

   c. Click "+ Add" to add the variable.

5. Click **Add extension** — you're ready to go

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
