# Calculation handoff

Record at minimum:

- immutable structure ID and file hash;
- transformation history and symmetry tolerance;
- code, exact version, input files, pseudopotentials or force field;
- exchange-correlation functional and dispersion treatment where relevant;
- basis/cutoff, k-point sampling, smearing, spin and charge;
- relaxation constraints and force/energy/stress thresholds;
- supercell, defect charge and correction scheme when relevant;
- convergence study and failure logs;
- hardware/runtime environment and random seeds;
- requested outputs and validators.

Do not merge values from different calculation settings into one result family without an explicit comparability analysis.
