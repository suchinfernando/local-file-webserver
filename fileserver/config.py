"""Runtime configuration for the file server."""

import os
from dataclasses import dataclass, field


@dataclass
class Root:
    """A navigable location (home folder, drive or custom root)."""

    key: str
    path: str
    label: str
    kind: str = "custom"          # "home" | "drive" | "custom"
    removable: bool = False
    accessible: bool = True
    device: str = ""
    fstype: str = ""

    def to_json(self, include_key=True):
        data = {
            "label": self.label,
            "path": self.path,
            "kind": self.kind,
            "removable": self.removable,
            "accessible": self.accessible,
            "device": self.device,
            "fstype": self.fstype,
        }
        if include_key and self.accessible:
            data["key"] = self.key
        return data


@dataclass
class Config:
    root: str
    host: str = "0.0.0.0"
    port: int = 8000
    read_only: bool = False
    quiet: bool = False
    show_qr: bool = True
    version: str = "1.0.0"

    home: str = ""
    show_hidden: bool = False
    use_trash: bool = True
    trash_dirname: str = ".trash"
    favorites_file: str = ""
    drive_mode: str = "locked"     # "accessible" | "locked" | "unlocked"

    # key -> Root (only accessible roots are stored here)
    roots: dict = field(default_factory=dict)
    # every detected/listed drive (accessible or not), for the sidebar
    drives: list = field(default_factory=list)
    primary_key: str = "home"
    # Favorites store instance (set by settings/CLI); may be None.
    favorites: object = field(default=None, repr=False, compare=False)

    # Populated once the socket is bound.
    bound_port: int = field(default=0, init=False)
    lan_ip: str = field(default="127.0.0.1", init=False)

    def __post_init__(self):
        self.root = os.path.abspath(self.root)
        if not self.home:
            self.home = self.root
        if not self.roots:
            label = os.path.basename(self.root) or self.root
            self.roots[self.primary_key] = Root(
                key=self.primary_key, path=self.root, label=label, kind="home",
            )

    def find_root(self, key):
        if not key:
            key = self.primary_key
        return self.roots.get(key)

    def primary(self):
        return self.roots[self.primary_key]

    def lan_url(self, lan_ip: str) -> str:
        return "http://%s:%d" % (lan_ip, self.bound_port or self.port)

    def local_url(self, local_ip: str = "127.0.0.1") -> str:
        return "http://%s:%d" % (local_ip, self.bound_port or self.port)