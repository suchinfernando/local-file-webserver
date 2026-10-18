"""Discovery of well-known user folders and mounted filesystems (stdlib only)."""

import os
import re
import sys

try:
    import shutil
except ImportError:  # pragma: no cover
    shutil = None

# Filesystem types that represent real storage we may want to expose.
REAL_FS = {
    "ext2", "ext3", "ext4", "btrfs", "xfs", "vfat", "exfat", "ntfs", "ntfs3",
    "fuseblk", "f2fs", "jfs", "reiserfs", "hfs", "hfsplus", "apfs", "udf",
    "iso9660", "cifs", "smb3", "nfs", "nfs4", "9p", "virtiofs", "zfs", "ufs",
}

# Everything else that is clearly a virtual/pseudo filesystem.
PSEUDO_FS = {
    "proc", "sysfs", "tmpfs", "devtmpfs", "devpts", "cgroup", "cgroup2",
    "securityfs", "debugfs", "tracefs", "pstore", "bpf", "configfs",
    "hugetlbfs", "mqueue", "autofs", "ramfs", "efivarfs", "rpc_pipefs",
    "nsfs", "overlay", "squashfs", "fuse.gvfsd-fuse", "fuse.portal",
    "fuse.snapfuse", "binfmt_misc", "fusectl", "sunrpc", "selinuxfs",
}

REMOVABLE_PREFIXES = ("/media/", "/run/media/", "/mnt/")

USER_DIR_NAMES = [
    ("Desktop", "desktop"),
    ("Documents", "document"),
    ("Downloads", "download"),
    ("Pictures", "image"),
    ("Music", "music"),
    ("Videos", "video"),
    ("Public", "folder"),
    ("Templates", "folder"),
]


def home_dir():
    """Return the current user's home directory."""
    return os.path.expanduser("~")


def tenant_display_name(path):
    base = os.path.basename(path.rstrip(os.sep))
    return base or path


def _decode_mount_field(value):
    """Decode octal escapes used by /proc/mounts (e.g. \\040 for space)."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def user_dirs(home=None):
    """Return the list of existing well-known user directories.

    Uses XDG user dirs configuration when present, otherwise the conventional
    English folder names. Each item is ``(label, absolute_path, icon)``.
    """
    home = os.path.abspath(home or home_dir())
    labels = {}
    config = os.path.join(home, ".config", "user-dirs.dirs")
    if os.path.isfile(config):
        try:
            with open(config, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"')
                    value = value.replace("$HOME", home)
                    if key.startswith("XDG_") and key.endswith("_DIR"):
                        name = key[4:-4].capitalize()
                        labels[name] = os.path.abspath(os.path.expanduser(value))
        except OSError:
            pass

    result = []
    seen = set()
    for name, icon in USER_DIR_NAMES:
        path = labels.get(name, os.path.join(home, name))
        if path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        result.append((name, path, icon))
    return result


def _is_removable(mountpoint, device):
    if mountpoint == "/":
        return False
    if mountpoint.startswith(REMOVABLE_PREFIXES):
        return True
    base = os.path.basename(device)
    sysfs = "/sys/block/%s/removable" % base
    try:
        with open(sysfs, "r", encoding="ascii") as fh:
            if fh.read().strip() == "1":
                return True
    except OSError:
        pass
    return False


def _linux_drives():
    drives = []
    seen = set()
    for source in ("/proc/mounts", "/etc/mtab"):
        if not os.path.isfile(source):
            continue
        try:
            with open(source, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 3:
                continue
            device = _decode_mount_field(parts[0])
            mountpoint = _decode_mount_field(parts[1])
            fstype = parts[2]
            if fstype in PSEUDO_FS or fstype not in REAL_FS:
                continue
            if mountpoint in seen:
                continue
            if not os.path.isdir(mountpoint):
                continue
            seen.add(mountpoint)
            drives.append({
                "path": mountpoint,
                "device": device,
                "fstype": fstype,
                "removable": _is_removable(mountpoint, device),
            })
        if drives:
            break
    return drives


def _macos_drives():
    drives = []
    volumes = "/Volumes"
    if os.path.isdir(volumes):
        for name in sorted(os.listdir(volumes)):
            path = os.path.join(volumes, name)
            if path == "/Volumes" or not os.path.isdir(path):
                continue
            drives.append({
                "path": path,
                "device": path,
                "fstype": "",
                "removable": True,
            })
    return drives


def _windows_drives():
    drives = []
    if sys.platform.startswith("win"):
        import ctypes  # pragma: no cover

        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i in range(26):
            if bitmask & (1 << i):
                path = "%s:\\" % chr(ord("A") + i)
                drives.append({
                    "path": path,
                    "device": path,
                    "fstype": "",
                    "removable": False,
                })
    return drives


def detect_drives():
    """Return a list of mounted real filesystems.

    Each item is a dict: ``path``, ``device``, ``fstype``, ``removable``.
    Sorted with removable drives first so they appear at the top.
    """
    if sys.platform.startswith("win"):
        drives = _windows_drives()
    elif sys.platform == "darwin":
        drives = _macos_drives()
    else:
        drives = _linux_drives()
    drives.sort(key=lambda d: (not d["removable"], d["path"]))
    return drives


def disk_usage(path):
    """Return ``{total, used, free}`` in bytes, or ``None`` when unavailable."""
    getter = getattr(shutil, "disk_usage", None)
    if getter is None:
        return None
    try:
        usage = getter(path)
    except OSError:
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}