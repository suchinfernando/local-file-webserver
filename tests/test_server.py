"""Smoke tests for local-file-webserver (stdlib unittest)."""

import base64
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import zipfile

from fileserver.config import Config, Root
from fileserver.favorites import Favorites
from fileserver.security import Auth
from fileserver.server import create_server


class ServerTestCase(unittest.TestCase):
    auth_password = "secret"

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = cls.tmp.name
        cls.extra_root = tempfile.mkdtemp()
        cls.locked_root = tempfile.mkdtemp()

        with open(os.path.join(cls.root, "hello.txt"), "w", encoding="utf-8") as fh:
            fh.write("hello world")
        with open(os.path.join(cls.root, "data.bin"), "wb") as fh:
            fh.write(bytes(range(256)))
        with open(os.path.join(cls.root, ".secret.txt"), "w", encoding="utf-8") as fh:
            fh.write("hidden")
        os.mkdir(os.path.join(cls.root, "sub"))
        os.mkdir(os.path.join(cls.root, "sub", "nested"))
        with open(os.path.join(cls.root, "sub", "nested", "findme.txt"), "w") as fh:
            fh.write("found")
        os.mkdir(os.path.join(cls.root, "zipdir"))
        with open(os.path.join(cls.root, "zipdir", "inner.txt"), "w") as fh:
            fh.write("inner")
        with open(os.path.join(cls.extra_root, "extra.txt"), "w") as fh:
            fh.write("extra")

        cls.config = Config(root=cls.root, host="127.0.0.1", port=0, quiet=True)
        cls.config.roots["extra"] = Root(
            key="extra", path=cls.extra_root, label="Extra", kind="custom")
        cls.config.roots["locked"] = Root(
            key="locked", path=cls.locked_root, label="Locked", kind="drive",
            accessible=False)
        cls.config.drives.append(cls.config.roots["locked"])
        cls.config.favorites = Favorites(os.path.join(cls.tmp.name, "favorites.json"))

        cls.auth = Auth(username="user", password=cls.auth_password)
        cls.server = create_server(cls.config, cls.auth)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()
        for path in (cls.extra_root, cls.locked_root):
            try:
                if os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
            except OSError:
                pass

    # -- helpers ----------------------------------------------------------
    def request(self, method, path, body=None, headers=None, auth=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        all_headers = dict(headers or {})
        if auth:
            token = base64.b64encode(
                ("user:%s" % self.auth_password).encode("utf-8")
            ).decode("ascii")
            all_headers["Authorization"] = "Basic " + token
        conn.request(method, path, body=body, headers=all_headers)
        response = conn.getresponse()
        data = response.read()
        conn.close()
        return response, data

    def json_request(self, method, path, body=None, headers=None, auth=True):
        response, data = self.request(method, path, body, headers, auth)
        payload = json.loads(data.decode("utf-8")) if data else None
        return response.status, payload

    # -- auth -------------------------------------------------------------
    def test_requires_auth(self):
        response, _ = self.request("GET", "/api/info", auth=False)
        self.assertEqual(response.status, 401)
        self.assertTrue(response.getheader("WWW-Authenticate"))

    def test_accepts_valid_auth(self):
        status, payload = self.json_request("GET", "/api/info")
        self.assertEqual(status, 200)
        self.assertIn("version", payload)

    def test_rejects_bad_password(self):
        token = base64.b64encode(b"user:wrong").decode("ascii")
        response, _ = self.request(
            "GET", "/api/info", auth=False,
            headers={"Authorization": "Basic " + token},
        )
        self.assertEqual(response.status, 401)

    # -- listing ----------------------------------------------------------
    def test_list_root(self):
        status, payload = self.json_request("GET", "/api/list")
        self.assertEqual(status, 200)
        names = {e["name"] for e in payload["entries"]}
        self.assertIn("hello.txt", names)
        self.assertIn("sub", names)
        sub = [e for e in payload["entries"] if e["name"] == "sub"][0]
        self.assertTrue(sub["is_dir"])

    def test_list_unknown_returns_404(self):
        status, payload = self.json_request("GET", "/api/list?path=nope")
        self.assertEqual(status, 400)

    def test_hidden_files_filtered(self):
        _, payload = self.json_request("GET", "/api/list")
        names = {e["name"] for e in payload["entries"]}
        self.assertNotIn(".secret.txt", names)
        _, payload = self.json_request("GET", "/api/list?hidden=1")
        names = {e["name"] for e in payload["entries"]}
        self.assertIn(".secret.txt", names)

    # -- roots / places ---------------------------------------------------
    def test_places(self):
        status, payload = self.json_request("GET", "/api/places")
        self.assertEqual(status, 200)
        self.assertEqual(payload["primary_key"], "home")
        self.assertEqual(payload["places"][0]["label"], "Home")
        self.assertTrue(any(d["label"] == "Extra" for d in payload["places"]) is False)

    def test_extra_root_listing(self):
        status, payload = self.json_request("GET", "/api/list?root=extra")
        self.assertEqual(status, 200)
        self.assertEqual(payload["root"], "extra")
        self.assertIn("extra.txt", {e["name"] for e in payload["entries"]})

    def test_locked_root_forbidden(self):
        status, payload = self.json_request("GET", "/api/list?root=locked")
        self.assertEqual(status, 403)

    def test_unknown_root_rejected(self):
        status, payload = self.json_request("GET", "/api/list?root=nope")
        self.assertEqual(status, 400)

    def test_extra_root_traversal_contained(self):
        status, payload = self.json_request("GET", "/api/list?root=extra&path=../../")
        self.assertEqual(status, 200)
        self.assertEqual(payload["path"], "")

    # -- search -----------------------------------------------------------
    def test_recursive_search(self):
        status, payload = self.json_request("GET", "/api/search?path=&q=findme")
        self.assertEqual(status, 200)
        paths = {e["path"] for e in payload["results"]}
        self.assertIn("sub/nested/findme.txt", paths)

    def test_search_skips_hidden(self):
        _, payload = self.json_request("GET", "/api/search?q=secret")
        self.assertEqual(payload["results"], [])
        _, payload = self.json_request("GET", "/api/search?q=secret&hidden=1")
        self.assertTrue(any("secret" in e["name"] for e in payload["results"]))

    # -- traversal --------------------------------------------------------
    def test_dotdot_is_neutralised(self):
        status, payload = self.json_request("GET", "/api/list?path=../..")
        self.assertEqual(status, 200)
        self.assertEqual(payload["path"], "")

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unsupported")
    def test_symlink_escape_is_forbidden(self):
        outside = tempfile.NamedTemporaryFile(delete=False)
        outside.write(b"secret")
        outside.close()
        link = os.path.join(self.root, "escape")
        try:
            os.symlink(outside.name, link)
        except OSError:
            self.skipTest("cannot create symlink")
        try:
            status, payload = self.json_request("GET", "/api/download?path=escape")
            self.assertEqual(status, 403)
        finally:
            os.unlink(link)
            os.unlink(outside.name)

    # -- download + ranges ------------------------------------------------
    def test_download(self):
        response, data = self.request("GET", "/api/download?path=hello.txt")
        self.assertEqual(response.status, 200)
        self.assertEqual(data, b"hello world")
        self.assertIn("attachment", response.getheader("Content-Disposition"))

    def test_download_folder_as_zip(self):
        response, data = self.request("GET", "/api/download?path=zipdir")
        self.assertEqual(response.status, 200)
        self.assertIn(".zip", response.getheader("Content-Disposition"))
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn("zipdir/inner.txt", zf.namelist())
        self.assertEqual(zf.read("zipdir/inner.txt"), b"inner")

    def test_archive_selection(self):
        body = json.dumps({"root": "home", "paths": ["hello.txt", "zipdir"]}).encode()
        response, data = self.request(
            "POST", "/api/archive", body=body,
            headers={"Content-Type": "application/json"})
        self.assertEqual(response.status, 200)
        zf = zipfile.ZipFile(io.BytesIO(data))
        self.assertIn("hello.txt", zf.namelist())
        self.assertIn("zipdir/inner.txt", zf.namelist())

    def test_range_request(self):
        response, data = self.request(
            "GET", "/api/raw?path=data.bin", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status, 206)
        self.assertEqual(data, bytes(range(10)))
        self.assertEqual(response.getheader("Content-Range"), "bytes 0-9/256")

    def test_unsatisfiable_range(self):
        response, data = self.request(
            "GET", "/api/raw?path=data.bin", headers={"Range": "bytes=9999-10000"})
        self.assertEqual(response.status, 416)

    # -- writes -----------------------------------------------------------
    def test_upload_and_delete(self):
        body = b"uploaded content"
        status, payload = self.json_request(
            "POST", "/api/upload?path=&name=upload.txt", body=body)
        self.assertEqual(status, 201)
        self.assertTrue(os.path.exists(os.path.join(self.root, "upload.txt")))
        with open(os.path.join(self.root, "upload.txt"), "rb") as fh:
            self.assertEqual(fh.read(), body)

        status, payload = self.json_request("POST", "/api/delete?path=upload.txt")
        self.assertEqual(status, 200)
        self.assertTrue(payload["trashed"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "upload.txt")))

    def test_upload_conflict(self):
        status, payload = self.json_request(
            "POST", "/api/upload?path=&name=hello.txt", body=b"x")
        self.assertEqual(status, 409)

    def test_upload_overwrite(self):
        status, _ = self.json_request(
            "POST", "/api/upload?path=&name=hello.txt&overwrite=1", body=b"new")
        self.assertEqual(status, 201)
        with open(os.path.join(self.root, "hello.txt"), "rb") as fh:
            self.assertEqual(fh.read(), b"new")
        with open(os.path.join(self.root, "hello.txt"), "wb") as fh:
            fh.write(b"hello world")

    def test_upload_bad_name(self):
        status, payload = self.json_request(
            "POST", "/api/upload?path=&name=..%2Fevil", body=b"x")
        self.assertEqual(status, 400)

    def test_mkdir_and_rename(self):
        status, _ = self.json_request("POST", "/api/mkdir?path=&name=folder1")
        self.assertEqual(status, 201)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "folder1")))

        status, _ = self.json_request("POST", "/api/rename?path=folder1&name=folder2")
        self.assertEqual(status, 200)
        self.assertTrue(os.path.isdir(os.path.join(self.root, "folder2")))
        os.rmdir(os.path.join(self.root, "folder2"))

    def test_cannot_delete_root(self):
        status, _ = self.json_request("POST", "/api/delete?path=")
        self.assertEqual(status, 400)

    # -- trash ------------------------------------------------------------
    def test_trash_restore_roundtrip(self):
        target = os.path.join(self.root, "trashme.txt")
        os.mkdir(os.path.join(self.root, "trashdir"))
        with open(os.path.join(self.root, "trashdir", "inside.txt"), "w") as fh:
            fh.write("x")
        with open(target, "w") as fh:
            fh.write("trash me")

        status, payload = self.json_request("POST", "/api/delete?path=trashme.txt")
        self.assertEqual(status, 200)
        self.assertTrue(payload["trashed"])
        self.assertFalse(os.path.exists(target))

        _, payload = self.json_request("GET", "/api/trash")
        ids = {e["name"]: e["id"] for e in payload["entries"]}
        self.assertIn("trashme.txt", ids)

        status, _ = self.json_request(
            "POST", "/api/trash/restore?id=" + ids["trashme.txt"])
        self.assertEqual(status, 200)
        self.assertTrue(os.path.exists(target))

        status, payload = self.json_request("POST", "/api/delete?path=trashdir")
        self.assertEqual(status, 200)
        self.assertFalse(os.path.exists(os.path.join(self.root, "trashdir")))
        status, payload = self.json_request("POST", "/api/trash/empty")
        self.assertEqual(status, 200)
        _, payload = self.json_request("GET", "/api/trash")
        self.assertEqual(payload["entries"], [])

    def test_permanent_delete(self):
        path = os.path.join(self.root, "perm.txt")
        with open(path, "w") as fh:
            fh.write("bye")
        status, payload = self.json_request(
            "POST", "/api/delete?path=perm.txt&permanent=1")
        self.assertEqual(status, 200)
        self.assertFalse(payload["trashed"])
        self.assertFalse(os.path.exists(path))

    # -- favorites --------------------------------------------------------
    def test_favorites_roundtrip(self):
        status, payload = self.json_request(
            "POST", "/api/favorites?path=sub&label=Sub")
        self.assertEqual(status, 200)
        self.assertTrue(payload["added"])
        _, payload = self.json_request("GET", "/api/favorites")
        labels = {f["label"] for f in payload["favorites"]}
        self.assertIn("Sub", labels)

        status, payload = self.json_request("DELETE", "/api/favorites?path=sub")
        self.assertEqual(status, 200)
        self.assertTrue(payload["removed"])
        _, payload = self.json_request("GET", "/api/favorites")
        self.assertEqual(payload["favorites"], [])


class ReadOnlyTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.config = Config(root=cls.tmp.name, host="127.0.0.1", port=0,
                            quiet=True, read_only=True)
        cls.auth = Auth(password=None)
        cls.server = create_server(cls.config, cls.auth)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)
        cls.tmp.cleanup()

    def test_upload_forbidden(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/upload?path=&name=x.txt", body=b"data")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 403)

    def test_no_auth_needed(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", "/api/info")
        response = conn.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertTrue(payload["read_only"])


if __name__ == "__main__":
    unittest.main()