# Chemical identity and evidence contract

Identity fields:

- supplied name/structure;
- normalized name;
- molecular formula and charge;
- connectivity and stereochemistry;
- isotope, tautomer/protomer, salt, solvate, and mixture state;
- InChI/InChIKey and canonical/isomeric SMILES;
- verified database identifiers and source;
- ambiguity/status.

Evidence records need `record_id`, `entity_id`, property/endpoint, value, relation operator, unit, conditions, method/assay, evidence type, source database, primary source identifier, source anchor, date/version, and uncertainty.

Never convert or compare units until basis and conditions agree. Preserve qualifiers such as `<`, `>`, approximately, below detection, racemate, wet/dry basis, temperature, pressure, pH, solvent, and crystal form.
