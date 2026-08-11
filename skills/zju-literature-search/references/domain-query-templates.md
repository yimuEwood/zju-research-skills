# Four-domain query decomposition templates

Use these as auditable starting structures, not copy-paste universal queries. Assign a `concept_id` to every bracketed block, test each block alone, then adapt field syntax to the selected source.

## Chemistry: reaction or molecular-function question

Question frame: *How does [catalyst/compound class] change [transformation or activity] under [conditions], compared with [baseline], and what mechanism limits selectivity/stability?*

Query views:

- Entity: `([preferred name] OR [synonym] OR [CAS/InChIKey/SMILES-derived class])`.
- Transformation/function: `([reaction name] OR [bond formed/cleaved] OR [target/pathway] OR [bioactivity endpoint])`.
- Conditions: `([solvent/electrolyte] OR [temperature/pressure/pH] OR [light/current])`.
- Mechanism: `([intermediate] OR [transition state] OR kinetics OR isotope effect OR operando OR in situ)`.
- Contradiction/failure: `(deactivation OR poisoning OR side reaction OR low selectivity OR inactive OR irreproducible)`.

Route scholarly papers plus structure/substance or bioactivity databases as appropriate; add reaction/patent sources for preparative prior art. Judge evidence using identity certainty, exact composition/stereochemistry, control reactions, analytical confirmation, mass balance/selectivity, kinetics, and whether the proposed intermediate was observed or inferred.

## Materials: composition-process-structure-property-performance

Question frame: *For [material system], how does [composition/process] alter [structure/defect/interface], [property], and [device/service performance] under [operating environment]?*

Query views:

- Composition: `([formula] OR [phase name] OR [dopant] OR [family synonym])`.
- Process: `([synthesis/deposition/annealing] OR [temperature] OR [atmosphere])`.
- Structure: `([phase] OR crystal OR defect OR grain boundary OR interface OR morphology)`.
- Property/performance: `([property name + symbol] OR [device metric] OR degradation OR stability)`.
- Boundary/failure: `([humidity/temperature/cycling/load] AND (failure OR instability OR hysteresis OR degradation))`.

Search broad and materials-focused indexes, then chase citations from the closest composition-process match. Judge evidence using phase/composition verification, batch and sample count, processing history, measurement protocol, device area/loading, uncertainty, accelerated versus real-world conditions, and whether property gains trade off against stability or manufacturability.

## Biomedicine: intervention, mechanism, outcome, and bias

Question frame: *In [population/model], what is the effect of [intervention/exposure] versus [comparator] on [outcome/time], through which mechanism, and for whom might it fail or harm?*

Query views:

- Population: `(MeSH/controlled term OR disease/model synonyms OR genotype/phenotype)`.
- Intervention/exposure: `(generic name OR target OR class OR protocol name)`.
- Outcome: `(validated endpoint OR biomarker OR adverse event OR patient-important outcome)`.
- Design: add validated filters only when recall loss is acceptable; keep trial registration identifiers.
- Contradiction/failure: `(adverse OR toxicity OR nonresponse OR null OR resistance OR relapse OR failed trial)`.

Search PubMed/Europe PMC plus trial registries and a broad citation index; map PMID/PMCID/DOI and preprint-publication pairs. Judge evidence by design-appropriate risk of bias, allocation/blinding, experimental unit, endpoint prespecification, effect size and uncertainty, attrition/missingness, multiplicity, clinical versus surrogate outcome, and directness to the target population.

## Agriculture: organism-management-environment-outcome

Question frame: *For [species/cultivar/breed] in [production system/environment], how does [management/input/stressor] affect [yield/quality/health/ecosystem outcome] across seasons and locations?*

Query views:

- Organism: `([scientific name] OR [common name] OR [cultivar/breed])`.
- Management/stressor: `([input/practice/pathogen] OR [dose/timing] OR [resistance gene])`.
- Environment: `([field/greenhouse/livestock system] OR [soil/climate/water regime] OR [region])`.
- Outcome: `(yield OR quality OR disease severity OR resource efficiency OR environmental impact)`.
- Transfer/failure: `(multi-location OR multi-season OR genotype environment OR resistance breakdown OR non-target effect)`.

Search agriculture-focused, biomedical/ecological when relevant, broad indexes, and Chinese literature sources for local production systems. Judge evidence using plot/animal as the correct experimental unit, randomization/blocking, site-year replication, cultivar and management context, baseline soil/environment, outcome measurement, missing plots/animals, multiplicity, and generalization across environments.

## Cross-domain handoff

Each domain template must end with: exact source queries, covered concept IDs, retained record IDs, a seed graph, contradiction-search results, saturation status, and gap IDs. These fields form `search-map.json` and remain stable through full-text access, reading, synthesis, hypothesis design, and monitoring.
