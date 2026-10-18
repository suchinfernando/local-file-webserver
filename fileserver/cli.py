"""Command line interface for local-file-webserver.

Usable via any of:
    python3 serve.py [folder]
    python3 -m fileserver [folder]

Settings come from (highest priority first): command-line flags, the config
file (see ``config.ini``), then built-in defaults.
"""

import argparse
import os
import sys

if __package__ in (None, ""):
    # Allow running this file directly, e.g. `python3 fileserver/cli.py`.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "fileserver"

from . import __version__, settings
from .config import Config
from .favorites import Favorites
from .security import Auth, generate_password
from .server import run


def build_parser():
    parser = argparse.ArgumentParser(
        prog="local-file-webserver",
        description="Serve a local folder over HTTP with a web UI.",
    )
    parser.add_argument(
        "directory", nargs="?", default=None,
        help="folder to serve (default: your home directory)",
    )
    parser.add_argument("--config", default=None,
                        help="path to a config file (default: ./config.ini if present)")
    parser.add_argument("-p", "--port", type=int, default=None,
                        help="port to listen on (default: 8000, 0 = random)")
    parser.add_argument("--host", default=None,
                        help="address to bind (default: 0.0.0.0 for LAN access)")
    parser.add_argument("--localhost", action="store_true",
                        help="bind to 127.0.0.1 only (no LAN access)")
    parser.add_argument("--user", default=None,
                        help="username for HTTP Basic auth (default: user)")
    parser.add_argument("--password", default=None,
                        help="enable auth with this password")
    parser.add_argument("--no-auth", action="store_true", default=None,
                        help="disable authentication entirely")
    parser.add_argument("--read-only", action="store_true", default=None,
                        help="disable uploads, deletes, renames and mkdir")

    parser.add_argument("--show-hidden", dest="show_hidden", action="store_true",
                        default=None, help="show dotfiles in listings")
    parser.add_argument("--no-show-hidden", dest="show_hidden", action="store_false",
                        help="hide dotfiles (default)")

    parser.add_argument("--trash", dest="use_trash", action="store_true", default=None,
                        help="soft-delete into .trash (default)")
    parser.add_argument("--permanent-delete", dest="use_trash", action="store_false",
                        help="delete permanently instead of moving to trash")

    parser.add_argument("--drives", choices=["accessible", "locked", "unlocked"],
                        default=None,
                        help="how non-removable filesystems are exposed")
    parser.add_argument("--no-drives", action="store_true", default=None,
                        help="do not list or expose mounted drives")
    parser.add_argument("--root", action="append", default=None, metavar="PATH",
                        help="extra root to expose (repeatable)")

    parser.add_argument("--no-qr", action="store_true",
                        help="hide the LAN QR code in the web UI")
    parser.add_argument("--open", action="store_true",
                        help="open the browser after starting")
    parser.add_argument("--quiet", action="store_true", default=None,
                        help="suppress per-request logging")
    parser.add_argument("--version", action="version",
                        version="%(prog)s " + __version__)
    return parser


def _merge(cli_value, file_parser, section, option, default):
    if cli_value is not None:
        return cli_value
    return settings.section_get(file_parser, section, option, default)


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = args.config or settings.default_config_path()
    file_cfg = settings.load_file(config_path)

    directory = _merge(args.directory, file_cfg, "server", "directory", None) \
        or os.path.expanduser("~")
    root = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(root):
        parser.error("directory does not exist: %s" % directory)

    host = _merge(args.host, file_cfg, "server", "host", "0.0.0.0")
    if args.localhost:
        host = "127.0.0.1"
    port = settings.as_int(_merge(args.port, file_cfg, "server", "port", 8000), 8000)
    read_only = settings.as_bool(
        _merge(args.read_only, file_cfg, "server", "read_only", False))
    quiet = settings.as_bool(
        _merge(args.quiet, file_cfg, "server", "quiet", False))

    show_hidden = settings.as_bool(
        _merge(args.show_hidden, file_cfg, "files", "show_hidden", False))
    use_trash = settings.as_bool(
        _merge(args.use_trash, file_cfg, "files", "trash", True))
    trash_dirname = settings.section_get(file_cfg, "trash", "dirname",
                                         settings.DEFAULT_TRASH_DIRNAME)

    drive_mode = _merge(args.drives, file_cfg, "drives", "mode",
                        settings.DEFAULT_DRIVE_MODE)
    if drive_mode not in ("accessible", "locked", "unlocked"):
        parser.error("drives mode must be accessible, locked or unlocked")
    file_drives_enabled = settings.as_bool(
        settings.section_get(file_cfg, "drives", "enabled", True))
    enable_drives = not (args.no_drives or not file_drives_enabled)

    extra_roots = args.root
    if extra_roots is None:
        extra_roots = settings.parse_roots_option(
            settings.section_get(file_cfg, "drives", "roots", ""))

    favorites_path = settings.section_get(file_cfg, "favorites", "file", None) \
        or settings.default_favorites_path()

    # auth
    user = _merge(args.user, file_cfg, "auth", "user", "user")
    if args.no_auth:
        password = None
    elif args.password is not None:
        password = args.password
    else:
        file_password = settings.section_get(file_cfg, "auth", "password", None)
        if file_password is None or file_password == "":
            password = generate_password()
        elif file_password.strip().lower() in ("none", "off", "disabled"):
            password = None
        else:
            password = file_password
    auth = Auth(username=user, password=password)

    config = Config(
        root=root,
        host=host,
        port=port,
        read_only=read_only,
        quiet=quiet,
        show_qr=not args.no_qr,
        version=__version__,
        home=root,
        show_hidden=show_hidden,
        use_trash=use_trash,
        trash_dirname=trash_dirname,
        favorites_file=favorites_path,
        drive_mode=drive_mode,
    )
    roots, drives = settings.build_roots(root, drive_mode, extra_roots, enable_drives)
    config.roots = roots
    config.drives = drives
    config.primary_key = "home"
    config.favorites = Favorites(favorites_path)

    try:
        run(config, auth, open_browser=args.open)
    except OSError as exc:
        parser.error("could not start server: %s" % exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())