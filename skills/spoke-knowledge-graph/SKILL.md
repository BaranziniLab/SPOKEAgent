---
name: spoke-knowledge-graph
description: Traverse the SPOKE biomedical knowledge graph to find relationships between diseases, genes, compounds, and biological entities
---

Use this skill when the user wants to explore relationships between biomedical entities — diseases, genes, drugs/compounds, pathways, anatomical structures, side effects, or biological processes — in the SPOKE knowledge graph.

## When to activate

- User asks about connections between diseases, genes, drugs, or biological processes
- User wants to find drug repurposing candidates or mechanism of action
- User asks "what genes are associated with [disease]"
- User asks "what drugs target [gene/protein]"
- User asks "how are [entity A] and [entity B] connected"
- User asks about disease mechanisms, biological pathways, or biomarkers
- User wants to find side effects of a drug or compound

## Key SPOKE entity types

| Node label | Examples |
|-----------|---------|
| `Disease` | Alzheimer disease, Type 2 Diabetes |
| `Gene` | APOE, BRCA1, TP53 |
| `Compound` | Metformin, Ibuprofen |
| `Pathway` | MAPK signaling, Apoptosis |
| `SideEffect` | Nausea, Headache |
| `Anatomy` | Brain, Liver, Heart |
| `BiologicalProcess` | Inflammation, Cell cycle |
| `Protein` | UniProt IDs |
| `PharmacologicClass` | Drug class groupings |

## Approach

1. Call `get_spoke_schema` to understand node types and relationship types available in the current SPOKE instance. Cache this — do not call it repeatedly.

2. Identify the entities the user is asking about. Use standard identifiers where possible:
   - Diseases: DOID identifiers or common names (SPOKE has both)
   - Genes: HGNC symbol (e.g., `APOE`, `TP53`)
   - Compounds: compound name or DrugBank ID

3. Write a targeted Cypher query using `query_spoke`. Always:
   - Use `MATCH` with specific node labels and relationship types from the schema
   - Add `LIMIT` to avoid full-graph traversals (default: `LIMIT 25`)
   - Filter with `WHERE` clauses to keep results relevant
   - Return human-readable properties (`.name`, `.identifier`) not just IDs

4. Interpret results for the user — explain the biological meaning of each relationship type found, and highlight the most clinically or scientifically relevant connections.

5. If the initial query returns too many or too few results, refine:
   - Narrow: add `WHERE` filters or reduce `LIMIT`
   - Broaden: use ancestor nodes, remove type filters, or traverse one more hop

## Example queries

**Genes associated with Alzheimer disease:**
```cypher
MATCH (d:Disease {name: "Alzheimer disease"})-[r:ASSOCIATES_DaG]->(g:Gene)
RETURN g.name AS gene, g.identifier AS ensembl_id, r.score AS score
ORDER BY r.score DESC
LIMIT 20
```

**Drugs that target a gene:**
```cypher
MATCH (c:Compound)-[r:TARGETS_CtG]->(g:Gene {name: "BRAF"})
RETURN c.name AS drug, r.actions AS mechanism
LIMIT 25
```

**Two-hop path between disease and compound:**
```cypher
MATCH path = (d:Disease {name: "Type 2 Diabetes mellitus"})-[*1..2]-(c:Compound)
WHERE c.approved = true
RETURN DISTINCT c.name AS drug, length(path) AS hops
LIMIT 20
```

**Side effects of a drug:**
```cypher
MATCH (c:Compound {name: "Metformin"})-[:CAUSES_CcSE]->(se:SideEffect)
RETURN se.name AS side_effect
LIMIT 30
```

## Notes

- SPOKE integrates data from: OMIM, DrugBank, DisGeNET, SIDER, Reactome, UniProt, ChEMBL, GO, and more
- All queries are read-only — `MERGE`, `CREATE`, `SET`, `DELETE` will be rejected
- Always use `LIMIT` — SPOKE has millions of nodes and relationships
- Property names vary by node type — use `get_spoke_schema` or `RETURN keys(n)` to discover them
- Relationship direction matters in Cypher; when in doubt, use undirected `-[]-`
- SPOKE uses community standard identifiers: Entrez Gene IDs for genes, DOID for diseases, UMLS CUIs for concepts
