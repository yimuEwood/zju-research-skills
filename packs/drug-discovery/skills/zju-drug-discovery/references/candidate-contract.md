# Candidate evidence contract

Input is a JSON object with `candidates`. Each candidate requires:

- `candidate_id`, `target_id`, and `compound_id`;
- `evidence`: records containing `lane`, a normalized `value` from 0 to 1, and a stable `source_id`;
- optional `potency_nM`, `selectivity_ratio`, `safety_flags`, and `hard_exclusion`.

Supported evidence lanes are `genetic`, `disease`, `tractability`, `mechanism`, `assay_reproducibility`, `translational`, and `developability`. The bundled score uses fixed declared weights, explicit missing-lane penalties, potency/selectivity transformations, safety penalties, and leave-one-lane-out sensitivity. It is intentionally a transparent heuristic, not a learned predictor or validated development model.

Keep raw evidence and conditions outside the normalized value. A normalization decision must be reversible to source records.
