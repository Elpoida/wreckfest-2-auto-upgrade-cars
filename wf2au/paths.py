"""Locate the Wreckfest 2 install, the career save and the Steam Cloud mirror.

Auto-detection is OS-aware (Windows native + Linux/Steam Proton) and reads
`steamapps/libraryfolders.vdf` so games installed on secondary drives are found too.
Manual paths always win; last-used paths are remembered in a small JSON config.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys

APPID = "1203190"
GAME_DIR = "Wreckfest 2"
SAVE_REL = os.path.join("savegame", "profile.sgfi")
MYGAMES_REL = os.path.join("Documents", "My Games", "Wreckfest 2")
COMPAT_SAVE_REL = os.path.join(
    "steamapps", "compatdata", APPID, "pfx", "drive_c", "users", "steamuser",
    "Documents", "My Games", "Wreckfest 2")
USERDATA_REL = os.path.join("userdata", "*", APPID, "remote", "profile.sgfi")


def config_path():
    home = os.path.expanduser("~")
    return os.path.join(home, ".wreckfest2autoupgrader.json")


def load_config():
    try:
        with open(config_path()) as f:
            cfg = json.load(f)
            if isinstance(cfg, dict):
                return cfg
    except (OSError, ValueError):
        pass
    return {}


def save_config(cfg):
    try:
        with open(config_path(), "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# --------------------------------------------------------------------------- steam roots

def _registry_steam_root():
    """Steam's install dir from HKCU\\Software\\Valve\\Steam — works whatever the drive/folder."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            val, _ = winreg.QueryValueEx(key, "SteamPath")
            return val or None
    except (ImportError, OSError):
        return None


def _default_steam_roots():
    roots = []
    if sys.platform.startswith("linux"):
        for cand in ("~/.steam/steam", "~/.local/share/Steam", "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"):
            p = os.path.expanduser(cand)
            if os.path.isdir(p):
                roots.append(p)
    else:
        reg = _registry_steam_root()
        if reg:
            roots.append(reg)
        pf = os.environ.get("ProgramFiles(x86)") or "C:\\Program Files (x86)"
        pf2 = os.environ.get("ProgramFiles") or "C:\\Program Files"
        for cand in (os.path.join(pf, "Steam"), os.path.join(pf2, "Steam"),
                     "C:\\Steam", "D:\\Steam", "D:\\SteamLibrary\\Steam"):
            if os.path.isdir(cand):
                roots.append(cand)
    seen, out = set(), []
    for r in roots:
        r = os.path.realpath(r)
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def _parse_libraryfolders(steam_root):
    """Yield library root paths from steamapps/libraryfolders.vdf."""
    vdf = os.path.join(steam_root, "steamapps", "libraryfolders.vdf")
    if not os.path.isfile(vdf):
        return
    text = open(vdf, encoding="utf-8", errors="replace").read()
    for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', text):
        path = m.group(1).replace("\\\\", "\\")
        path = path.replace("//", "/")
        if os.path.isdir(path):
            yield path


def _all_libraries():
    libs = set()
    for root in _default_steam_roots():
        libs.add(root)
        for lib in _parse_libraryfolders(root):
            libs.add(os.path.realpath(lib))
    return libs


def _glob_lib(part):
    for lib in _all_libraries():
        for m in glob.glob(os.path.join(lib, part)):
            yield m


# --------------------------------------------------------------------------- detection

def find_vehicle_dir():
    """Return the game's data/vehicle directory or None."""
    for lib in _all_libraries():
        cand = os.path.join(lib, "steamapps", "common", GAME_DIR, "data", "vehicle")
        if os.path.isdir(cand):
            return cand
    # fallback: any path containing Wreckfest 2 / data/vehicle
    return None


def find_save_and_mirror():
    """Return (primary_save, cloud_mirror_or_None)."""
    primary = mirror = None

    # 1) Proton / Windows-native My Games copy
    for lib in _all_libraries():
        for m in glob.glob(os.path.join(lib, COMPAT_SAVE_REL, "*", SAVE_REL)):
            primary = m
            break
        if primary:
            break
    if primary is None:
        # Windows-native Documents (incl. OneDrive redirection / localized names).
        # MYGAMES_REL already starts with "Documents", so these are full paths from home.
        home = os.path.expanduser("~")
        for rel in (MYGAMES_REL,
                    os.path.join("OneDrive", "Documents", "My Games", "Wreckfest 2"),
                    os.path.join("OneDrive", "Dokumente", "My Games", "Wreckfest 2")):
            for m in glob.glob(os.path.join(home, rel, "*", SAVE_REL)):
                primary = m
                break
            if primary:
                break

    # 2) Steam userdata cloud mirror
    for root in _default_steam_roots():
        for m in glob.glob(os.path.join(root, USERDATA_REL)):
            mirror = m
            break
        if mirror:
            break

    return primary, mirror


def detect(manual_vehicle=None, manual_save=None, manual_mirror=None):
    """Resolve all three paths, manual > config > auto-detect.

    Stale config paths (files/folders that no longer exist, e.g. after the user moved them)
    are ignored and fall through to auto-detection.
    """
    cfg = load_config()
    auto_save, auto_mirror = find_save_and_mirror()

    def pick(manual, key, auto):
        if manual:
            return manual
        val = cfg.get(key)
        if val and os.path.exists(val):
            return val
        return auto

    return {
        "vehicle_dir": pick(manual_vehicle, "vehicle_dir", find_vehicle_dir()),
        "save_path": pick(manual_save, "save_path", auto_save),
        "mirror_path": pick(manual_mirror, "mirror_path", auto_mirror),
    }