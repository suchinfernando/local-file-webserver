/* Front-end logic for local-file-webserver. Vanilla JS, no dependencies. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    breadcrumbs: $("breadcrumbs"), files: $("files"), empty: $("empty"), loading: $("loading"),
    search: $("search"), scopeBtn: $("scope-btn"), selectBtn: $("select-btn"),
    hiddenBtn: $("hidden-btn"), sort: $("sort"), sortDir: $("sort-dir"),
    viewGrid: $("view-grid"), viewList: $("view-list"), refresh: $("refresh-btn"),
    home: $("home-btn"), sidebarBtn: $("sidebar-btn"), sidebar: $("sidebar"),
    sidebarBackdrop: $("sidebar-backdrop"),
    sbStarred: $("sb-starred"), sbStarredSection: $("sb-starred-section"),
    sbPlaces: $("sb-places"), sbDrives: $("sb-drives"),
    sbDrivesSection: $("sb-drives-section"), sbTrash: $("sb-trash"),
    sbTrashSection: $("sb-trash-section"),
    uploadBtn: $("upload-btn"), mkdirBtn: $("mkdir-btn"), starBtn: $("star-btn"),
    shareBtn: $("share-btn"), themeBtn: $("theme-btn"),
    fileInput: $("file-input"), statusText: $("status-text"), statusRight: $("status-right"),
    actionbar: $("actionbar"), actionbarCount: $("actionbar-count"),
    abDownload: $("ab-download"), abDelete: $("ab-delete"), abEmpty: $("ab-empty"),
    abClear: $("ab-clear"),
    modal: $("modal"), modalTitle: $("modal-title"), modalBody: $("modal-body"),
    modalDownload: $("modal-download"),
    shareModal: $("share-modal"), qr: $("qr"), shareLan: $("share-lan"), shareLocal: $("share-local"),
    promptModal: $("prompt-modal"), promptTitle: $("prompt-title"),
    promptInput: $("prompt-input"), promptOk: $("prompt-ok"),
    uploads: $("uploads"), toasts: $("toasts")
  };

  var state = {
    root: "home", path: "", mode: "browse",
    entries: [], results: [], trash: [],
    info: null, places: null, favorites: [],
    sort: "name", dir: 1, view: "grid", filter: "",
    showHidden: false, scope: "folder",
    selecting: false, selection: {}, focus: null
  };
  var MENU = null;
  var reloadTimer = null;
  var searchTimer = null;
  var TEXT_PREVIEW_LIMIT = 2 * 1024 * 1024;

  // -- helpers -------------------------------------------------------------
  function enc(v) { return encodeURIComponent(v); }

  function u(path, params) {
    var qs = [];
    Object.keys(params || {}).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null) return;
      qs.push(enc(key) + "=" + enc(value));
    });
    return path + (qs.length ? "?" + qs.join("&") : "");
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(name) {
    return '<svg class="icon"><use href="#i-' + name + '"/></svg>';
  }

  function kindIcon(kind) {
    switch (kind) {
      case "image": return "image";
      case "video": return "video";
      case "audio": return "audio";
      case "pdf": return "pdf";
      case "text": return "text";
      case "archive": return "archive";
      default: return "file";
    }
  }

  function humanSize(size) {
    if (!size) return "0 B";
    var units = ["B", "KB", "MB", "GB", "TB"], value = size, i = 0;
    while (value >= 1024 && i < units.length - 1) { value /= 1024; i++; }
    return (i === 0 ? value : value.toFixed(1)) + " " + units[i];
  }

  function fmtDate(ts) {
    if (!ts) return "";
    try {
      return new Date(ts * 1000).toLocaleString([], {
        year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
      });
    } catch (e) { return ""; }
  }

  function basename(path) {
    if (!path) return "";
    return path.split("/").filter(Boolean).pop() || "";
  }

  function isReadOnly() { return !!(state.info && state.info.read_only); }
  function isMobile() { return window.matchMedia("(max-width: 760px)").matches; }
  function hiddenParam() { return state.showHidden ? 1 : undefined; }

  async function api(url, opts) {
    var res = await fetch(url, opts);
    var ctype = res.headers.get("content-type") || "";
    var body = null;
    if (ctype.indexOf("application/json") !== -1) {
      try { body = await res.json(); } catch (e) { body = null; }
    }
    if (!res.ok) {
      throw new Error((body && body.error) || (res.status + " " + res.statusText));
    }
    return body;
  }

  // -- toasts and modals ---------------------------------------------------
  function toast(message, type) {
    var node = document.createElement("div");
    node.className = "toast" + (type ? " " + type : "");
    node.textContent = message;
    el.toasts.appendChild(node);
    setTimeout(function () { node.remove(); }, 3200);
  }

  function show(modal) { modal.classList.remove("hidden"); }
  function hide(modal) { modal.classList.add("hidden"); }
  function openModal() { show(el.modal); }
  function closeModal() { hide(el.modal); el.modalBody.innerHTML = ""; }

  function askPrompt(title, value) {
    return new Promise(function (resolve) {
      el.promptTitle.textContent = title;
      el.promptInput.value = value || "";
      show(el.promptModal);
      setTimeout(function () { el.promptInput.focus(); el.promptInput.select(); }, 30);
      function done(result) {
        hide(el.promptModal);
        el.promptOk.onclick = null;
        el.promptInput.onkeydown = null;
        el.promptModal.querySelectorAll("[data-close]").forEach(function (b) { b.onclick = null; });
        resolve(result);
      }
      el.promptOk.onclick = function () { done(el.promptInput.value.trim()); };
      el.promptInput.onkeydown = function (e) { if (e.key === "Enter") done(el.promptInput.value.trim()); };
      el.promptModal.querySelectorAll("[data-close]").forEach(function (b) {
        b.onclick = function () { done(null); };
      });
    });
  }

  // -- sidebar -------------------------------------------------------------
  function rootLabel() {
    if (state.places) {
      var place = state.places.places.filter(function (p) {
        return p.root === state.root && p.path === "";
      })[0];
      if (place) return place.label;
      var drive = state.places.drives.filter(function (d) { return d.key === state.root; })[0];
      if (drive) return drive.label;
    }
    return (state.info && state.info.root) || "Home";
  }

  function sbItem(opts) {
    return '<li class="sb-item" data-root="' + escapeHtml(opts.root) +
      '" data-path="' + escapeHtml(opts.path || "") +
      '" data-trash="' + (opts.trash ? "1" : "0") +
      '" title="' + escapeHtml(opts.abs || opts.label) + '">' +
      icon(opts.icon || "folder") +
      '<span class="sb-item-label">' + escapeHtml(opts.label) + "</span>" +
      (opts.suffix || "") + "</li>";
  }

  function driveItem(drive) {
    var cap = "";
    if (drive.usage && drive.usage.total) {
      var pct = Math.max(0, Math.min(100, Math.round((drive.usage.used / drive.usage.total) * 100)));
      cap = '<span class="sb-cap"><span style="width:' + pct + '%"></span></span>';
    }
    var lock = drive.accessible ? "" : '<svg class="icon sb-lock"><use href="#i-lock"/></svg>';
    var classes = "sb-item" + (drive.accessible ? "" : " locked");
    return '<li class="' + classes + '" data-root="' + escapeHtml(drive.key || "") +
      '" data-path="" data-trash="0" data-locked="' + (drive.accessible ? "0" : "1") +
      '" title="' + escapeHtml(drive.path + (drive.device ? "  (" + drive.device + ")" : "")) + '">' +
      icon("drive") + '<span class="sb-item-label">' + escapeHtml(drive.label) + "</span>" +
      cap + lock + "</li>";
  }

  function renderSidebar() {
    var favorites = state.favorites || [];
    el.sbStarred.innerHTML = favorites.map(function (f) {
      return sbItem({ root: f.root, path: f.path, label: f.label, icon: "star", abs: f.abs });
    }).join("");
    el.sbStarredSection.classList.toggle("empty", favorites.length === 0);

    var places = (state.places && state.places.places) || [];
    el.sbPlaces.innerHTML = places.map(function (p) {
      return sbItem({ root: p.root, path: p.path, label: p.label, icon: p.icon, abs: p.abs });
    }).join("");

    var drives = (state.places && state.places.drives) || [];
    el.sbDrives.innerHTML = drives.map(driveItem).join("");
    el.sbDrivesSection.classList.toggle("empty", drives.length === 0);

    var trash = (state.places && state.places.trash) || [];
    el.sbTrash.innerHTML = trash.map(function (t) {
      return sbItem({
        root: t.root, path: "", label: t.label, icon: "trash", trash: true,
        suffix: '<span class="sb-count">' + t.count + "</span>",
      });
    }).join("");
    el.sbTrashSection.classList.toggle("empty", trash.length === 0);

    updateActive();
  }

  function updateActive() {
    el.sidebar.querySelectorAll(".sb-item").forEach(function (item) {
      var isTrash = item.dataset.trash === "1";
      var match = !isTrash && item.dataset.root === state.root &&
        (item.dataset.path || "") === state.path && state.mode !== "trash";
      item.classList.toggle("active", match);
    });
  }

  function setSidebar(open) {
    if (isMobile()) {
      document.body.classList.toggle("sidebar-open", open);
      el.sidebarBackdrop.classList.toggle("hidden", !open);
    } else {
      document.body.classList.toggle("sidebar-collapsed", !open);
      localStorage.setItem("lfw-sidebar", open ? "open" : "collapsed");
    }
  }
  function toggleSidebar() {
    var open = isMobile()
      ? document.body.classList.contains("sidebar-open")
      : !document.body.classList.contains("sidebar-collapsed");
    setSidebar(!open);
  }
  function closeMobileSidebar() { if (isMobile()) setSidebar(false); }

  // -- rendering -----------------------------------------------------------
  function renderCrumbs(path) {
    var parts = path ? path.split("/") : [];
    var html = '<button class="crumb' + (parts.length ? "" : " current") +
      '" data-root="' + escapeHtml(state.root) + '" data-path="">' +
      escapeHtml(rootLabel()) + "</button>";
    var acc = "";
    parts.forEach(function (part, i) {
      acc = acc ? acc + "/" + part : part;
      var current = i === parts.length - 1;
      html += '<span class="crumb-sep">/</span>' +
        '<button class="crumb' + (current ? " current" : "") +
        '" data-root="' + escapeHtml(state.root) + '" data-path="' + escapeHtml(acc) + '">' +
        escapeHtml(part) + "</button>";
    });
    el.breadcrumbs.innerHTML = html;
    el.breadcrumbs.querySelectorAll(".crumb").forEach(function (button) {
      button.addEventListener("click", function () {
        go(button.dataset.root, button.dataset.path);
      });
    });
  }

  function sortEntries(list) {
    var dir = state.dir;
    function key(entry) {
      switch (state.sort) {
        case "size": return entry.is_dir ? -1 : (entry.size || 0);
        case "mtime": return entry.mtime || 0;
        case "kind": return entry.is_dir ? "" : entry.kind;
        default: return entry.name.toLowerCase();
      }
    }
    return list.slice().sort(function (a, b) {
      if (a.is_dir !== b.is_dir) return a.is_dir ? -1 : 1;
      var ka = key(a), kb = key(b);
      if (ka < kb) return -dir;
      if (ka > kb) return dir;
      return a.name.localeCompare(b.name);
    });
  }

  function isSelected(path) { return !!state.selection[path]; }

  function tileHtml(entry, mode) {
    var img = "", hasImg = false;
    if (entry.kind === "image" && !entry.is_dir) {
      img = '<img loading="lazy" alt="" src="' + u("/api/raw", { root: state.root, path: entry.path }) + '">';
      hasImg = true;
    }
    var meta = entry.is_dir
      ? "Folder"
      : (entry.size_human || humanSize(entry.size)) + " \u00b7 " + fmtDate(entry.mtime);
    var where = (mode === "search" && entry.parent)
      ? '<div class="tile-where">' + escapeHtml(entry.parent) + "</div>" : "";
    return '<li class="tile' + (isSelected(entry.path) ? " selected" : "") +
      '" data-path="' + escapeHtml(entry.path) +
      '" data-name="' + escapeHtml(entry.name) +
      '" data-dir="' + (entry.is_dir ? "1" : "0") +
      '" data-kind="' + entry.kind + '">' +
      '<span class="tile-check">' + icon("check") + "</span>" +
      '<div class="tile-thumb' + (hasImg ? " has-img" : "") + '">' +
        '<span class="tile-icon">' + icon(entry.is_dir ? "folder" : kindIcon(entry.kind)) + "</span>" + img +
      "</div>" +
      '<div class="tile-name">' + escapeHtml(entry.name) + "</div>" + where +
      '<div class="tile-meta">' + escapeHtml(meta) + "</div>" +
      '<button class="icon-btn tile-menu" data-menu title="Actions" aria-label="Actions">' + icon("dots") + "</button>" +
      "</li>";
  }

  function trashTile(item) {
    var meta = "Deleted " + fmtDate(item.deleted_at) + (item.is_dir ? " \u00b7 Folder" : "");
    return '<li class="tile" data-id="' + escapeHtml(item.id) +
      '" data-name="' + escapeHtml(item.name) +
      '" data-dir="' + (item.is_dir ? "1" : "0") + '" data-trash="1">' +
      '<div class="tile-thumb"><span class="tile-icon">' +
        icon(item.is_dir ? "folder" : "file") + "</span></div>" +
      '<div class="tile-name">' + escapeHtml(item.name) + "</div>" +
      '<div class="tile-meta">' + escapeHtml(meta) + "</div>" +
      '<div class="tile-where">from ' + escapeHtml(item.original) + "</div>" +
      '<button class="icon-btn tile-menu" data-menu title="Actions" aria-label="Actions">' + icon("dots") + "</button>" +
      "</li>";
  }

  function render() {
    if (state.mode === "trash") {
      el.files.className = "files " + state.view;
      el.files.innerHTML = state.trash.map(trashTile).join("");
      setEmpty(state.trash.length === 0, "Trash is empty.");
      updateActionbar();
      return;
    }
    var list = state.mode === "search" ? state.results.slice() : state.entries.slice();
    if (state.mode === "search") {
      el.files.className = "files " + state.view;
      el.files.innerHTML = sortEntries(list).map(function (e) { return tileHtml(e, "search"); }).join("");
      setEmpty(list.length === 0, "No matches for \u201c" + state.filter + "\u201d.");
    } else {
      var term = state.filter.toLowerCase();
      list = list.filter(function (e) { return !term || e.name.toLowerCase().indexOf(term) !== -1; });
      el.files.className = "files " + state.view;
      el.files.innerHTML = sortEntries(list).map(function (e) { return tileHtml(e, "browse"); }).join("");
      setEmpty(list.length === 0, term ? "No matches for \u201c" + state.filter + "\u201d."
        : "This folder is empty.");
    }
    updateActionbar();
  }

  function setEmpty(showEmpty, message) {
    el.empty.classList.toggle("hidden", !showEmpty);
    if (showEmpty) el.empty.textContent = message;
  }

  function updateStatus() {
    if (state.mode === "search") {
      el.statusText.textContent = state.results.length + " result" +
        (state.results.length === 1 ? "" : "s") + " for \u201c" + state.filter + "\u201d";
    } else if (state.mode === "trash") {
      el.statusText.textContent = state.trash.length + " item" +
        (state.trash.length === 1 ? "" : "s") + " in trash";
    } else {
      var folders = 0, files = 0, total = 0;
      state.entries.forEach(function (e) {
        if (e.is_dir) folders++; else { files++; total += e.size || 0; }
      });
      el.statusText.textContent = folders + " folder" + (folders === 1 ? "" : "s") +
        ", " + files + " file" + (files === 1 ? "" : "s") + " \u00b7 " + humanSize(total);
    }
    if (state.info) {
      var bits = ["v" + state.info.version];
      if (state.info.read_only) bits.push("read-only");
      if (state.info.auth_enabled) bits.push("auth: " + state.info.username);
      el.statusRight.textContent = bits.join(" \u00b7 ");
    }
  }

  function showLoading(on) { el.loading.classList.toggle("hidden", !on); }

  // -- data loading --------------------------------------------------------
  async function load(path, opts) {
    opts = opts || {};
    if (!opts.silent) showLoading(true);
    try {
      var data = await api(u("/api/list", {
        root: state.root, path: path || "", hidden: hiddenParam(),
      }));
      state.mode = "browse";
      state.path = data.path;
      state.entries = data.entries;
      exitSelection(false);
      if (!opts.keepFilter) { state.filter = ""; el.search.value = ""; }
      renderCrumbs(data.path);
      render();
      updateStatus();
      updateActive();
      updateStar();
      try { history.replaceState(null, "", "#" + enc(state.root) + ":" + encodeURI(data.path)); } catch (e) {}
    } catch (err) {
      toast(err.message, "error");
    } finally {
      showLoading(false);
    }
  }

  function go(root, path) {
    state.root = root || state.root;
    closeMobileSidebar();
    load(path || "");
  }

  function scheduleReload() {
    clearTimeout(reloadTimer);
    reloadTimer = setTimeout(function () { load(state.path, { silent: true }); }, 250);
  }

  async function refreshFavorites() {
    try {
      var data = await api("/api/favorites");
      state.favorites = data.favorites || [];
    } catch (e) { state.favorites = []; }
    renderSidebar();
  }

  async function refreshPlaces() {
    try {
      state.places = await api("/api/places");
    } catch (e) { /* keep previous */ }
    renderSidebar();
  }

  // -- preview -------------------------------------------------------------
  async function preview(path, name, kind) {
    el.modalTitle.textContent = name;
    el.modalDownload.href = u("/api/download", { root: state.root, path: path });
    var raw = u("/api/raw", { root: state.root, path: path });
    var body = el.modalBody;
    body.innerHTML = "";

    if (kind === "image") {
      body.innerHTML = '<img alt="" src="' + raw + '">';
    } else if (kind === "video") {
      body.innerHTML = '<video controls autoplay src="' + raw + '"></video>';
    } else if (kind === "audio") {
      body.innerHTML = '<audio controls autoplay src="' + raw + '"></audio>';
    } else if (kind === "pdf") {
      body.innerHTML = '<iframe title="PDF" src="' + raw + '"></iframe>';
    } else if (kind === "text") {
      var entry = state.entries.filter(function (e) { return e.path === path; })[0];
      if (entry && entry.size > TEXT_PREVIEW_LIMIT) {
        body.innerHTML = '<div class="unsupported">File is too large to preview.<br>Use Download.</div>';
      } else {
        body.innerHTML = "<pre>Loading\u2026</pre>";
        try {
          var res = await fetch(raw);
          if (!res.ok) throw new Error("Could not load file");
          body.innerHTML = "<pre>" + escapeHtml(await res.text()) + "</pre>";
        } catch (err) {
          body.innerHTML = '<div class="unsupported">' + escapeHtml(err.message) + "</div>";
        }
      }
    } else {
      body.innerHTML = '<div class="unsupported">No preview available for this file type.<br>Use Download.</div>';
    }
    openModal();
  }

  // -- context menu --------------------------------------------------------
  function closeMenu() { if (MENU) { MENU.remove(); MENU = null; } }

  function openMenu(anchor, tile) {
    closeMenu();
    var items = [];
    if (tile.dataset.trash === "1") {
      var id = tile.dataset.id;
      items.push(["Restore", function () { trashRestore(id); }]);
      items.push(["Delete permanently", function () { trashDelete(id); }, "danger"]);
    } else {
      var path = tile.dataset.path, name = tile.dataset.name;
      var isDir = tile.dataset.dir === "1";
      if (isDir) items.push(["Open", function () { load(path); }]);
      else items.push(["Preview", function () { preview(path, name, tile.dataset.kind); }]);
      items.push(["Download" + (isDir ? " (ZIP)" : ""),
        function () { window.location.href = u("/api/download", { root: state.root, path: path }); }]);
      var starred = isFavorite(state.root, path);
      items.push([starred ? "Remove star" : "Add star",
        function () { toggleFavorite(state.root, path, name); }]);
      if (!isReadOnly()) {
        items.push(["Rename", function () { renameEntry(path, name); }]);
        items.push(["Delete", function () { removeEntry(path, name); }, "danger"]);
      }
    }
    if (!items.length) return;
    var menu = document.createElement("div");
    menu.className = "ctx-menu";
    items.forEach(function (item) {
      var button = document.createElement("button");
      button.textContent = item[0];
      if (item[2]) button.className = item[2];
      button.addEventListener("click", function () { closeMenu(); item[1](); });
      menu.appendChild(button);
    });
    document.body.appendChild(menu);
    var rect = anchor.getBoundingClientRect();
    var top = Math.min(rect.bottom + 4, window.innerHeight - menu.offsetHeight - 8);
    var left = Math.min(rect.left, window.innerWidth - menu.offsetWidth - 8);
    menu.style.top = Math.max(8, top) + "px";
    menu.style.left = Math.max(8, left) + "px";
    MENU = menu;
  }

  // -- selection -----------------------------------------------------------
  function selectionPaths() { return Object.keys(state.selection); }
  function selectionCount() { return selectionPaths().length; }

  function toggleSelecting() {
    state.selecting = !state.selecting;
    document.body.classList.toggle("selecting", state.selecting);
    el.selectBtn.classList.toggle("active", state.selecting);
    if (!state.selecting) state.selection = {};
    render();
  }

  function toggleSelectTile(tile) {
    var path = tile.dataset.path;
    if (state.selection[path]) delete state.selection[path];
    else state.selection[path] = { path: path, name: tile.dataset.name, isDir: tile.dataset.dir === "1" };
    tile.classList.toggle("selected", isSelected(path));
    updateActionbar();
  }

  function exitSelection(renderIt) {
    state.selecting = false;
    state.selection = {};
    document.body.classList.remove("selecting");
    el.selectBtn.classList.remove("active");
    if (renderIt !== false) render();
  }

  function updateActionbar() {
    if (state.mode === "trash") {
      el.actionbar.classList.remove("hidden");
      el.actionbarCount.textContent = "Trash";
      el.abDownload.classList.add("hidden");
      el.abDelete.classList.add("hidden");
      el.abClear.classList.add("hidden");
      el.abEmpty.classList.remove("hidden");
      return;
    }
    el.abEmpty.classList.add("hidden");
    if (!state.selecting) { el.actionbar.classList.add("hidden"); return; }
    el.actionbar.classList.remove("hidden");
    el.abDownload.classList.remove("hidden");
    el.abDelete.classList.remove("hidden");
    el.abClear.classList.remove("hidden");
    el.actionbarCount.textContent = selectionCount() + " selected";
  }

  function saveBlob(blob, name) {
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  async function downloadSelection() {
    var paths = selectionPaths();
    if (!paths.length) return;
    var name = (basename(state.path) || "selection") + ".zip";
    try {
      var res = await fetch("/api/archive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root: state.root, paths: paths, name: name }),
      });
      if (!res.ok) {
        var detail = null;
        try { detail = await res.json(); } catch (e) {}
        toast((detail && detail.error) || "Download failed", "error");
        return;
      }
      saveBlob(await res.blob(), name);
    } catch (e) { toast("Download failed", "error"); }
  }

  async function deleteSelection() {
    var paths = selectionPaths();
    if (!paths.length) return;
    if (!window.confirm("Delete " + paths.length + " item(s)?")) return;
    var ok = 0;
    for (var i = 0; i < paths.length; i++) {
      try {
        await api(u("/api/delete", { root: state.root, path: paths[i] }), { method: "POST" });
        ok++;
      } catch (err) { toast(err.message, "error"); }
    }
    exitSelection(false);
    toast("Deleted " + ok + " item(s)", "ok");
    scheduleReload();
    refreshPlaces();
  }

  // -- actions -------------------------------------------------------------
  async function renameEntry(path, currentName) {
    var name = await askPrompt("Rename", currentName);
    if (!name || name === currentName) return;
    try {
      await api(u("/api/rename", { root: state.root, path: path, name: name }), { method: "POST" });
      toast("Renamed", "ok");
      scheduleReload();
    } catch (err) { toast(err.message, "error"); }
  }

  async function removeEntry(path, name) {
    if (!window.confirm('Delete "' + name + '"? It will be moved to trash.')) return;
    try {
      await api(u("/api/delete", { root: state.root, path: path }), { method: "POST" });
      toast("Moved to trash", "ok");
      scheduleReload();
      refreshPlaces();
    } catch (err) { toast(err.message, "error"); }
  }

  async function makeFolder() {
    var name = await askPrompt("New folder name", "");
    if (!name) return;
    try {
      await api(u("/api/mkdir", { root: state.root, path: state.path, name: name }), { method: "POST" });
      toast("Folder created", "ok");
      scheduleReload();
    } catch (err) { toast(err.message, "error"); }
  }

  function isFavorite(root, path) {
    return (state.favorites || []).some(function (f) {
      return f.root === root && f.path === path;
    });
  }

  async function toggleFavorite(root, path, label) {
    try {
      if (isFavorite(root, path)) {
        await api(u("/api/favorites", { root: root, path: path }), { method: "DELETE" });
      } else {
        await api(u("/api/favorites", { root: root, path: path, label: label || path || rootLabel() }),
          { method: "POST" });
      }
      await refreshFavorites();
    } catch (err) { toast(err.message, "error"); }
    updateStar();
  }

  function updateStar() {
    var path = state.mode === "browse" ? state.path : null;
    var active = path !== null && isFavorite(state.root, path);
    var use = el.starBtn.querySelector("use");
    if (use) use.setAttribute("href", active ? "#i-star-filled" : "#i-star");
    el.starBtn.classList.toggle("active", active);
  }

  // -- trash ---------------------------------------------------------------
  async function openTrash(root) {
    showLoading(true);
    try {
      var data = await api(u("/api/trash", { root: root }));
      state.root = root;
      state.mode = "trash";
      state.trash = data.entries || [];
      state.path = "";
      exitSelection(false);
      renderCrumbs("");
      renderCrumbsTrash();
      render();
      updateStatus();
      updateActive();
      closeMobileSidebar();
    } catch (err) { toast(err.message, "error"); }
    finally { showLoading(false); }
  }

  function renderCrumbsTrash() {
    el.breadcrumbs.innerHTML =
      '<button class="crumb" data-root="' + escapeHtml(state.root) + '" data-path="">' +
      escapeHtml(rootLabel()) + "</button>" +
      '<span class="crumb-sep">/</span>' +
      '<span class="crumb current">Trash</span>';
    el.breadcrumbs.querySelector(".crumb").addEventListener("click", function () {
      go(state.root, "");
    });
  }

  async function trashRestore(id) {
    try {
      await api(u("/api/trash/restore", { root: state.root, id: id }), { method: "POST" });
      toast("Restored", "ok");
      await openTrash(state.root);
      refreshPlaces();
    } catch (err) { toast(err.message, "error"); }
  }

  async function trashDelete(id) {
    if (!window.confirm("Delete this item permanently?")) return;
    try {
      await api(u("/api/trash/empty", { root: state.root, id: id }), { method: "POST" });
      toast("Deleted", "ok");
      await openTrash(state.root);
      refreshPlaces();
    } catch (err) { toast(err.message, "error"); }
  }

  async function trashEmpty() {
    if (!window.confirm("Empty trash? This cannot be undone.")) return;
    try {
      await api(u("/api/trash/empty", { root: state.root }), { method: "POST" });
      toast("Trash emptied", "ok");
      await openTrash(state.root);
      refreshPlaces();
    } catch (err) { toast(err.message, "error"); }
  }

  // -- search --------------------------------------------------------------
  function toggleScope() {
    state.scope = state.scope === "folder" ? "recursive" : "folder";
    var recursive = state.scope === "recursive";
    el.scopeBtn.classList.toggle("recursive", recursive);
    el.scopeBtn.querySelector("use").setAttribute("href", recursive ? "#i-globe" : "#i-folder");
    el.scopeBtn.title = "Search scope: " + (recursive ? "this folder and below" : "this folder");
    el.search.placeholder = recursive ? "Search this folder and below..." : "Filter in this folder...";
    if (!recursive) {
      if (state.mode === "search") state.mode = "browse";
      state.filter = "";
      el.search.value = "";
      render();
    }
  }

  async function runSearch(query) {
    if (!query) { load(state.path, { silent: true, keepFilter: true }); return; }
    showLoading(true);
    try {
      var data = await api(u("/api/search", {
        root: state.root, path: state.path, q: query, hidden: hiddenParam(),
      }));
      state.mode = "search";
      state.filter = query;
      state.results = data.results || [];
      render();
      updateStatus();
    } catch (err) { toast(err.message, "error"); }
    finally { showLoading(false); }
  }

  // -- upload --------------------------------------------------------------
  function uploadFiles(fileList) {
    var files = Array.prototype.slice.call(fileList || []);
    if (!files.length) return;
    if (isReadOnly()) { toast("Server is read-only", "error"); return; }
    files.forEach(uploadOne);
  }

  function uploadOne(file) {
    var item = document.createElement("div");
    item.className = "upload-item";
    item.innerHTML = '<div class="upload-name">' + escapeHtml(file.name) +
      '</div><div class="upload-bar"><span></span></div>';
    el.uploads.appendChild(item);
    var bar = item.querySelector(".upload-bar span");

    function finish(ok, message) {
      if (ok) {
        bar.style.width = "100%";
        item.classList.add("done");
        setTimeout(function () { item.remove(); }, 1200);
        scheduleReload();
      } else {
        item.classList.add("error");
        toast(message || "Upload failed", "error");
        setTimeout(function () { item.remove(); }, 4000);
      }
    }

    function send(overwrite) {
      var url = u("/api/upload", {
        root: state.root, path: state.path, name: file.name,
        overwrite: overwrite ? 1 : undefined,
      });
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url);
      xhr.upload.addEventListener("progress", function (e) {
        if (e.lengthComputable) bar.style.width = Math.round((e.loaded / e.total) * 100) + "%";
      });
      xhr.addEventListener("load", function () {
        if (xhr.status >= 200 && xhr.status < 300) { finish(true); return; }
        var msg = "Upload failed";
        try { var parsed = JSON.parse(xhr.responseText); if (parsed.error) msg = parsed.error; } catch (e) {}
        if (xhr.status === 409 && !overwrite &&
            window.confirm('"' + file.name + '" already exists. Overwrite?')) {
          send(true);
          return;
        }
        finish(false, msg);
      });
      xhr.addEventListener("error", function () { finish(false, "Network error during upload"); });
      xhr.send(file);
    }
    send(false);
  }

  // -- share ---------------------------------------------------------------
  function openShare() {
    var info = state.info;
    if (info) {
      el.shareLan.textContent = info.lan_url;
      el.shareLocal.textContent = info.local_url;
      if (typeof QRCode !== "undefined") {
        try { el.qr.innerHTML = QRCode.toSvg(info.lan_url, { border: 2 }); }
        catch (e) { el.qr.textContent = ""; }
      }
    }
    show(el.shareModal);
  }

  // -- view / theme --------------------------------------------------------
  function applyView() {
    el.files.className = "files " + state.view;
    el.viewGrid.classList.toggle("active", state.view === "grid");
    el.viewList.classList.toggle("active", state.view === "list");
  }

  function initTheme() {
    var stored = localStorage.getItem("lfw-theme");
    if (!stored) {
      stored = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light" : "dark";
    }
    document.documentElement.setAttribute("data-theme", stored);
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute("data-theme");
    var next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("lfw-theme", next);
  }

  function updateHiddenBtn() {
    var use = el.hiddenBtn.querySelector("use");
    if (use) use.setAttribute("href", state.showHidden ? "#i-eye" : "#i-eye-off");
    el.hiddenBtn.classList.toggle("active", state.showHidden);
    el.hiddenBtn.title = state.showHidden ? "Hide hidden files" : "Show hidden files";
  }

  // -- keyboard ------------------------------------------------------------
  function goUp() {
    if (state.mode !== "browse") { load(state.path, { silent: true }); return; }
    if (!state.path) return;
    var parts = state.path.split("/");
    parts.pop();
    load(parts.join("/"));
  }

  function onKeydown(ev) {
    var tag = (ev.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || ev.target.isContentEditable) return;
    if (ev.key === "Escape") {
      closeMenu();
      if (!el.modal.classList.contains("hidden")) closeModal();
      if (!el.shareModal.classList.contains("hidden")) hide(el.shareModal);
      return;
    }
    if (ev.key === "Backspace") { ev.preventDefault(); goUp(); return; }
    if (ev.key === "/") { ev.preventDefault(); el.search.focus(); return; }
    if (state.focus && !isReadOnly() && state.mode !== "trash") {
      if (ev.key === "F2") { ev.preventDefault(); renameEntry(state.focus.path, state.focus.name); }
      else if (ev.key === "Delete") { ev.preventDefault(); removeEntry(state.focus.path, state.focus.name); }
    }
  }

  // -- init ----------------------------------------------------------------
  function parseHash() {
    var hash = decodeURI(location.hash.replace(/^#/, ""));
    if (!hash) return { root: null, path: "" };
    var idx = hash.indexOf(":");
    if (idx < 0) return { root: null, path: hash };
    return { root: hash.slice(0, idx), path: hash.slice(idx + 1) };
  }

  function bind() {
    el.home.addEventListener("click", function () { go(state.info ? state.info.primary_key : "home", ""); });
    el.sidebarBtn.addEventListener("click", toggleSidebar);
    el.sidebarBackdrop.addEventListener("click", function () { setSidebar(false); });
    el.refresh.addEventListener("click", function () { load(state.path, { silent: true }); });
    el.uploadBtn.addEventListener("click", function () { el.fileInput.click(); });
    el.mkdirBtn.addEventListener("click", makeFolder);
    el.starBtn.addEventListener("click", function () {
      if (state.mode !== "browse") return;
      toggleFavorite(state.root, state.path, basename(state.path) || rootLabel());
    });
    el.shareBtn.addEventListener("click", openShare);
    el.themeBtn.addEventListener("click", toggleTheme);
    el.scopeBtn.addEventListener("click", toggleScope);
    el.selectBtn.addEventListener("click", toggleSelecting);
    el.hiddenBtn.addEventListener("click", function () {
      state.showHidden = !state.showHidden;
      localStorage.setItem("lfw-hidden", state.showHidden ? "1" : "0");
      updateHiddenBtn();
      load(state.path, { silent: true, keepFilter: true });
    });

    el.abDownload.addEventListener("click", downloadSelection);
    el.abDelete.addEventListener("click", deleteSelection);
    el.abEmpty.addEventListener("click", trashEmpty);
    el.abClear.addEventListener("click", function () { exitSelection(true); });

    el.fileInput.addEventListener("change", function () {
      uploadFiles(el.fileInput.files);
      el.fileInput.value = "";
    });

    el.search.addEventListener("input", function () {
      clearTimeout(searchTimer);
      var value = el.search.value;
      if (state.scope === "recursive") {
        searchTimer = setTimeout(function () {
          if (value) runSearch(value);
          else load(state.path, { silent: true, keepFilter: true });
        }, 260);
      } else {
        searchTimer = setTimeout(function () {
          if (state.mode === "search") state.mode = "browse";
          state.filter = value;
          render();
        }, 120);
      }
    });

    el.sort.addEventListener("change", function () { state.sort = el.sort.value; render(); });
    el.sortDir.addEventListener("click", function () {
      state.dir = -state.dir;
      el.sortDir.classList.toggle("desc", state.dir === -1);
      render();
    });
    el.viewGrid.addEventListener("click", function () {
      state.view = "grid"; localStorage.setItem("lfw-view", state.view); applyView();
    });
    el.viewList.addEventListener("click", function () {
      state.view = "list"; localStorage.setItem("lfw-view", state.view); applyView();
    });

    el.files.addEventListener("click", function (ev) {
      var tile = ev.target.closest(".tile");
      if (!tile) return;
      var menuBtn = ev.target.closest("[data-menu]");
      if (menuBtn) { ev.stopPropagation(); openMenu(menuBtn, tile); return; }
      if (tile.dataset.trash === "1") { openMenu(tile, tile); return; }
      state.focus = { path: tile.dataset.path, name: tile.dataset.name, isDir: tile.dataset.dir === "1" };
      if (state.selecting) { toggleSelectTile(tile); return; }
      if (state.mode === "search") {
        if (tile.dataset.dir === "1") load(tile.dataset.path);
        else preview(tile.dataset.path, tile.dataset.name, tile.dataset.kind);
        return;
      }
      if (tile.dataset.dir === "1") load(tile.dataset.path);
      else preview(tile.dataset.path, tile.dataset.name, tile.dataset.kind);
    });

    el.sidebar.addEventListener("click", function (ev) {
      var item = ev.target.closest(".sb-item");
      if (!item) return;
      if (item.dataset.locked === "1") {
        toast("This drive is locked. Start with --drives unlocked (or --root) to enable it.", "error");
        return;
      }
      if (item.dataset.trash === "1") { openTrash(item.dataset.root); return; }
      go(item.dataset.root, item.dataset.path || "");
    });

    document.addEventListener("click", function (ev) {
      if (MENU && !ev.target.closest(".ctx-menu")) closeMenu();
    });
    window.addEventListener("scroll", closeMenu, true);
    window.addEventListener("resize", closeMenu);

    document.querySelectorAll("[data-close]").forEach(function (button) {
      button.addEventListener("click", function () {
        var modal = button.closest(".modal");
        if (modal === el.promptModal) return;
        if (modal === el.modal) closeModal(); else if (modal) hide(modal);
      });
    });

    document.addEventListener("keydown", onKeydown);

    window.addEventListener("resize", function () {
      if (!isMobile()) {
        document.body.classList.remove("sidebar-open");
        el.sidebarBackdrop.classList.add("hidden");
      } else {
        document.body.classList.remove("sidebar-collapsed");
      }
    });

    var dragDepth = 0;
    function hasFiles(ev) {
      return ev.dataTransfer && Array.prototype.indexOf.call(ev.dataTransfer.types || [], "Files") !== -1;
    }
    window.addEventListener("dragenter", function (ev) {
      if (!hasFiles(ev)) return;
      dragDepth++; document.body.classList.add("dragging");
    });
    window.addEventListener("dragover", function (ev) { if (hasFiles(ev)) ev.preventDefault(); });
    window.addEventListener("dragleave", function () {
      dragDepth--; if (dragDepth <= 0) { dragDepth = 0; document.body.classList.remove("dragging"); }
    });
    window.addEventListener("drop", function (ev) {
      if (!hasFiles(ev)) return;
      ev.preventDefault();
      dragDepth = 0; document.body.classList.remove("dragging");
      uploadFiles(ev.dataTransfer.files);
    });

    window.addEventListener("hashchange", function () {
      var target = parseHash();
      if (target.root) state.root = target.root;
      if (target.path !== state.path || state.mode !== "browse") load(target.path);
    });
  }

  async function init() {
    initTheme();
    state.view = localStorage.getItem("lfw-view") || "grid";
    applyView();
    if (!isMobile() && localStorage.getItem("lfw-sidebar") === "collapsed") {
      document.body.classList.add("sidebar-collapsed");
    }
    if (localStorage.getItem("lfw-hidden") === "1") state.showHidden = true;
    updateHiddenBtn();

    try { state.info = await api("/api/info"); } catch (e) {}
    if (state.info) {
      state.root = state.info.primary_key || "home";
      if (state.info.show_hidden) state.showHidden = true;
      updateHiddenBtn();
      if (isReadOnly()) {
        el.uploadBtn.classList.add("hidden");
        el.mkdirBtn.classList.add("hidden");
      }
    }
    bind();
    await Promise.all([refreshPlaces(), refreshFavorites()]);

    var target = parseHash();
    if (target.root) state.root = target.root;
    await load(target.path || "");
  }

  init();
})();