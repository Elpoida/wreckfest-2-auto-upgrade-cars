"""Tkinter GUI for the Wreckfest 2 auto-upgrader.

Left:  car list (extended selection = pick targets for copying).
Right: the selected car's available parts, grouped by slot, one checkbox each.
       Checking a part marks it to be fitted in that slot (one per slot).
Buttons: copy the current car's checked parts to selected cars, select all/none,
         quick-pick the best available part per slot ("Racing package"), restore defaults.
Bottom: Back up & write the save (writes the Steam Cloud mirror too), plus a log.
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from . import catalog as catalog_mod
from . import edit as edit_mod
from . import paths as paths_mod
from .bbag import SaveFile
from .cars import editable_slots, parse_cars, slot_of

SLOT_ORDER = [
    "brakes", "clutch", "gearbox", "transmission", "suspension",
    "roll_bar/front", "roll_bar/rear", "engine/stock/parts/cooling",
    "engine/air_filter", "engine/camshaft", "engine/engine_management",
    "engine/exhaust_manifold", "engine/flywheel", "engine/fuel_system",
    "engine/ignition", "engine/intake_manifold", "engine/pistons",
    "engine/throttle_body", "engine/valves", "forced_induction",
]

PREFERENCE = {
    "engine/flywheel": ["prorace", "sport", "derby_heavy_duty", "stock"],
    "engine/air_filter": ["sport", "stock"],
    "engine/camshaft": ["competition", "sport", "stock"],
    "engine/engine_management": ["competition", "sport", "stock"],
    "engine/exhaust_manifold": ["competition", "sport", "stock"],
    "engine/fuel_system": ["competition", "sport", "stock"],
    "engine/ignition": ["competition", "sport", "stock"],
    "engine/intake_manifold": ["competition", "sport", "stock"],
    "engine/pistons": ["competition", "stock"],
    "engine/throttle_body": ["competition", "sport", "stock"],
    "brakes": ["adjustable_brakes_disc_14", "shared_brakes_disc_14",
               "shared_brakes_disc_12", "shared_brakes_disc_11",
               "shared_brakes_drum_14", "shared_brakes_drum_12", "shared_brakes_supervan"],
    "clutch": ["clutch_racing", "clutch_sport", "clutch_stock"],
    "gearbox": ["adjustable_full_6", "wide_5", "short_5", "adjustable_final_5", "stock_5"],
    "transmission": ["viscous_adjustable", "adjustable", "racing_locked", "stiff_1-way",
                     "rally_2-way", "stock_open"],
    "suspension": ["race_springs_stiff_dampers_adjustable", "race_springs_stiff_dampers",
                   "race_springs_comf_dampers", "street_springs_stiff_dampers",
                   "street_springs_comf_dampers", "stock"],
    "roll_bar/front": ["adjustable", "race", "sport", "stock", "soft", "unst"],
    "roll_bar/rear": ["adjustable", "race", "sport", "stock", "soft", "unst"],
    "engine/stock/parts/cooling": ["racing_high_flow_radiator", "derby_reinforced_radiator",
                                   "stock_radiator"],
}


def slot_sort_key(slot):
    if slot in SLOT_ORDER:
        return (0, SLOT_ORDER.index(slot), slot)
    return (1, 0, slot)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Wreckfest 2 Auto Upgrader")
        root.geometry("980x760")

        self.cars = []
        self.catalog = {}
        self.selection = {}
        self.dirty = set()
        self.car_vars = {}          # car_name -> {slot: [(part, BooleanVar)]}
        self.current_car = None
        self.current_car_sel = {}   # working copy of the displayed car's slot->part

        self._build_path_bar()
        self._build_main()
        self._build_action_bar()
        self._build_log()

        cfg = paths_mod.load_config()
        if cfg.get("save_path") or cfg.get("vehicle_dir"):
            self.auto_load(quiet=True)

    # ---------------------------------------------------------------- widgets

    def _build_path_bar(self):
        f = ttk.LabelFrame(self.root, text="Paths (auto-detected, or enter your own)")
        f.pack(fill="x", padx=8, pady=(8, 4))

        self.vehicle_var = tk.StringVar()
        self.save_var = tk.StringVar()
        self.mirror_var = tk.StringVar()

        row = ttk.Frame(f)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text="Wreckfest 2 folder:").pack(side="left")
        ttk.Entry(row, textvariable=self.vehicle_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._browse_vehicle).pack(side="left")
        ttk.Button(row, text="Auto-detect", command=self._detect).pack(side="left", padx=4)

        row = ttk.Frame(f)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text="profile.sgfi:").pack(side="left")
        ttk.Entry(row, textvariable=self.save_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._browse_save).pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text="Cloud mirror (optional):").pack(side="left")
        ttk.Entry(row, textvariable=self.mirror_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="Browse…", command=self._browse_mirror).pack(side="left")
        ttk.Button(row, text="Load save", command=self.load).pack(side="left", padx=8)

    def _build_main(self):
        pane = ttk.PanedWindow(self.root, orient="horizontal")
        pane.pack(fill="both", expand=True, padx=8, pady=4)

        left = ttk.Frame(pane)
        ttk.Label(left, text="Cars  (click to edit · ctrl/click to multi-select for copying)").pack(anchor="w")
        self.car_list = tk.Listbox(left, selectmode="extended", exportselection=False)
        self.car_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.car_list.yview)
        sb.pack(side="left", fill="y")
        self.car_list.configure(yscrollcommand=sb.set)
        self.car_list.bind("<<ListboxSelect>>", self._on_car_select)
        pane.add(left, weight=1)

        right = ttk.Frame(pane)
        self.canvas = tk.Canvas(right, highlightthickness=0)
        self.parts_frame = ttk.Frame(self.canvas)
        self.vsb = ttk.Scrollbar(right, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._win = self.canvas.create_window((0, 0), window=self.parts_frame, anchor="nw")
        self.parts_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._win, width=e.width))
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.canvas.bind(seq, self._on_mousewheel)
        pane.add(right, weight=3)

    def _bind_wheel(self, widget):
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(seq, self._on_mousewheel)
        for child in widget.winfo_children():
            self._bind_wheel(child)

    def _on_mousewheel(self, event):
        if event.num == 4 or (hasattr(event, "delta") and event.delta > 0):
            step = -1
        elif event.num == 5 or (hasattr(event, "delta") and event.delta < 0):
            step = 1
        else:
            return
        self.canvas.yview_scroll(step, "units")
        return "break"

    def _build_action_bar(self):
        f = ttk.Frame(self.root)
        f.pack(fill="x", padx=8, pady=4)
        ttk.Button(f, text="Racing package → all cars", command=self.racing_package).pack(side="left")
        ttk.Button(f, text="Copy current car → selected cars", command=self.copy_to_selected).pack(side="left", padx=4)
        ttk.Button(f, text="→ all cars", command=self.copy_to_all).pack(side="left", padx=4)
        ttk.Button(f, text="Restore car default", command=self.restore_car).pack(side="left", padx=4)
        self.status = ttk.Label(f, text="Not loaded")
        self.status.pack(side="right")

    def _build_log(self):
        f = ttk.LabelFrame(self.root, text="Log")
        f.pack(fill="both", expand=False, padx=8, pady=4)
        self.log = scrolledtext.ScrolledText(f, height=8, state="disabled")
        self.log.pack(fill="x", padx=4, pady=4)
        self.write_btn = ttk.Button(self.root, text="Back up & write save", command=self.write_save)
        self.write_btn.pack(fill="x", padx=8, pady=(0, 8))

    # ---------------------------------------------------------------- path helpers

    def _browse_vehicle(self):
        d = filedialog.askdirectory(title="Select the Wreckfest 2 folder (contains data/vehicle)")
        if d:
            self.vehicle_var.set(d)

    def _browse_save(self):
        f = filedialog.askopenfilename(title="Select profile.sgfi", filetypes=[("profile.sgfi", "*.sgfi")])
        if f:
            self.save_var.set(f)

    def _browse_mirror(self):
        f = filedialog.askopenfilename(title="Select the Steam Cloud mirror profile.sgfi", filetypes=[("profile.sgfi", "*.sgfi")])
        if f:
            self.mirror_var.set(f)

    def _detect(self):
        det = paths_mod.detect()
        self.vehicle_var.set(det["vehicle_dir"] or "")
        self.save_var.set(det["save_path"] or "")
        self.mirror_var.set(det["mirror_path"] or "")
        self.log_msg("Auto-detect:\n"
                     f"  install : {det['vehicle_dir']}\n"
                     f"  save    : {det['save_path']}\n"
                     f"  mirror  : {det['mirror_path']}")
        if det["save_path"]:
            self.load()

    # ---------------------------------------------------------------- load

    def _catalog_dir(self, vehicle):
        """Resolve the data/vehicle dir whether `vehicle` is the game root or already it."""
        if not vehicle:
            return None
        joined = os.path.join(vehicle, "data", "vehicle")
        if os.path.isdir(joined):
            return joined
        return vehicle

    def auto_load(self, quiet=False):
        det = paths_mod.detect()
        self.vehicle_var.set(det["vehicle_dir"] or "")
        self.save_var.set(det["save_path"] or "")
        self.mirror_var.set(det["mirror_path"] or "")
        if det["save_path"]:
            try:
                self.load()
            except Exception as e:
                if not quiet:
                    self.log_msg(f"Auto-load failed: {e}")

    def load(self):
        save_path = self.save_var.get().strip()
        if not save_path or not os.path.isfile(save_path):
            messagebox.showerror("Missing save", "Enter the path to your profile.sgfi (use Auto-detect).")
            return
        vehicle = self.vehicle_var.get().strip()
        mirror = self.mirror_var.get().strip() or None

        try:
            self.save_bytes = open(save_path, "rb").read()
            self.savefile = SaveFile.parse(self.save_bytes)
            self.cars = parse_cars(self.savefile.cars_chunk().decoded_payload)
        except Exception as e:
            messagebox.showerror("Load failed", f"Could not read the save:\n{e}")
            return

        self.catalog = catalog_mod.build_catalog(self._catalog_dir(vehicle)) if vehicle else {}
        if not self.catalog:
            self.log_msg("No parts catalog found (install folder missing) — showing parts "
                         "already referenced by the save.")
        self.selection = edit_mod.selection_defaults(self.cars)
        self.safe = editable_slots(self.cars)
        self.dirty = set()

        self.car_list.delete(0, "end")
        for c in self.cars:
            self.car_list.insert("end", c.name)

        paths_mod.save_config({
            "vehicle_dir": vehicle,
            "save_path": save_path,
            "mirror_path": mirror,
        })
        self.status.config(text=f"{len(self.cars)} cars loaded")
        self.log_msg(f"Loaded save: {save_path}\n"
                     f"{len(self.cars)} cars, catalog {'OK' if self.catalog else 'MISSING'}")
        self.car_list.selection_clear(0, "end")
        if self.cars:
            self.car_list.selection_set(0)
            self._on_car_select()

    # ---------------------------------------------------------------- car selection

    def _on_car_select(self, event=None):
        sel = self.car_list.curselection()
        if not sel:
            return
        name = self.car_list.get(sel[0])
        if name == self.current_car:
            return
        self.current_car = name
        self.current_car_sel = dict(self.selection.get(name, {}))
        self._render_parts(name)
        self.status.config(text=f"Editing: {name}")

    def available_parts(self, cardir):
        if self.catalog:
            return sorted(self.catalog.get(cardir, set()) | self.catalog.get("shared", set()))
        # fallback: everything referenced anywhere in the save
        all_parts = set()
        for c in self.cars:
            all_parts.update(c.parts)
        return sorted(all_parts)

    def _render_parts(self, name):
        for w in self.parts_frame.winfo_children():
            w.destroy()
        self.car_vars[name] = {}
        car = next(c for c in self.cars if c.name == name)
        avail = self.available_parts(car.cardir)

        by_slot = {}
        for pt in avail:
            slot = slot_of(pt, car.cardir)
            if slot and slot in self.safe:
                by_slot.setdefault(slot, []).append(pt)
        if not by_slot:
            ttk.Label(self.parts_frame, text="  (no editable parts listed — install folder not found?)").pack(anchor="w")

        for slot in sorted(by_slot, key=slot_sort_key):
            label = ttk.Label(self.parts_frame, text=f"  ▸ {slot}", font=("", 9, "bold"))
            label.pack(anchor="w", pady=(6, 0))
            self.car_vars[name][slot] = []
            for pt in sorted(by_slot[slot]):
                text = pt.rsplit("/", 1)[-1][:-5]
                if pt.startswith("data/vehicle/shared/"):
                    text += "  (shared)"
                var = tk.BooleanVar(value=(self.current_car_sel.get(slot) == pt))
                cb = ttk.Checkbutton(
                    self.parts_frame,
                    text=text,
                    variable=var,
                    command=lambda s=slot, p=pt, v=var, n=name: self._on_toggle(n, s, p, v))
                cb.pack(anchor="w", padx=18)
                self.car_vars[name][slot].append((pt, var))
        self._bind_wheel(self.parts_frame)

    def _on_toggle(self, car, slot, part, var):
        if var.get():
            for other, other_var in self.car_vars[car].get(slot, []):
                if other != part and other_var.get():
                    other_var.set(False)
            self.current_car_sel[slot] = part
        else:
            if self.current_car_sel.get(slot) == part:
                self.current_car_sel[slot] = None
        self.selection[car] = dict(self.current_car_sel)
        self.dirty.add(car)

    # ---------------------------------------------------------------- actions

    def _target_names(self):
        sel = self.car_list.curselection()
        return [self.car_list.get(i) for i in sel]

    def copy_to_selected(self):
        targets = [t for t in self._target_names() if t != self.current_car]
        if self.current_car is None:
            return
        if not targets:
            messagebox.showinfo("No targets", "Select the cars to copy to (ctrl/click in the list).")
            return
        self._copy_cur_to(targets)

    def copy_to_all(self):
        if self.current_car is None:
            return
        self._copy_cur_to([c.name for c in self.cars if c.name != self.current_car])

    def _copy_cur_to(self, targets):
        src = next(c for c in self.cars if c.name == self.current_car)
        copied, skipped = 0, 0
        for tname in targets:
            tgt = next(c for c in self.cars if c.name == tname)
            if not self.catalog:
                avail = set().union(*(c.parts for c in self.cars))
            else:
                avail = self.catalog.get(tgt.cardir, set()) | self.catalog.get("shared", set())
            sel, skip = edit_mod.rewrite_for_target(self.current_car_sel, src.cardir, tgt.cardir, avail)
            if sel:
                self.selection[tname] = sel
                self.dirty.add(tname)
            copied += len(sel)
            skipped += len(skip)
            self.log_msg(f"  {tname}: {len(sel)} part(s) set"
                         + (f", {len(skip)} skipped (not available)" if skip else ""))
        self.status.config(text=f"Copied to {len(targets)} car(s)")
        self.log_msg(f"Copied '{self.current_car}' selection to {len(targets)} cars "
                     f"({copied} parts, {skipped} skipped).")

    def restore_car(self):
        if not self.current_car:
            return
        car = next(c for c in self.cars if c.name == self.current_car)
        sel = {}
        for pt in car.parts:
            slot = slot_of(pt, car.cardir)
            if slot:
                sel[slot] = pt
        self.selection[self.current_car] = sel
        self.dirty.discard(self.current_car)
        self.current_car_sel = dict(sel)
        self._render_parts(self.current_car)
        self.log_msg(f"Restored '{self.current_car}' to its currently fitted parts.")

    def racing_package(self):
        """Apply the best available part per editable slot to EVERY car."""
        if not self.cars:
            return
        total_slots = 0
        for car in self.cars:
            avail = self.available_parts(car.cardir)
            by_slot = {}
            for pt in avail:
                slot = slot_of(pt, car.cardir)
                if slot and slot in self.safe:
                    by_slot.setdefault(slot, []).append(pt)
            sel = dict(self.selection.get(car.name, {}))
            for slot, paths in by_slot.items():
                best = self._best_part(slot, paths)
                if best:
                    sel[slot] = best
                    total_slots += 1
            self.selection[car.name] = sel
            self.dirty.add(car.name)
        self.log_msg(f"Racing package applied to all {len(self.cars)} cars ({total_slots} part slots set).")
        if self.current_car:
            self.current_car_sel = dict(self.selection[self.current_car])
            self._render_parts(self.current_car)

    @staticmethod
    def _best_part(slot, paths):
        pref = PREFERENCE.get(slot)
        if not pref:
            return None
        names = {p.rsplit("/", 1)[-1][:-5]: p for p in paths}
        for key in pref:
            for nm, path in names.items():
                if key in nm:
                    return path
        return None

    # ---------------------------------------------------------------- write

    def write_save(self):
        if not getattr(self, "savefile", None):
            messagebox.showerror("Nothing loaded", "Load a save first.")
            return
        save_path = self.save_var.get().strip()
        mirror = self.mirror_var.get().strip() or None
        try:
            data, report = edit_mod.apply_selection(self.savefile, self.selection, self.catalog)
            problems = edit_mod.verify_save(data)
            if problems:
                self.log_msg("Integrity check failed — NOT writing:\n" + "\n".join(problems))
                messagebox.showerror("Write aborted", "Integrity check failed:\n" + "\n".join(problems))
                return
            backup_dir, verify = edit_mod.write_save(data, save_path, mirror)
            self.log_msg(f"Wrote save. Backup: {backup_dir}")
            if verify:
                self.log_msg("WARN: " + "\n".join(verify))
            changed = sum(1 for _ in report)
            self.log_msg(f"Updated {changed} car(s):")
            for name, ch, sk in report:
                self.log_msg(f"  {name}: {ch} part(s) changed" + (f", {sk} skipped" if sk else ""))
            messagebox.showinfo("Done", "Done — save updated!\n\n"
                                        "Both the real save and the Steam Cloud mirror were written.\n\n"
                                        "Backups saved to:\n"
                                        f"{backup_dir}\n\n"
                                        "Start Wreckfest 2 and check your cars.")
        except Exception as e:
            self.log_msg(f"Write failed: {e}")
            messagebox.showerror("Write failed", str(e))

    # ---------------------------------------------------------------- log

    def log_msg(self, msg):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def run():
    root = tk.Tk()
    App(root)
    root.mainloop()