"""Parse the cars + their fitted parts out of the decoded `srcc` chunk payload.

Car records in the payload look like:

    nart [u32 2][u32 1]   str vehicleKey   str displayName   str config
    sspu [1][1]           frpu [1][count]  count × (rgpu [len][path])
    sspu [1][1]           frpu [1][count2] count2 × (rgpu [len][path])   (spares, usually 0)
    ... presets / stats ...
"""

from __future__ import annotations

from .lz4 import read_u32

CAR_TAG = b"nart"
SSPU_TAG = b"sspu"
FRPU_TAG = b"frpu"
RGPU_TAG = b"rgpu"
VEHICLE_KEY_PREFIX = "VEHICLE_NAME_"
MAX_STRING = 4096

# Single-slot categories that are cosmetic rather than performance. Excluded from the editable
# set so the tool only offers performance options. New *non-cosmetic* categories added by game
# updates are still picked up automatically (see editable_slots).
COSMETIC_SLOTS = {
    "body", "bumper_front", "bumper_rear", "cover_front", "exhaust", "grille",
    "hood", "lights", "livery", "roll_cage", "seat", "side_protector",
    "spoiler_front", "spoiler_rear", "steering_wheel", "trunk",
}


def safe_slots(cars):
    """Slots that take at most ONE fitted part per car — the safe things to auto-swap.

    Derived from the save itself rather than a hardcoded list, so future part categories
    added by game updates become editable automatically. A slot holding multiple parts at
    once (windows, doors, fenders, the temp driver/seat/steering cluster, …) is excluded.
    """
    mx = {}
    for c in cars:
        per = {}
        for pt in c.parts:
            slot = slot_of(pt, c.cardir)
            if slot:
                per[slot] = per.get(slot, 0) + 1
        for slot, n in per.items():
            mx[slot] = max(mx.get(slot, 0), n)
    return {s for s, n in mx.items() if n == 1}


def editable_slots(cars):
    """safe_slots minus the cosmetic categories — the parts the tool actually offers."""
    return safe_slots(cars) - COSMETIC_SLOTS


def is_editable_slot(slot, safe=None):
    return safe is None or slot in safe


class Car:
    def __init__(self, offset, end, vehicle_key, name, config, parts, parts_start, second_sspu):
        self.offset = offset
        self.end = end
        self.vehicle_key = vehicle_key
        self.name = name
        self.config = config
        self.parts = parts
        self.parts_start = parts_start      # offset of the first `frpu` tag
        self.second_sspu = second_sspu      # offset just past the fitted-part entries

    @property
    def cardir(self):
        return self.config.split(":")[0]

    def __repr__(self):
        return f"<Car {self.name} [{self.config}] {len(self.parts)} parts>"


def tag_at(d, p, tag):
    return p >= 0 and p + 4 <= len(d) and d[p:p + 4] == tag


def _read_string(d, p, end):
    if p < 0 or p + 4 > end:
        raise ValueError("truncated string header")
    length = read_u32(d, p)
    if length > MAX_STRING or p + 4 + length > end:
        raise ValueError("implausible string length")
    return d[p + 4:p + 4 + length].decode("latin1"), p + 4 + length


def parse_cars(payload):
    """Return the list of Car records from the full decoded cars payload."""
    starts = []
    for i in range(len(payload) - 12):
        if not tag_at(payload, i, CAR_TAG):
            continue
        if read_u32(payload, i + 4) != 2 or read_u32(payload, i + 8) != 1:
            continue
        try:
            key, _ = _read_string(payload, i + 12, len(payload))
        except ValueError:
            continue
        if key and key.startswith(VEHICLE_KEY_PREFIX):
            starts.append(i)

    cars = []
    for k, start in enumerate(starts):
        end = starts[k + 1] if k + 1 < len(starts) else len(payload)
        cars.append(_parse_car(payload, start, end))
    return cars


def _parse_car(d, start, end):
    p = start + 12
    vehicle_key, p = _read_string(d, p, end)
    name, p = _read_string(d, p, end)
    config, p = _read_string(d, p, end)
    if not tag_at(d, p, SSPU_TAG):
        raise ValueError(f"car {name}: expected sspu after config")
    p += 12
    if not tag_at(d, p, FRPU_TAG):
        raise ValueError(f"car {name}: expected frpu after sspu")
    parts_start = p
    count = read_u32(d, p + 8)
    p += 12
    parts = []
    for _ in range(count):
        if not tag_at(d, p, RGPU_TAG):
            raise ValueError(f"car {name}: expected rgpu entry at 0x{p:x}")
        length = read_u32(d, p + 4)
        parts.append(d[p + 8:p + 8 + length].decode("latin1"))
        p += 8 + length
    if not tag_at(d, p, SSPU_TAG):
        raise ValueError(f"car {name}: expected second sspu after fitted parts")
    return Car(start, end, vehicle_key, name, config, parts, parts_start, p)


def make_frpu(paths):
    """Encode a fitted-part section: frpu [1][count] count × (rgpu [len][path])."""
    body = bytearray()
    for path in paths:
        b = path.encode("latin1")
        body += RGPU_TAG
        body += len(b).to_bytes(4, "little")
        body += b
    return (FRPU_TAG + (1).to_bytes(4, "little") + len(paths).to_bytes(4, "little")
            + bytes(body))


def slot_of(path, cardir):
    """Canonical slot for a fitted part path, e.g. 'engine/camshaft', 'roll_bar/front', 'brakes'."""
    prefix = f"data/vehicle/{cardir}/part/"
    shared = "data/vehicle/shared/part/"
    rel = None
    if path.startswith(prefix):
        rel = path[len(prefix):]
    elif path.startswith(shared):
        rel = path[len(shared):]
    if not rel:
        return None
    if "/" in rel:
        rel_dir, rel_file = rel.rsplit("/", 1)
    else:
        rel_dir, rel_file = "", rel
    if rel_file.startswith("front_antiroll_bar_") or rel_file.startswith("roll_bar_front_"):
        return "roll_bar/front"
    if rel_file.startswith("rear_antiroll_bar_") or rel_file.startswith("roll_bar_rear_"):
        return "roll_bar/rear"
    if rel_dir == "forced_induction" or rel_dir.endswith("/forced_induction"):
        return "forced_induction"
    return rel_dir if rel_dir else rel_file


def build_fitted_selection(cars, editable=None):
    """Default per-car selection: editable slot -> currently fitted path (nothing changes until
    the user edits). Editable = single-instance non-cosmetic categories detected from the save."""
    if editable is None:
        editable = editable_slots(cars)
    selection = {}
    for c in cars:
        sel = {}
        for pt in c.parts:
            slot = slot_of(pt, c.cardir)
            if slot and slot in editable:
                sel[slot] = pt
        selection[c.name] = sel
    return selection