# Figure contract

Record before implementation:

```yaml
figure_id: F1
bounded_conclusion: ""
audience_and_journal_stage: ""
route: data_figure | assembled_figure | scientific_schematic | audit_only
backend: python | r | other
target_width_mm: 0
  source_files: []
  transformations: []
  result_registry: "result-registry.json"
  panels: []
exports: []
```

Each quantitative panel needs a panel ID, question, source anchor, plot/image type, variables and units, experimental unit, analysis IDs, result IDs, uncertainty encoding, statistical annotation source, transformation recipe, figure-source artifact ID, and relationship to the figure conclusion. Record every value rendered in a panel or legend as a result-registry `use` with `consumer_type: figure`.

Tables follow the same contract: generate the table body and footnotes from result IDs, retain a machine-readable table-source artifact, and reconcile displayed rounding separately from the underlying machine-readable value.

For generated schematics, list every depicted entity, relation, certainty state, and source. Use visual conventions to distinguish observed, inferred, hypothetical, and planned elements.
