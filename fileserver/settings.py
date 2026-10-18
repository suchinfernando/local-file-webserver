"""Configuration file handling and root/drive assembly.

Precedence: command-line flag > config file > built-in default.
"""

import configparser
import os

from . import places
from .config import Root

DEFAULT_DRIVE_MODE = "locked"
DEFAULT_TRASH_DIRNAME = ".trash"


def default_config_path(cwd=None):
    """Return ``./config.ini`` when it exists, otherwise ``None``."""
    candidate = os.path.join(cwd or os.getcwd(), "config.ini")
    return candidate if os.path.isfile(candidate) else None


def load_file(path):
    """Load a config file into a ``ConfigParser`` (empty parser if missing)."""
    parser = configparser.ConfigParser()
    if path and os.path.isfile(path):
        try:
            parser.read(path)
        except (OSError, configparser.Error):
            pass
    return parser


def section_get(parser, section, option, default=None):
    if parser is None or not parser.has_option(section, option):
        return default
    return parser.get(section, option)


def as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def default_favorites_path(home=None):
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        home or os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, "local-file-webserver", "favorites.json")


def parse_roots_option(value):
    """Parse a comma/newline separated list of extra root paths."""
    if not value:
        return []
    parts = []
    for chunk in str(value).replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            parts.append(chunk)
    return parts


def build_roots(primary_path, drive_mode, extra_roots, enable_drives):
    """Return ``(roots, drives)``.

    ``roots`` maps opaque keys to accessible :class:`Root` objects. ``drives``
    lists every detected drive (including locked ones) for the sidebar.
    """
    primary_path = os.path.abspath(primary_path)
    label = os.path.basename(primary_path) or primary_path
    roots = {
        "home": Root(key="home", path=primary_path, label=label or "Home", kind="home"),
    }
    drives = []
    seen = {primary_path}

    if enable_drives:
        index = 0
        for detected in places.detect_drives():
            path = os.path.abspath(detected["path"])
            if path in seen:
                continue
            removable = bool(detected.get("removable"))
            if drive_mode == "accessible" and not removable:
                continue
            accessible = drive_mode == "unlocked" or removable
            key = "drive%d" % index
            index += 1
            root = Root(
                key=key,
                path=path,
                label=places.tenant_display_name(path),
                kind="drive",
                removable=removable,
                accessible=accessible,
                device=detected.get("device", ""),
                fstype=detected.get("fstype", ""),
            )
            drives.append(root)
            if accessible:
                roots[key] = root
                seen.add(path)

    for index, raw in enumerate(extra_roots or []):
        path = os.path.abspath(os.path.expanduser(raw))
        if path in seen or not os.path.isdir(path):
            continue
        key = "root%d" % index
        root = Root(
            key=key, path=path, label=places.tenant_display_name(path), kind="custom",
        )
        roots[key] = root
        seen.add(path)

    return roots, drives