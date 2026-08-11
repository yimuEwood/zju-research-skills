# Availability statement contract

For every artifact state:

- what it contains and which claims/figures it supports;
- which analysis IDs and result IDs it supports or regenerates;
- where it is available;
- exact identifier and version, or `PENDING`;
- access level and license;
- embargo or controlled-access procedure;
- third-party or ethics restriction basis;
- code/runtime dependencies needed to use it.

Use resolvable persistent identifiers only after verification. Cite datasets and software as research outputs, not only as URLs. Keep repository landing pages distinct from direct file links.

An honest incomplete statement with explicit pending fields is preferable to a fluent but false claim of availability.

For computational results, inventory the raw/processed inputs, analysis contract, executable code/notebook, environment lock, canonical result registry, figure/table source data, and expected outputs as distinct artifacts linked by `derived_from`. A reproducibility package should name its entrypoint, environment artifact, expected result IDs, and whether verification was `not_run`, `passed`, `partial`, or `failed`; file presence alone is not a successful rerun.
