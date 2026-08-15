#!/usr/bin/env python3
"""Deterministic crystal-cell and periodic-distance validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from itertools import product
from pathlib import Path

import numpy as np


VERSION = "1.0"
AVOGADRO = 6.02214076e23
ANGSTROM3_TO_CM3 = 1e-24
ATOMIC_WEIGHTS = {
    "H": 1.008, "Li": 6.94, "B": 10.81, "C": 12.011, "N": 14.007, "O": 15.999,
    "F": 18.998403, "Na": 22.989769, "Mg": 24.305, "Al": 26.981538, "Si": 28.085,
    "P": 30.973762, "S": 32.06, "Cl": 35.45, "K": 39.0983, "Ca": 40.078,
    "Ti": 47.867, "V": 50.9415, "Cr": 51.9961, "Mn": 54.938044, "Fe": 55.845,
    "Co": 58.933194, "Ni": 58.6934, "Cu": 63.546, "Zn": 65.38, "Ga": 69.723,
    "Ge": 72.630, "As": 74.921595, "Se": 78.971, "Br": 79.904, "Sr": 87.62,
    "Zr": 91.224, "Nb": 92.90637, "Mo": 95.95, "Ru": 101.07, "Rh": 102.9055,
    "Pd": 106.42, "Ag": 107.8682, "Cd": 112.414, "In": 114.818, "Sn": 118.710,
    "Sb": 121.760, "Te": 127.60, "I": 126.90447, "Ba": 137.327, "La": 138.90547,
    "Ce": 140.116, "W": 183.84, "Pt": 195.084, "Au": 196.96657, "Pb": 207.2,
}


class StructureInputError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _from_pymatgen(path: Path) -> tuple[dict, str]:
    try:
        import pymatgen.core
        from pymatgen.core import Structure
    except ImportError as exc:
        raise StructureInputError("CIF/POSCAR input requires optional pymatgen") from exc
    try:
        structure = Structure.from_file(str(path))
    except Exception as exc:
        raise StructureInputError(f"pymatgen could not parse {path}: {exc}") from exc
    sites = []
    for site in structure:
        for species, occupancy in site.species.items():
            sites.append({
                "element": species.symbol,
                "fractional": [float(value) for value in site.frac_coords],
                "occupancy": float(occupancy),
            })
    version = getattr(pymatgen.core, "__version__", "unknown")
    return {
        "structure_id": path.stem,
        "lattice_angstrom": structure.lattice.matrix.tolist(),
        "sites": sites,
    }, f"pymatgen-{version}"


def _load(path: Path) -> tuple[dict, str]:
    if path.suffix.lower() == ".json":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StructureInputError(f"invalid structure JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise StructureInputError("structure JSON must be an object")
        return value, "native-json"
    return _from_pymatgen(path)


def _angle(a: np.ndarray, b: np.ndarray) -> float:
    denominator = np.linalg.norm(a) * np.linalg.norm(b)
    if denominator == 0:
        raise StructureInputError("lattice vectors must be non-zero")
    cosine = float(np.clip(np.dot(a, b) / denominator, -1.0, 1.0))
    return math.degrees(math.acos(cosine))


def _formula(composition: dict[str, float]) -> str:
    order = sorted(composition)
    if "C" in composition:
        order = ["C"] + (["H"] if "H" in composition else []) + [key for key in order if key not in {"C", "H"}]
    parts = []
    for element in order:
        amount = composition[element]
        rounded = round(amount)
        suffix = "" if math.isclose(amount, 1.0) else str(int(rounded) if math.isclose(amount, rounded) else round(amount, 4))
        parts.append(f"{element}{suffix}")
    return "".join(parts)


def _minimum_image_distance(
    delta: np.ndarray, lattice: np.ndarray, *, exclude_origin: bool
) -> float:
    """Return the exact nearest-image distance for a well-conditioned 3D cell.

    Fractional coordinates may be outside [0, 1).  A first local search gives
    an upper bound; the smallest lattice singular value then gives a finite
    integer box containing every translation that could improve that bound.
    """
    singular_values = np.linalg.svd(lattice, compute_uv=False)
    sigma_min = float(singular_values.min())
    if sigma_min < 1e-8 or float(singular_values.max() / sigma_min) > 1e8:
        raise StructureInputError("lattice is too ill-conditioned for a reliable periodic-distance search")
    base = -np.rint(delta).astype(int)
    best = math.inf
    for offset in product((-1, 0, 1), repeat=3):
        translation = base + np.asarray(offset, dtype=int)
        if exclude_origin and np.all(translation == 0):
            continue
        best = min(best, float(np.linalg.norm((delta + translation) @ lattice)))
    if not math.isfinite(best):
        raise StructureInputError("could not establish a periodic-distance bound")
    radius = best / sigma_min + 1e-12
    bounds = [
        range(math.ceil(-float(delta[index]) - radius), math.floor(-float(delta[index]) + radius) + 1)
        for index in range(3)
    ]
    candidates = math.prod(len(values) for values in bounds)
    if candidates > 2_000_000:
        raise StructureInputError("periodic-distance search is too large for this highly skewed cell")
    for translation_tuple in product(*bounds):
        translation = np.asarray(translation_tuple, dtype=int)
        if exclude_origin and np.all(translation == 0):
            continue
        best = min(best, float(np.linalg.norm((delta + translation) @ lattice)))
    return best


def analyze(path: Path) -> dict:
    raw, parser = _load(path)
    try:
        lattice = np.asarray(raw["lattice_angstrom"], dtype=float)
        sites = list(raw["sites"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StructureInputError("structure requires lattice_angstrom and sites") from exc
    if lattice.shape != (3, 3) or not np.isfinite(lattice).all():
        raise StructureInputError("lattice_angstrom must be a finite 3x3 matrix")
    determinant = float(np.linalg.det(lattice))
    volume = abs(determinant)
    if volume < 1e-6:
        raise StructureInputError("lattice is singular or has negligible volume")
    if not sites or len(sites) > 1000:
        raise StructureInputError("sites must contain between 1 and 1000 entries")

    composition: dict[str, float] = {}
    site_entries = []
    warnings = []
    partial = False
    for index, site in enumerate(sites):
        try:
            element = str(site["element"])
            coordinate = np.asarray(site["fractional"], dtype=float)
            occupancy = float(site.get("occupancy", 1.0))
        except (KeyError, TypeError, ValueError) as exc:
            raise StructureInputError(f"invalid site at index {index}") from exc
        if element not in ATOMIC_WEIGHTS and not element.isalpha():
            raise StructureInputError(f"invalid element token at site {index}: {element}")
        if coordinate.shape != (3,) or not np.isfinite(coordinate).all():
            raise StructureInputError(f"site {index} fractional coordinate must contain three finite numbers")
        if not (0 < occupancy <= 1):
            raise StructureInputError(f"site {index} occupancy must be in (0, 1]")
        partial = partial or occupancy < 1.0
        composition[element] = composition.get(element, 0.0) + occupancy
        wrapped = np.mod(coordinate, 1.0)
        wrapped[np.isclose(wrapped, 1.0, atol=1e-10)] = 0.0
        site_entries.append({
            "source_index": index,
            "element": element,
            "occupancy": occupancy,
            "fractional": wrapped,
        })
    if partial:
        warnings.append("partial occupancy present; no ordering was inferred")

    geometric_sites: list[dict] = []
    for entry in site_entries:
        matched = None
        for site in geometric_sites:
            difference = entry["fractional"] - site["fractional"]
            difference -= np.rint(difference)
            if np.max(np.abs(difference)) < 1e-8:
                matched = site
                break
        if matched is None:
            geometric_sites.append({
                "fractional": entry["fractional"],
                "source_indices": [entry["source_index"]],
                "occupancy_sum": entry["occupancy"],
            })
        else:
            matched["source_indices"].append(entry["source_index"])
            matched["occupancy_sum"] += entry["occupancy"]
            if matched["occupancy_sum"] > 1.0 + 1e-8:
                raise StructureInputError(
                    "co-located site occupancies exceed one; inspect duplicate or malformed disordered sites"
                )
    if any(len(site["source_indices"]) > 1 for site in geometric_sites):
        warnings.append("co-occupied site entries were consolidated for geometric distance checks")

    minimum = math.inf
    minimum_pair = None
    for i, first in enumerate(geometric_sites):
        for j in range(i, len(geometric_sites)):
            delta = geometric_sites[j]["fractional"] - first["fractional"]
            candidate = _minimum_image_distance(delta, lattice, exclude_origin=i == j)
            if candidate < minimum:
                minimum = candidate
                minimum_pair = [first["source_indices"][0], geometric_sites[j]["source_indices"][0]]
    if minimum < 0.5:
        warnings.append("minimum periodic distance is below 0.5 angstrom; inspect duplicate or implausible sites")

    lengths = [float(np.linalg.norm(vector)) for vector in lattice]
    angles = [_angle(lattice[1], lattice[2]), _angle(lattice[0], lattice[2]), _angle(lattice[0], lattice[1])]
    unknown = sorted(element for element in composition if element not in ATOMIC_WEIGHTS)
    density = None
    if unknown:
        warnings.append(f"density unavailable; atomic weights missing for: {', '.join(unknown)}")
    else:
        mass_g_mol = sum(ATOMIC_WEIGHTS[element] * amount for element, amount in composition.items())
        density = mass_g_mol / AVOGADRO / (volume * ANGSTROM3_TO_CM3)

    return {
        "schema_version": "1.0",
        "executor": {"name": "analyze_structure", "version": VERSION, "parser": parser},
        "source": {"path": str(path), "sha256": _sha256(path)},
        "structure_id": str(raw.get("structure_id") or path.stem),
        "cell": {
            "lattice_angstrom": lattice.tolist(),
            "lengths_angstrom": [round(value, 10) for value in lengths],
            "angles_degrees": [round(value, 10) for value in angles],
            "volume_angstrom3": round(volume, 10),
            "handedness": "right" if determinant > 0 else "left",
        },
        "composition": {
            "formula": _formula(composition),
            "amounts": composition,
            "site_entries": len(sites),
            "geometric_sites": len(geometric_sites),
        },
        "density_g_cm3": None if density is None else round(float(density), 10),
        "periodic_distance_check": {
            "minimum_distance_angstrom": round(minimum, 10),
            "site_indices": minimum_pair,
            "search_images": "adaptive exact integer-lattice enumeration",
        },
        "warnings": warnings,
        "scientific_claim_status": "geometry_check_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        result = analyze(Path(args.input))
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, StructureInputError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
