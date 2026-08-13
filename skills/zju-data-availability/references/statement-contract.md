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

Before assembling a release directory, run `python scripts/build_release_manifest.py inventory.json --output release-manifest.json`. Resolve relative `current_location` or `location` values from the inventory directory. A successful file check records the byte size and SHA-256 digest; a missing or unspecified file, or a mismatch with an inventory `sha256`, remains in `local_unresolved` and makes `local_package_ready` false. A missing or unverified route-required identifier/license remains in `publication_unresolved` and makes `publication_release_ready` false.

For a verified identifier or license, the inventory must include an `<field>_verification` object with `status: verified`, `checked_at`, `source`, and optionally the exact `value`. This records the verification assertion; the offline script does not perform a live registry check. The legacy `release_ready` field is retained as a deprecated alias of the stricter `publication_release_ready`, not the local-file result. CLI exit status defaults to local-package readiness; add `--require-publication-ready` when publication readiness is the gate. A checksum proves only which local bytes were inspected. It does not verify a remote deposit, DOI, accession, license, FAIRness, or computational reproducibility.
