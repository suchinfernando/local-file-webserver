"""Filesystem helpers: safe path resolution, listings, mime types and ranges."""

import mimetypes
import os
import posixpath

IMAGE_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico", ".avif",
}
VIDEO_EXT = {
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".ogv",
}
AUDIO_EXT = {
    ".mp3", ".wav", ".ogg", ".oga", ".m4a", ".flac", ".aac", ".opus",
}
PDF_EXT = {".pdf"}
TEXT_EXT = {
    ".txt", ".md", ".markdown", ".rst", ".log", ".csv", ".tsv", ".json",
    ".xml", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".conf", ".env",
    ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx",
    ".py", ".rb", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".hpp",
    ".cc", ".cs", ".php", ".sh", ".bash", ".zsh", ".fish", ".ps1", ".bat",
    ".sql", ".graphql", ".vue", ".svelte", ".lua", ".pl", ".r", ".swift",
    ".dockerfile", ".makefile", ".gradle", ".gitignore", ".editorconfig",
}
ARCHIVE_EXT = {
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".zst",
}

TEXT_MIME_PREFIXES = ("text/",)
TEXT_APPLICATION = {
    "application/json", "application/xml", "application/javascript",
    "application/x-javascript", "application/x-sh", "application/x-httpd-php",
}


class PathEscapeError(Exception):
    """Raised when a requested path would escape the served root."""


def normalize_rel(rel):
    """Normalize a URL-decoded relative path into a clean posix-style string."""
    if not rel:
        return ""
    rel = rel.replace("\\", "/")
    rel = posixpath.normpath("/" + rel)  # collapses .. and .
    rel = rel.lstrip("/")
    if rel == ".":
        return ""
    return rel


def resolve_within(root, rel):
    """Resolve ``rel`` under ``root`` and guarantee it stays inside ``root``.

    Follows symlinks via realpath, so symlinks pointing outside the root are
    rejected as well.
    """
    root_real = os.path.realpath(os.path.abspath(root))
    candidate = os.path.realpath(os.path.join(root_real, normalize_rel(rel)))
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        raise PathEscapeError("path escapes served root: %r" % rel)
    return candidate


def validate_name(name):
    """Validate a single path component (file or folder name)."""
    if name is None:
        raise ValueError("name is required")
    name = name.strip()
    if name in ("", ".", ".."):
        raise ValueError("invalid name")
    if "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("name must not contain path separators")
    return name


def kind_for(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    if ext in ARCHIVE_EXT:
        return "archive"
    return "file"


def content_type(path):
    ctype, encoding = mimetypes.guess_type(path)
    if ctype is None:
        ctype = "application/octet-stream"
    if encoding:
        ctype = "%s; charset=utf-8" % ctype
    elif ctype.startswith(TEXT_MIME_PREFIXES) or ctype in TEXT_APPLICATION:
        ctype = "%s; charset=utf-8" % ctype
    return ctype


def human_size(size):
    if size is None:
        return None
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return "%d %s" % (int(value), unit)
            return "%.1f %s" % (value, unit)
        value /= 1024
    return "%d B" % size


def is_hidden(name):
    return bool(name) and name.startswith(".")


def list_dir(root, rel, show_hidden=False, trash_dirname=".trash"):
    """Return a JSON-serializable listing of the directory at ``rel``."""
    rel = normalize_rel(rel)
    target = resolve_within(root, rel)
    if not os.path.isdir(target):
        raise NotADirectoryError(rel or "/")
    entries = []
    with os.scandir(target) as it:
        for entry in it:
            if entry.name == trash_dirname:
                continue
            if not show_hidden and is_hidden(entry.name):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            child = posixpath.join(rel, entry.name) if rel else entry.name
            entries.append({
                "name": entry.name,
                "path": child,
                "is_dir": is_dir,
                "is_symlink": entry.is_symlink(),
                "size": None if is_dir else st.st_size,
                "size_human": None if is_dir else human_size(st.st_size),
                "mtime": st.st_mtime,
                "kind": "dir" if is_dir else kind_for(entry.name),
            })
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    parent = posixpath.dirname(rel.rstrip("/"))
    if rel and parent:
        parent_path = parent
    elif rel:
        parent_path = ""
    else:
        parent_path = None
    return {
        "path": rel,
        "parent": parent_path,
        "is_root": rel == "",
        "entries": entries,
    }


def walk_search(root, rel, query, show_hidden=False, limit=500, max_depth=6,
                trash_dirname=".trash"):
    """Recursively search names under ``rel`` for ``query`` (case-insensitive)."""
    base = resolve_within(root, rel)
    if not os.path.isdir(base):
        raise NotADirectoryError(rel or "/")
    needle = (query or "").strip().lower()
    if not needle:
        return []
    base_depth = base.rstrip(os.sep).count(os.sep)
    results = []
    for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirnames[:] = []
        else:
            dirnames[:] = sorted(
                d for d in dirnames
                if (show_hidden or not is_hidden(d))
                and d != trash_dirname
                and not os.path.islink(os.path.join(dirpath, d))
            )
        for name in list(dirnames):
            if needle in name.lower():
                results.append(_make_entry(root, os.path.join(dirpath, name), True))
        for name in filenames:
            if not show_hidden and is_hidden(name):
                continue
            if needle in name.lower():
                results.append(_make_entry(root, os.path.join(dirpath, name), False))
        if len(results) >= limit:
            break
    results.sort(key=lambda e: (not e["is_dir"], e["path"].lower()))
    return results[:limit]


def _make_entry(root, abs_path, is_dir):
    rel = os.path.relpath(abs_path, os.path.abspath(root)).replace("\\", "/")
    try:
        st = os.stat(abs_path, follow_symlinks=False)
        size = None if is_dir else st.st_size
        mtime = st.st_mtime
    except OSError:
        size = None
        mtime = 0
    return {
        "name": os.path.basename(abs_path),
        "path": rel,
        "parent": posixpath.dirname(rel),
        "is_dir": is_dir,
        "is_symlink": os.path.islink(abs_path),
        "size": size,
        "size_human": human_size(size),
        "mtime": mtime,
        "kind": "dir" if is_dir else kind_for(os.path.basename(abs_path)),
    }


def parse_range(header, size):
    """Parse a single-range ``Range`` header.

    Returns ``(start, end)`` (inclusive) for a satisfiable range, or ``None``
    for a syntactically valid but unsatisfiable range. Raises ``ValueError``
    for malformed headers (callers should ignore those and serve the whole
    file).
    """
    if not header or not header.startswith("bytes="):
        raise ValueError("unsupported range unit")
    if size <= 0:
        return None
    spec = header[len("bytes="):].split(",")[0].strip()
    if "-" not in spec:
        raise ValueError("malformed range")
    first, _, last = spec.partition("-")
    if first == "" and last == "":
        raise ValueError("malformed range")
    try:
        if first == "":
            suffix = int(last)
            if suffix <= 0:
                return None
            start = max(0, size - suffix)
            end = size - 1
        else:
            start = int(first)
            end = size - 1 if last == "" else int(last)
    except ValueError:
        raise ValueError("malformed range")
    if start > end or start >= size:
        return None
    end = min(end, size - 1)
    return start, end