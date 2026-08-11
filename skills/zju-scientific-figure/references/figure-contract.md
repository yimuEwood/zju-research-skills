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
panels: []
exports: []
```

Each panel needs a panel ID, question, source anchor, plot/image type, variables and units, experimental unit, uncertainty encoding, statistical annotation source, and relationship to the figure conclusion.

For generated schematics, list every depicted entity, relation, certainty state, and source. Use visual conventions to distinguish observed, inferred, hypothetical, and planned elements.
