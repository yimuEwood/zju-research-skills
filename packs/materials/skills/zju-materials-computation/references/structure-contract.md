# Structure input contract

The portable JSON form is:

```json
{
  "structure_id": "MAT-001",
  "lattice_angstrom": [[a1x, a1y, a1z], [a2x, a2y, a2z], [a3x, a3y, a3z]],
  "sites": [
    {"element": "Si", "fractional": [0, 0, 0], "occupancy": 1.0}
  ]
}
```

Lattice vectors are rows. Fractional coordinates may lie outside `[0, 1)` and are wrapped only for distance calculation; the original values remain in the source file. Occupancies must be in `(0, 1]`. More than 1,000 sites is rejected by the lightweight executor.

CIF and POSCAR parsing requires optional `pymatgen`. Parsing success does not resolve disorder, oxidation states, magnetic order, primitive/conventional-cell choice, or source credibility.
