"""Server-side persisted list of starred/pinned locations."""

import json
import os
import threading


class Favorites:
    """A tiny JSON-backed store of pinned folders.

    Entries are dicts of ``{root, path, label}`` where ``root`` is a root key
    and ``path`` is relative to that root.
    """

    def __init__(self, path=None):
        self.path = path
        self._lock = threading.Lock()
        self._items = []
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self):
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._items = [self._clean(item) for item in data if self._clean(item)]
        except (OSError, ValueError):
            self._items = []

    def _save(self):
        if not self.path:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._items, fh, indent=2)
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
            os.replace(tmp, self.path)
        except OSError:
            pass

    @staticmethod
    def _clean(item):
        if not isinstance(item, dict):
            return None
        root = item.get("root")
        path = item.get("path")
        if root is None or path is None:
            return None
        return {
            "root": str(root),
            "path": str(path),
            "label": str(item.get("label") or path or root),
        }

    # -- api --------------------------------------------------------------
    def list(self):
        with self._lock:
            return [dict(item) for item in self._items]

    def has(self, root, path):
        with self._lock:
            return any(i["root"] == root and i["path"] == path for i in self._items)

    def add(self, root, path, label):
        with self._lock:
            if any(i["root"] == root and i["path"] == path for i in self._items):
                return False
            self._items.append({"root": root, "path": path, "label": label})
            self._save()
            return True

    def remove(self, root, path):
        with self._lock:
            before = len(self._items)
            self._items = [
                i for i in self._items if not (i["root"] == root and i["path"] == path)
            ]
            changed = len(self._items) != before
            if changed:
                self._save()
            return changed