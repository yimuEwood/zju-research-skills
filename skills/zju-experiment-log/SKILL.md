---
name: zju-experiment-log
description: Convert experiment notes, image metadata, audio transcripts, and mixed local inputs into a traceable Markdown laboratory record with parseable YAML frontmatter. Use for experiment logging, daily lab notes, instrument-run records, sample tracking, or reconstructing a record from raw files. Obsidian and Feishu are optional destinations, never prerequisites.
---

# ZJU Experiment Log

Create an immutable-source, traceable experiment record. Preserve uncertainty and distinguish what was observed from what was inferred.

## Workflow

1. Inventory all supplied text, images, audio/transcripts, instrument exports, and related files. Do not move, rename, modify, or delete originals unless explicitly requested.
2. Extract observable facts with source paths and timestamps. For images, record the file and visible observation; for audio, retain the transcript and mark uncertain words with timestamps when possible.
3. Ask only for blocking identifiers such as experiment ID, date, operator, project, or sample mapping. Leave non-blocking unknowns as `unknown`; never guess.
4. Read `references/log-schema.md` and create Markdown with parseable YAML. `scripts/build_log.py` can deterministically render a structured JSON intake:

   `python scripts/build_log.py --input intake.json --output 2026-08-10-exp-001.md`

5. Separate objective, protocol, deviations, raw observations, derived results, interpretation, anomalies, and next actions. Preserve units and instrument settings.
6. Add source-file paths and SHA-256 hashes when the files are accessible. A hash establishes file identity, not scientific validity.
7. For an instrument, simulation, analysis, or code run, also read `references/run-manifest.md`. Record inputs, parameters, software/environment, outputs, hashes, status, and the next decision gate. Validate structured JSON with:

   `python scripts/validate_run_manifest.py --input run-manifest.json --output run-manifest-report.json`

8. Validate confidentiality and destination rules using `references/privacy-safety.md`. Write to a Feishu or Obsidian adapter only when the user selects and authorizes it.

## Correction Fallback

If asked to overwrite or erase an earlier observation, refuse the silent change and immediately provide an append-only amendment record with `amendment_id`, `parent_record_id`, `recorded_at`, `actor`, `field`, `old_value`, `new_value`, `reason`, and `source_anchor`. Use `unknown` for absent values and ask only for the fields required to finalize the amendment. Preserve both reported experiment time and actual amendment time.

## Integrity Boundary

- Never backdate a record without labeling the actual creation time and reported experiment time.
- Never rewrite an observation to match an interpretation. Append corrections with reason and timestamp.
- Never invent reagent lots, sample IDs, parameters, units, operator names, or instrument results.
- Redact personal, clinical, credential, or confidential project data from outputs that will leave the authorized workspace.

## Output Contract

Return the saved record path, experiment identity, missing required fields, source manifest, run-manifest status when applicable, next decision gate, and unresolved ambiguities. The Markdown body must include Objective, Materials and samples, Procedure, Deviations, Observations, Results, Interpretation, Anomalies, and Next actions.
