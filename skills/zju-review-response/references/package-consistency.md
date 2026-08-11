# Revision package consistency

Check in this order:

1. Every editor/reviewer item appears exactly once in the master tracker.
2. Every response letter preserves the correct reviewer boundary and comment wording.
3. Every claimed manuscript change exists in the clean and redline versions.
4. Quoted revised text matches the manuscript verbatim.
5. Locations remain correct after pagination or edits.
6. Numbers, directions, significance, figure/table IDs, citations, and terminology agree across all files and with the canonical result registry.
7. `AUTHOR_INPUT_NEEDED`, `LOCATION_PENDING`, and open blockers are visible in the internal tracker and excluded from a falsely ready package.
8. The cover letter summarizes verified changes without adding promises absent from the tracker.

Any manuscript edit invalidates prior quote and location checks. Re-run the audit after the final edit.

For each closed item, retain the chain `concern_id → action_id → evidence_ids/result_ids → manuscript_diff → location`. For new analyses, also check Methods, analysis population, diagnostics/sensitivity reporting, source-data/table/figure artifacts, and the data-availability inventory.
