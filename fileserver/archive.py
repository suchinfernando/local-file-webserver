"""Build ZIP archives from files/folders on demand (stdlib only)."""

import os
import tempfile
import zipfile


def _add_dir(zf, abs_dir, arc_prefix, skip_names):
    for dirpath, dirnames, filenames in os.walk(abs_dir):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_names)
        rel = os.path.relpath(dirpath, abs_dir)
        arc_dir = arc_prefix if rel == "." else os.path.join(arc_prefix, rel)
        if not filenames and not dirnames:
            zf.writestr(arc_dir.replace(os.sep, "/").rstrip("/") + "/", "")
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            if os.path.islink(full):
                continue
            zf.write(full, os.path.join(arc_dir, name))


def build_zip(root_path, rels, trash_dirname=".trash"):
    """Create a temporary ZIP containing ``rels`` (relative to ``root_path``).

    Returns the path to the temporary file; the caller owns and must delete it.
    """
    fd, dest = tempfile.mkstemp(prefix="lfw-", suffix=".zip")
    os.close(fd)
    used = set()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in rels:
            abs_path = os.path.join(root_path, rel)
            if os.path.isdir(abs_path) and not os.path.islink(abs_path):
                base = os.path.basename(abs_path.rstrip(os.sep)) or "folder"
                top = base
                counter = 1
                while top in used:
                    top = "%s (%d)" % (base, counter)
                    counter += 1
                used.add(top)
                _add_dir(zf, abs_path, top, {trash_dirname})
            elif os.path.isfile(abs_path):
                base = os.path.basename(abs_path)
                top = base
                counter = 1
                while top in used:
                    top = "%s (%d)" % (base, counter)
                    counter += 1
                used.add(top)
                zf.write(abs_path, top)
            else:
                raise FileNotFoundError(rel)
    return dest