# SPOKE Ground Truth (measured 2026-06-20 against bolt://spoke.cgl.ucsf.edu:7687, db=spoke)

Neo4j 5.x with APOC. 43,253,095 nodes, 264 relationship types. Hetionet-style edge
codes: `VERB_AbC` where A = source label initial, b = lowercase verb, C = target label
initial (e.g. `ASSOCIATES_DaG` = Disease→Gene, `BINDS_CbP` = Compound→Protein).

## Node labels (count) — major ones
| Label | Count | identifier namespace | name field | other key props |
|---|---|---|---|---|
| Protein | 39,442,384 | UniProt accession | name (e.g. APOE_HUMAN) | org_ncbi_id, org_name, gene, reviewed, EC, chembl_id |
| Compound | 3,129,786 | `inchikey:...` | name | xrefs (drugbank:/chembl.compound:/pubchem.compound:/kegg...), synonyms, smiles, max_phase |
| Organism | 356,647 | NCBI taxon id | name | level |
| Gene | 20,824 | **Entrez Gene ID** | **HGNC symbol** | ensembl, synonyms, chromosome, description |
| Disease | 12,243 | **DOID** | name (lowercase) | omim_list, mesh_list |
| Anatomy | 16,067 | UBERON | name | mesh_id, bto |
| BiologicalProcess / MolecularFunction / CellularComponent | 13,653 / 3,998 / 1,894 | **GO:** | name | description |
| SideEffect | 5,964 | UMLS CUI (C…) | name | — |
| Symptom | 1,960 | MeSH (D…) | name | — |
| Pathway | 6,180 | Reactome/WikiPathways | name | **vestige** (84% True!), url |
| MiRNA | 2,656 | mature name (hsa-miR-…) | — (no name) | accession |
| PharmacologicClass | 635 | NDF-RT | name | class_type (MoA/PE/…) |
| CellType | 2,754 | CL: | name | synonyms |
| Food | 15,057 | FOODON: | name | — |
| SDoH | 320 | SNOMED_… | name | description, mesh_ids |

Also indexed but small/empty: AnatomyCellType, CellLine, ClinicalLab, Nutrient,
SARSCov2, ProteinFamily(818), Complex(2820), EC(8714), Reaction(38749), ProteinDomain(27557), PwGroup, Location(108k), DietarySupplement, Blend, ExtracellularParticle.

## Identifier resolution
- Every major label has a **UNIQUENESS constraint + RANGE index on `identifier`** and a RANGE index on `name` → exact lookups by `{identifier:…}` or `{name:…}` are fast.
- Per-label FULLTEXT indexes named `<Label>NamesAndIds` (e.g. `DiseaseNamesAndIds`, `GeneNamesAndIds`) over name/description/identifier/synonyms.
- One global FULLTEXT index `anyNamesAndIds` across all labels.
- **Gotcha:** exact `{name:…}` match is **case-sensitive**. Disease names are lowercase ("multiple sclerosis" works, "Multiple Sclerosis" returns 0).
- **Gotcha:** fulltext analyzer is finicky — `DiseaseNamesAndIds('alzheimer')` returned 0 (token mismatch vs "Alzheimer's"); use `alzheimer*` wildcard or lowercase. Global `anyNamesAndIds('APOE')` returns mostly Reaction/PwGroup junk with the real Gene ranked 5th → **must filter by label** after fulltext.
- Compound xrefs example (Metformin): `inchikey:XZWYZXLIPXDOLR-UHFFFAOYSA-N`, xrefs include `drugbank:DB00331`, `chembl.compound:CHEMBL1431`, `pubchem.compound:4091`.

## Edge properties (sampled; many are often NULL)
- `ASSOCIATES_DaG` (Disease→Gene): diseases_scores, diseases_confidences, diseases_sources, gwas_pvalue, sources — **scores frequently NULL** (skill's `r.score` is WRONG; property is `diseases_scores`).
- `TREATS_CtD` (Compound→Disease): phase, purpose, sources, act_sources.
- `BINDS_CbP` (Compound→Protein): chembl_action_type, drugcentral_relationship, bindingdb_ic50s/kds/kis, sources.
- `CAUSES_CcSE`: sources only.
- `UPREGULATES_CuG` / `DOWNREGULATES_CdG` (Compound→Gene): zscore, pvalue, sources.
- `RESEMBLES_DrD` (Disease→Disease): fisher, odds, enrichment, cooccur.
- `PREVALENCE_DpL` (Disease→Location): data_value, total_population, location_name, state_abbr…

## Correct edges for common questions (skill examples are partly WRONG)
- Disease→Gene: `ASSOCIATES_DaG`; also UPREGULATES_KGuG/DOWNREGULATES_KGdG (knockout), MARKER_POS_GmpD/MARKER_NEG_GmnD (Gene→Disease).
- Compound→Disease: `TREATS_CtD`, `CONTRAINDICATES_CcD`, clinical trials `IN_CLINICAL_TRIALS_FOR_CictD` / `MENTIONED_CLINICAL_TRIALS_FOR_CmctD`.
- **Drug targets a gene** = Compound→Protein→Gene: `(c:Compound)-[:BINDS_CbP]->(p:Protein)<-[:ENCODES_GeP]-(g:Gene {name:$sym})`. **There is NO `TARGETS_CtG` edge** (skill is wrong). `TARGETS_MtG` is MiRNA→Gene.
- Gene→Protein: `ENCODES_GeP` (157k). Organism→Protein: `ENCODES_OeP` (38M — avoid).
- Compound→SideEffect: `CAUSES_CcSE`. Disease→Symptom: `PRESENTS_DpS`. Gene→Symptom: `ASSOCIATES_GaS`.
- Disease→Anatomy: `LOCALIZES_DlA`. Anatomy→Gene: `EXPRESSES_AeG`.
- Gene→Pathway: `PARTICIPATES_GpPW`. Gene→BiologicalProcess: `PARTICIPATES_GpBP`. Gene→MolecularFunction: `PARTICIPATES_GpMF`. Gene→CellularComponent: `PARTICIPATES_GpCC`.
- Disease→Disease: `RESEMBLES_DrD`, `ISA_DiD`. Compound→Compound: `ISA_CiC`, `HASROLE_ChC`. PharmacologicClass→Compound: `INCLUDES_PCiC`.
- Protein→Protein: `INTERACTS_PiP` (2.4M). Protein→Compound: `INTERACTS_PiC` (69M — giant, avoid unanchored).

## Performance landmines (measured)
- `MATCH (p:Protein) WHERE p.org_ncbi_id='9606'` → **26 s** (no index on org_ncbi_id; 39M scan; 203,605 human proteins). NEVER scan Protein by organism — anchor via Gene→ENCODES_GeP→Protein instead.
- Giant edges to never traverse unanchored: `INTERACTS_PiC` (69M), `PARTOF_PDpP` (54M), `ENCODES_OeP` (38M), `HAS_PhEC` (3.8M), `INTERACTS_PiP` (2.4M), `TARGETS_MtG` (1.6M), `BINDS_CbP` (840k).
- Unanchored 2-hop `(:Compound)-[:BINDS_CbP]->(:Protein)<-[:BINDS_CbP]-(:Compound)` → does not return within 6 s (cartesian blow-up).
- `vestige=True` on most Pathways (5193/6180) and some other nodes → filter `WHERE NOT coalesce(n.vestige,false)` (note: vestige may be stored as boolean True or string "True" depending on node — handle both).
- Anchor every query on an indexed `{name:…}` or `{identifier:…}`, cap variable-length paths (`*1..2`), always `LIMIT`.

## Properties are sometimes stringified lists
Some list-valued props come back as Python-repr strings (e.g. `sources: "['Entrez Gene']"`, `synonyms: "['FAM90A3P']"`) rather than native arrays, while others (Compound.xrefs) are native arrays. Don't assume `IN` works on them; may need string CONTAINS.
