"""API route handlers. Each handler receives the request handler and query."""

import os
import shutil
import sys
import tempfile

if __package__ in (None, ""):
    # Allow running this file directly, e.g. `python3 fileserver/routes.py`.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "fileserver"

from . import archive, fsutil, places, trash


def _first(query, key, default=None):
    values = query.get(key)
    if not values:
        return default
    return values[0]


def _bool(value):
    return str(value).lower() in ("1", "true", "yes", "on")


def _silent_remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


class Routes:
    def __init__(self, config):
        self.config = config

    # -- dispatch ---------------------------------------------------------
    def handle(self, handler, method, path, query):
        table = {
            "/api/info": {"GET": self.info},
            "/api/places": {"GET": self.places},
            "/api/list": {"GET": self.list},
            "/api/search": {"GET": self.search},
            "/api/download": {"GET": self.download},
            "/api/raw": {"GET": self.raw},
            "/api/archive": {"POST": self.archive},
            "/api/upload": {"POST": self.upload},
            "/api/mkdir": {"POST": self.mkdir},
            "/api/rename": {"POST": self.rename},
            "/api/delete": {"POST": self.delete, "DELETE": self.delete},
            "/api/favorites": {
                "GET": self.favorites_list,
                "POST": self.favorites_add,
                "DELETE": self.favorites_remove,
            },
            "/api/trash": {"GET": self.trash_list},
            "/api/trash/restore": {"POST": self.trash_restore},
            "/api/trash/empty": {"POST": self.trash_empty},
        }
        entry = table.get(path)
        if entry is None:
            return handler.json_error(404, "unknown endpoint: %s" % path)
        fn = entry.get(method)
        if fn is None:
            allowed = ", ".join(sorted(entry))
            return handler.json_error(405, "method not allowed (allowed: %s)" % allowed)
        return fn(handler, query)

    # -- shared helpers ---------------------------------------------------
    def _root(self, handler, query):
        key = _first(query, "root") or self.config.primary_key
        root = self.config.find_root(key)
        if root is None:
            raise ValueError("unknown root: %s" % key)
        if not root.accessible:
            raise PermissionError("root is locked: %s" % key)
        return root

    def _show_hidden(self, query):
        value = _first(query, "hidden")
        if value is None:
            return self.config.show_hidden
        return _bool(value)

    def _require_writable(self, handler):
        if self.config.read_only:
            handler.json_error(403, "server is read-only")
            return False
        return True

    # -- metadata ---------------------------------------------------------
    def info(self, handler, query):
        cfg = self.config
        lan_ip = getattr(cfg, "lan_ip", None) or "127.0.0.1"
        handler.send_json(200, {
            "root": os.path.basename(cfg.home) or cfg.home,
            "root_path": cfg.home,
            "home": cfg.home,
            "primary_key": cfg.primary_key,
            "drive_mode": cfg.drive_mode,
            "show_hidden": cfg.show_hidden,
            "use_trash": cfg.use_trash,
            "read_only": cfg.read_only,
            "auth_enabled": handler.auth.enabled,
            "username": handler.auth.username if handler.auth.enabled else None,
            "version": cfg.version,
            "lan_ip": lan_ip,
            "lan_url": cfg.lan_url(lan_ip),
            "local_url": cfg.local_url(),
        })

    def places(self, handler, query):
        cfg = self.config
        home_root = cfg.primary()
        items = [{
            "root": home_root.key, "path": "", "label": "Home",
            "icon": "home", "abs": home_root.path,
        }]
        for label, abs_path, icon in places.user_dirs(home_root.path):
            rel = os.path.relpath(abs_path, home_root.path).replace("\\", "/")
            if rel.startswith(".."):
                continue
            items.append({
                "root": home_root.key, "path": rel, "label": label,
                "icon": icon, "abs": abs_path,
            })

        drives = []
        for root in cfg.drives:
            item = root.to_json()
            item["usage"] = places.disk_usage(root.path) if root.accessible else None
            drives.append(item)

        trash_roots = []
        if cfg.use_trash:
            for root in cfg.roots.values():
                if root.kind in ("home", "custom"):
                    count = trash.count(root.path, cfg.trash_dirname)
                    if count:
                        trash_roots.append({
                            "root": root.key, "label": root.label, "count": count,
                        })

        handler.send_json(200, {
            "home": cfg.home,
            "primary_key": cfg.primary_key,
            "drive_mode": cfg.drive_mode,
            "places": items,
            "drives": drives,
            "trash": trash_roots,
        })

    # -- read -------------------------------------------------------------
    def list(self, handler, query):
        root = self._root(handler, query)
        rel = _first(query, "path", "")
        data = fsutil.list_dir(
            root.path, rel,
            show_hidden=self._show_hidden(query),
            trash_dirname=self.config.trash_dirname,
        )
        data["root"] = root.key
        handler.send_json(200, data)

    def search(self, handler, query):
        root = self._root(handler, query)
        rel = _first(query, "path", "")
        text = _first(query, "q", "")
        try:
            limit = max(1, min(int(_first(query, "limit", "300") or 300), 2000))
        except ValueError:
            limit = 300
        results = fsutil.walk_search(
            root.path, rel, text,
            show_hidden=self._show_hidden(query),
            limit=limit,
            trash_dirname=self.config.trash_dirname,
        )
        for entry in results:
            entry["root"] = root.key
        handler.send_json(200, {
            "root": root.key,
            "path": fsutil.normalize_rel(rel),
            "query": text,
            "results": results,
        })

    def download(self, handler, query):
        root = self._root(handler, query)
        rel = _first(query, "path", "")
        target = fsutil.resolve_within(root.path, rel)
        if os.path.isdir(target) and not os.path.islink(target):
            norm = fsutil.normalize_rel(rel)
            dest = archive.build_zip(root.path, [norm], self.config.trash_dirname)
            name = os.path.basename(target.rstrip(os.sep)) or "download"
            try:
                handler.send_file(dest, download_name=name + ".zip", inline=False)
            finally:
                _silent_remove(dest)
            return
        if not os.path.exists(target):
            raise FileNotFoundError(rel)
        handler.send_file(target, download_name=os.path.basename(target), inline=False)

    def raw(self, handler, query):
        root = self._root(handler, query)
        rel = _first(query, "path", "")
        target = fsutil.resolve_within(root.path, rel)
        if os.path.isdir(target) and not os.path.islink(target):
            return handler.json_error(400, "cannot preview a directory")
        handler.send_file(target, inline=True)

    def archive(self, handler, query):
        payload = handler.read_json()
        key = payload.get("root") or self.config.primary_key
        root = self.config.find_root(key)
        if root is None or not root.accessible:
            return handler.json_error(403, "unknown or locked root")
        rels = payload.get("paths")
        if not isinstance(rels, list) or not rels:
            return handler.json_error(400, "no paths supplied")
        norm = []
        for rel in rels:
            normalized = fsutil.normalize_rel(rel)
            target = fsutil.resolve_within(root.path, normalized)
            if not os.path.exists(target):
                return handler.json_error(404, "not found: %s" % rel)
            norm.append(normalized)
        name = payload.get("name") or "download.zip"
        if not name.endswith(".zip"):
            name += ".zip"
        dest = archive.build_zip(root.path, norm, self.config.trash_dirname)
        try:
            handler.send_file(dest, download_name=name, inline=False)
        finally:
            _silent_remove(dest)

    # -- write ------------------------------------------------------------
    def upload(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        directory = _first(query, "path", "")
        name = _first(query, "name") or handler.headers.get("X-Filename")
        try:
            name = fsutil.validate_name(name)
        except ValueError as exc:
            return handler.json_error(400, str(exc))

        length = handler.content_length()
        if length is None:
            return handler.json_error(411, "Content-Length is required")

        overwrite = _bool(_first(query, "overwrite", ""))
        rel = directory.rstrip("/") + "/" + name if directory else name
        target = fsutil.resolve_within(root.path, rel)
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            return handler.json_error(404, "destination directory not found")
        if os.path.isdir(target):
            return handler.json_error(409, "a directory with that name exists")
        if os.path.exists(target) and not overwrite:
            return handler.json_error(409, "file already exists")

        tmp_fd, tmp_path = tempfile.mkstemp(prefix=".upload-", dir=parent)
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                remaining = length
                while remaining > 0:
                    chunk = handler.rfile.read(min(1 << 16, remaining))
                    if not chunk:
                        raise ConnectionError("client closed connection during upload")
                    fh.write(chunk)
                    remaining -= len(chunk)
            handler.mark_body_consumed()
            os.replace(tmp_path, target)
        except Exception:
            _silent_remove(tmp_path)
            raise

        handler.send_json(201, {
            "saved": True,
            "root": root.key,
            "path": fsutil.normalize_rel(rel),
            "name": name,
            "size": length,
        })

    def mkdir(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        directory = _first(query, "path", "")
        try:
            name = fsutil.validate_name(_first(query, "name"))
        except ValueError as exc:
            return handler.json_error(400, str(exc))
        rel = directory.rstrip("/") + "/" + name if directory else name
        target = fsutil.resolve_within(root.path, rel)
        if os.path.exists(target):
            return handler.json_error(409, "already exists")
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            return handler.json_error(404, "parent directory not found")
        os.mkdir(target)
        handler.send_json(201, {"created": True, "root": root.key,
                                "path": fsutil.normalize_rel(rel)})

    def rename(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        rel = _first(query, "path", "")
        try:
            new_name = fsutil.validate_name(_first(query, "name"))
        except ValueError as exc:
            return handler.json_error(400, str(exc))
        src = fsutil.resolve_within(root.path, rel)
        if src == os.path.realpath(os.path.abspath(root.path)):
            return handler.json_error(400, "cannot rename the root directory")
        if not os.path.exists(src):
            return handler.json_error(404, "not found")
        parent_rel = os.path.dirname(fsutil.normalize_rel(rel))
        dst_rel = os.path.join(parent_rel, new_name) if parent_rel else new_name
        dst = fsutil.resolve_within(root.path, dst_rel)
        if os.path.exists(dst):
            return handler.json_error(409, "target already exists")
        os.rename(src, dst)
        handler.send_json(200, {"renamed": True, "root": root.key,
                                "path": fsutil.normalize_rel(dst_rel)})

    def delete(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        rel = fsutil.normalize_rel(_first(query, "path", ""))
        if not rel or rel == self.config.trash_dirname:
            return handler.json_error(400, "cannot delete this location")
        target = fsutil.resolve_within(root.path, rel)
        if not os.path.exists(target) and not os.path.islink(target):
            return handler.json_error(404, "not found")
        permanent = _bool(_first(query, "permanent", ""))
        if self.config.use_trash and not permanent:
            item_id = trash.move(root.path, rel, self.config.trash_dirname)
            handler.send_json(200, {"deleted": True, "trashed": True,
                                    "id": item_id, "root": root.key, "path": rel})
        else:
            if os.path.isdir(target) and not os.path.islink(target):
                shutil.rmtree(target)
            else:
                os.remove(target)
            handler.send_json(200, {"deleted": True, "trashed": False,
                                    "root": root.key, "path": rel})

    # -- favorites --------------------------------------------------------
    def favorites_list(self, handler, query):
        store = self.config.favorites
        enriched = []
        for item in (store.list() if store else []):
            root = self.config.find_root(item["root"])
            if not root or not root.accessible:
                continue
            enriched.append({
                "root": item["root"],
                "path": item["path"],
                "label": item["label"],
                "abs": os.path.join(root.path, item["path"]),
            })
        handler.send_json(200, {"favorites": enriched})

    def favorites_add(self, handler, query):
        store = self.config.favorites
        if store is None:
            return handler.json_error(503, "favorites are disabled")
        root = self._root(handler, query)
        rel = fsutil.normalize_rel(_first(query, "path", ""))
        target = fsutil.resolve_within(root.path, rel)
        if not os.path.isdir(target):
            return handler.json_error(400, "not a directory")
        label = _first(query, "label") or os.path.basename(target.rstrip(os.sep)) or root.label
        added = store.add(root.key, rel, label)
        handler.send_json(200, {"added": added, "favorites": store.list()})

    def favorites_remove(self, handler, query):
        store = self.config.favorites
        if store is None:
            return handler.json_error(503, "favorites are disabled")
        root = self._root(handler, query)
        rel = fsutil.normalize_rel(_first(query, "path", ""))
        removed = store.remove(root.key, rel)
        handler.send_json(200, {"removed": removed})

    # -- trash ------------------------------------------------------------
    def trash_list(self, handler, query):
        root = self._root(handler, query)
        entries = trash.list_items(root.path, self.config.trash_dirname)
        handler.send_json(200, {"root": root.key, "entries": entries})

    def trash_restore(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        item_id = _first(query, "id", "")
        if not item_id:
            return handler.json_error(400, "id is required")
        path = trash.restore(root.path, item_id, self.config.trash_dirname)
        handler.send_json(200, {"restored": True, "root": root.key, "path": path})

    def trash_empty(self, handler, query):
        if not self._require_writable(handler):
            return
        root = self._root(handler, query)
        item_id = _first(query, "id", "")
        if item_id:
            trash.remove(root.path, item_id, self.config.trash_dirname)
            handler.send_json(200, {"deleted": True, "id": item_id})
        else:
            count = trash.empty(root.path, self.config.trash_dirname)
            handler.send_json(200, {"emptied": True, "count": count})