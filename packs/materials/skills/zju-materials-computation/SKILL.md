---
name: zju-materials-computation
description: Validate crystal structures and prepare traceable materials-computation handoffs from JSON, CIF, or POSCAR inputs. Use for lattice, volume, composition, density, periodic-distance, occupancy, structure-sanity, calculation-manifest, convergence, and provenance checks before DFT, molecular dynamics, phase, defect, interface, or property workflows. Use pymatgen when available for CIF or POSCAR parsing; do not infer a stable phase or material property from geometry checks alone.
---

# ZJU Materials Computation

Separate structure identity and deterministic geometry checks from claims that require a converged physical model.

## Workflow

1. Establish structure source, composition, charge/oxidation assumptions, disorder, magnetic state, temperature/pressure, target property, and computational objective.
2. Read `references/structure-contract.md`. For the portable JSON contract, run:

   `python scripts/analyze_structure.py --input structure.json --output structure-analysis.json`

   CIF and POSCAR inputs are accepted only when `pymatgen` is installed; record its version.
3. Inspect source hash, cell volume, lattice parameters, composition, density availability, partial occupancies, minimum periodic distance, and parser warnings. Resolve implausible contacts or ambiguous species before expensive calculations.
4. Read `references/calculation-handoff.md` and build a method-specific handoff for DFT, MD, phonon, defect, interface, or phase analysis. Record code/version, pseudopotential or force field, functional, cutoffs, k mesh, convergence, supercell, constraints, seeds, and environment.
5. Route statistical comparisons and figures through `$zju-statistics-audit` and `$zju-scientific-figure`, retaining structure and calculation IDs.

## Boundaries

- Geometry validation does not establish thermodynamic, kinetic, mechanical, electronic, or catalytic stability.
- Do not silently order disordered sites, assign oxidation states, repair occupancies, or choose magnetic configurations.
- Do not compare energies computed with incompatible settings.
- Preserve the supplied cell and coordinates; emit a new artifact for every transformation.

## Output

Return `structure-analysis.json`, unresolved structure issues, and a calculation handoff. State explicitly which conclusions require a domain simulator or experimental validation.
