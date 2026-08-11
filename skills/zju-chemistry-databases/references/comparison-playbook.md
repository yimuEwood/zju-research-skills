# Condition-aware chemistry comparison playbook

Use this playbook when the question asks which value, compound, route, or
candidate is "better". Retrieval is not yet comparison: first decide whether
the records describe the same entity, endpoint, basis, conditions and evidence
stratum.

## Query decomposition

1. **Entity block** — parent connectivity, stereochemistry, isotope, charge,
   tautomer/protomer, salt/solvate, mixture and crystal form.
2. **Endpoint block** — property or assay endpoint, relation operator, unit and
   measurement basis (mass/molar, wet/dry, total/free, IC50/Ki/Kd, etc.).
3. **Condition block** — temperature, pressure, pH, solvent, atmosphere,
   polymorph, instrument/method, organism/construct/cell line and exposure time.
4. **Evidence block** — experimental primary report, curated record, submitted
   record, computed/predicted value, regulatory entry or vendor claim.
5. **Time/version block** — database release, query time, correction/retraction
   status and the primary-source version.

Run exact identifiers and structure/substructure/similarity queries as separate
branches. For reactions, add transformation, atom mapping or role-aware
reagent/catalyst/solvent branches. For medicinal chemistry, search target,
assay family, organism/construct and relation-qualified endpoint separately.

## Comparability decision

Records can share a direct comparison group only when all of the following are
aligned or explicitly converted with a documented rule:

- resolved chemical entity and material form;
- endpoint definition and relation operator;
- unit and basis;
- material conditions and sample state;
- method or assay context;
- evidence stratum.

Do not pool measured and predicted values, racemate and enantiomer, free base
and salt, different polymorphs, total and unbound concentrations, or endpoints
with different constructs. Present them as parallel strata and explain what new
measurement would discriminate them.

## Conflict resolution output

For each apparent conflict report:

| Field | Required content |
|---|---|
| conflict_id | Stable local ID |
| record_ids | All conflicting records |
| identity_match | exact / partial / no / unresolved |
| condition_match | exact / convertible / incompatible / missing |
| evidence_rank | Primary measured evidence first; never rank solely by database popularity |
| likely_explanation | Method, sample, form, unit, transcription, version or genuine heterogeneity |
| decision | prefer / retain_as_strata / cannot_resolve |
| discriminating_action | Primary-source check, unit/basis recovery or new measurement |

Use `scripts/build_evidence_matrix.py` to create conservative comparison groups.
The script deliberately performs no unit conversion and no automatic averaging.
