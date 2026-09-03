"""Installable-parts catalog from the game's loose `.upgr` files under data/vehicle."""

from __future__ import annotations

import os

SHARED = "shared"


def build_catalog(vehicle_dir):
    """Return {cardir: set of 'data/vehicle/...' paths}, plus 'shared' merged into every car's set.

    The returned structure is a dict cardir -> set of fully-qualified part paths that the car
    can fit (its own parts plus the universal `shared` parts).
    """
    raw = {}
    if not vehicle_dir or not os.path.isdir(vehicle_dir):
        return {}
    for cardir in sorted(os.listdir(vehicle_dir)):
        base = os.path.join(vehicle_dir, cardir)
        if not os.path.isdir(base):
            continue
        s = set()
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".upgr"):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, vehicle_dir).replace(os.sep, "/")
                    s.add("data/vehicle/" + rel)
        raw[cardir] = s

    shared = raw.get(SHARED, set())
    catalog = {}
    for cardir, s in raw.items():
        if cardir == SHARED:
            continue
        catalog[cardir] = s | shared
    catalog[SHARED] = shared
    return catalog


def parts_for_car(catalog, cardir):
    """Sorted list of available part paths for a car (own + shared)."""
    return sorted(catalog.get(cardir, set()) | catalog.get(SHARED, set()))