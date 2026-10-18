"""Soft-delete (trash) support: move files aside instead of erasing them."""

import json
import os
import shutil
import threading
import time

_LOCKS = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(root_path):
    with _LOCKS_GUARD:
        lock = _LOCKS.get(root_path)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[root_path] = lock
        return lock


def trash_dir(root_path, dirname=".trash"):
    return os.path.join(root_path, dirname)


def is_trash_rel(rel, dirname=".trash"):
    if not rel:
        return False
    first = rel.replace("\\", "/").split("/", 1)[0]
    return first == dirname


def _index_path(root_path, dirname):
    return os.path.join(trash_dir(root_path, dirname), "index.json")


def _load_index(root_path, dirname):
    path = _index_path(root_path, dirname)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save_index(root_path, dirname, items):
    directory = trash_dir(root_path, dirname)
    os.makedirs(directory, exist_ok=True)
    path = _index_path(root_path, dirname)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2)
    os.replace(tmp, path)


def _entry_size(abs_path):
    if os.path.isfile(abs_path) and not os.path.islink(abs_path):
        try:
            return os.path.getsize(abs_path)
        except OSError:
            return None
    return None


def move(root_path, rel, dirname=".trash"):
    """Move ``rel`` (relative to ``root_path``) into the trash. Returns the id."""
    abs_target = os.path.join(root_path, rel)
    name = os.path.basename(abs_target.rstrip(os.sep))
    with _lock_for(root_path):
        directory = trash_dir(root_path, dirname)
        os.makedirs(directory, exist_ok=True)
        item_id = "%d-%s" % (int(time.time()), os.urandom(3).hex())
        dest = os.path.join(directory, item_id)
        os.rename(abs_target, dest)
        items = _load_index(root_path, dirname)
        items.append({
            "id": item_id,
            "name": name,
            "original": rel.replace("\\", "/"),
            "deleted_at": time.time(),
            "is_dir": os.path.isdir(dest),
            "size": _entry_size(dest),
        })
        _save_index(root_path, dirname, items)
        return item_id


def list_items(root_path, dirname=".trash"):
    """Return trash entries (most recent first), dropping stale manifest rows."""
    with _lock_for(root_path):
        items = _load_index(root_path, dirname)
        directory = trash_dir(root_path, dirname)
        valid = []
        changed = False
        for item in items:
            if os.path.exists(os.path.join(directory, item.get("id", ""))):
                valid.append(item)
            else:
                changed = True
        if changed:
            _save_index(root_path, dirname, valid)
        valid.sort(key=lambda i: i.get("deleted_at", 0), reverse=True)
        return valid


def count(root_path, dirname=".trash"):
    try:
        return len(list_items(root_path, dirname))
    except OSError:
        return 0


def _unique_target(root_path, rel):
    candidate = os.path.join(root_path, rel)
    if not os.path.exists(candidate):
        return candidate
    base, ext = os.path.splitext(candidate)
    counter = 1
    while True:
        candidate = "%s (restored %d)%s" % (base, counter, ext)
        if not os.path.exists(candidate):
            return candidate
        counter += 1


def restore(root_path, item_id, dirname=".trash"):
    """Restore a trashed item to its original location (renaming on conflict)."""
    with _lock_for(root_path):
        directory = trash_dir(root_path, dirname)
        items = _load_index(root_path, dirname)
        match = next((i for i in items if i.get("id") == item_id), None)
        if match is None:
            raise FileNotFoundError(item_id)
        src = os.path.join(directory, item_id)
        if not os.path.exists(src):
            raise FileNotFoundError(item_id)
        original = match.get("original") or match.get("name") or item_id
        dest = _unique_target(root_path, original)
        parent = os.path.dirname(dest)
        if parent:
            os.makedirs(parent, exist_ok=True)
        os.rename(src, dest)
        items = [i for i in items if i.get("id") != item_id]
        _save_index(root_path, dirname, items)
        return os.path.relpath(dest, root_path).replace("\\", "/")


def remove(root_path, item_id, dirname=".trash"):
    """Permanently delete a single trashed item."""
    with _lock_for(root_path):
        directory = trash_dir(root_path, dirname)
        target = os.path.join(directory, item_id)
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target)
        elif os.path.exists(target):
            os.remove(target)
        items = [i for i in _load_index(root_path, dirname) if i.get("id") != item_id]
        _save_index(root_path, dirname, items)


def empty(root_path, dirname=".trash"):
    """Permanently delete everything in the trash."""
    with _lock_for(root_path):
        directory = trash_dir(root_path, dirname)
        items = _load_index(root_path, dirname)
        for item in items:
            target = os.path.join(directory, item.get("id", ""))
            try:
                if os.path.isdir(target) and not os.path.islink(target):
                    shutil.rmtree(target)
                elif os.path.exists(target):
                    os.remove(target)
            except OSError:
                pass
        try:
            os.remove(_index_path(root_path, dirname))
        except OSError:
            pass
        return len(items)