"""Wreckfest 2 Auto Upgrader — entry point.

Run without arguments to open the GUI:

    python main.py

Headless commands (handy for scripting / testing):

    python main.py detect
    python main.py info <save.sgfi>
    python main.py copy <in.sgfi> <out.sgfi> <source_car> <target_cars...> [--vehicle <dir>]
"""

from __future__ import annotations

import sys

from wf2au import edit as edit_mod
from wf2au import paths as paths_mod
from wf2au import catalog as catalog_mod
from wf2au.bbag import SaveFile
from wf2au.cars import parse_cars, build_fitted_selection, slot_of


def cmd_detect():
    det = paths_mod.detect()
    for k, v in det.items():
        print(f"{k:12s}: {v or '(not found)'}")


def cmd_info(save_path):
    sf = SaveFile.parse(open(save_path, "rb").read())
    cars = parse_cars(sf.cars_chunk().decoded_payload)
    print(f"{len(cars)} cars")
    for c in cars:
        print(f"  {c.name:16s} [{c.config}]  {len(c.parts)} parts")


def cmd_copy(infile, outfile, source, targets, vehicle_dir=None):
    sf = SaveFile.parse(open(infile, "rb").read())
    cars = parse_cars(sf.cars_chunk().decoded_payload)
    if vehicle_dir:
        catalog = catalog_mod.build_catalog(vehicle_dir)
    else:
        catalog = {}
    sel = build_fitted_selection(cars)
    src = next((c for c in cars if c.name == source), None)
    if src is None:
        print(f"error: no car named '{source}'"); sys.exit(1)
    source_sel = sel[source]
    for tname in targets:
        tgt = next((c for c in cars if c.name == tname), None)
        if tgt is None:
            print(f"skip: no car named '{tname}'"); continue
        if catalog:
            avail = catalog.get(tgt.cardir, set()) | catalog.get("shared", set())
        else:
            avail = set().union(*(c.parts for c in cars))
        tsel, skipped = edit_mod.rewrite_for_target(source_sel, src.cardir, tgt.cardir, avail)
        sel[tname] = tsel
        print(f"  {tname}: {len(tsel)} parts" + (f", {len(skipped)} skipped" if skipped else ""))
    data, report = edit_mod.apply_selection(sf, sel, catalog)
    open(outfile, "wb").write(data)
    print(f"wrote {outfile} ({len(data)} bytes)")
    for name, ch, sk in report:
        print(f"  changed {name}: {ch} parts" + (f", {sk} skipped" if sk else ""))


def main():
    args = sys.argv[1:]
    if not args:
        from wf2au.gui import run
        run()
        return
    cmd = args[0]
    if cmd == "detect":
        cmd_detect()
    elif cmd == "info":
        cmd_info(args[1])
    elif cmd == "copy":
        vehicle = None
        rest = []
        i = 1
        while i < len(args):
            if args[i] == "--vehicle":
                vehicle = args[i + 1]
                i += 2
            else:
                rest.append(args[i])
                i += 1
        if len(rest) < 4:
            print("usage: copy <in> <out> <source_car> <target_car...> [--vehicle <dir>]")
            sys.exit(2)
        infile, outfile, source, targets = rest[0], rest[1], rest[2], rest[3:]
        cmd_copy(infile, outfile, source, targets, vehicle)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()