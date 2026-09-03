"""Apply per-car part selections to a save, and write it out with backup + verification.

A "selection" is {car_name: {slot: part_path}} where slot is the canonical category
(see cars.slot_of). The part_path may be the car's own path or a shared path. When copying a
selection from one car to another, car-specific paths are rewritten for the target and checked
against the target's catalog.
"""

from __future__ import annotations

import os
import shutil
import time

from .bbag import SaveFile
from .cars import editable_slots, parse_cars, make_frpu, slot_of

BACKUP_ROOT = os.path.join(os.path.expanduser("~"), ".wreckfest2autoupgrader_backups")


def apply_selection(savefile, selection, catalog):
    """Rewrite the save in memory. Returns (serialized_bytes, report_list)."""
    chunk = savefile.cars_chunk()
    if chunk is None:
        raise ValueError("this save has no cars chunk")
    payload = chunk.decoded_payload
    cars = parse_cars(payload)

    shared = catalog.get("shared", set())
    out = bytearray()
    pos = 0
    report = []
    for c in cars:
        out += payload[pos:c.offset]
        sel = selection.get(c.name)
        if sel:
            avail = catalog.get(c.cardir, set()) | shared
            new_parts, changed, skipped = apply_to_car(c.parts, sel, c.cardir, avail)
            record = (bytearray(payload[c.offset:c.parts_start])
                      + make_frpu(new_parts)
                      + bytearray(payload[c.second_sspu:c.end]))
            out += record
            if changed or skipped:
                report.append((c.name, changed, skipped))
        else:
            out += payload[c.offset:c.end]
        pos = c.end
    out += payload[pos:]

    chunk.set_decoded_payload(bytes(out))
    return savefile.serialize(), report


def apply_to_car(fitted, sel, cardir, avail):
    """Build the new fitted list for one car. Returns (paths, changed_count, skipped_count)."""
    new = []
    present = set()
    changed = skipped = 0
    for pt in fitted:
        slot = slot_of(pt, cardir)
        target = sel.get(slot) if slot else None
        if target and target != pt:
            if target in avail:
                new.append(target)
                changed += 1
                present.add(slot)
            else:
                skipped += 1
                new.append(pt)
                present.add(slot)
        else:
            new.append(pt)
            if slot:
                present.add(slot)
    for slot, target in sel.items():
        if slot in present or not target:
            continue
        if target in avail:
            new.append(target)
            changed += 1
        else:
            skipped += 1
    return new, changed, skipped


def rewrite_for_target(sel, source_cardir, target_cardir, avail):
    """Copy a selection onto another car: rewrite car-specific paths and drop unavailable parts.

    Returns ({slot: path}, skipped).
    """
    out = {}
    skipped = []
    for slot, path in sel.items():
        if not path:
            continue
        src_prefix = f"data/vehicle/{source_cardir}/part/"
        if path.startswith(src_prefix):
            target = f"data/vehicle/{target_cardir}/part/" + path[len(src_prefix):]
        elif path.startswith("data/vehicle/shared/part/"):
            target = path
        else:
            continue
        if target in avail:
            out[slot] = target
            continue
        # Drivetrain-aware transmission swap (rwd_<->fwd_): the source diff may be RWD while the
        # target is FWD (or vice-versa) — offer the target's matching variant instead.
        if slot == "transmission" and "/transmission/" in target:
            base = target.rsplit("/", 1)[-1]
            swapped = base.replace("rwd_", "fwd_", 1) if base.startswith("rwd_") else \
                      (base.replace("fwd_", "rwd_", 1) if base.startswith("fwd_") else None)
            if swapped:
                alt = target.rsplit("/", 1)[0] + "/" + swapped
                if alt in avail:
                    out[slot] = alt
                    continue
        skipped.append((slot, path))
    return out, skipped


def selection_defaults(cars):
    """{car_name: {editable slot: currently-fitted path}} — nothing changes until the user edits.

    Only non-cosmetic single-instance slots are included, so cosmetic parts and multi-part
    categories (windows, doors, temp…) can never be clobbered by a write.
    """
    editable = editable_slots(cars)
    sel = {}
    for c in cars:
        s = {}
        for pt in c.parts:
            slot = slot_of(pt, c.cardir)
            if slot and slot in editable:
                s[slot] = pt
        sel[c.name] = s
    return sel


def verify_save(data):
    """Re-parse and confirm integrity. Returns list of problems (empty = all good)."""
    problems = []
    sf = SaveFile.parse(data)
    for c in sf.chunks:
        if not c.stored_crc_valid:
            problems.append(f"chunk '{c.tag}' CRC invalid")
        from .lz4 import crc32c
        if crc32c(c.container.content) != c.container.expected_crc:
            problems.append(f"chunk '{c.tag}' container CRC invalid")
    parse_cars(sf.cars_chunk().decoded_payload) if sf.cars_chunk() else None
    return problems


def write_save(data, primary, mirror=None, backup=True):
    """Back up, then write the same bytes to the primary save and (if set) the cloud mirror.

    Returns the backup directory used.
    """
    if not primary:
        raise ValueError("no primary save path given")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(backup_dir, exist_ok=True)

    for label, path in (("primary", primary), ("mirror", mirror)):
        if path and os.path.isfile(path) and backup:
            dst = os.path.join(backup_dir, f"{label}_profile.sgfi")
            shutil.copy2(path, dst)

    with open(primary, "wb") as f:
        f.write(data)
    if mirror:
        os.makedirs(os.path.dirname(mirror), exist_ok=True)
        with open(mirror, "wb") as f:
            f.write(data)

    problems = verify_save(data)
    return backup_dir, problems