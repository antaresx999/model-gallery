(function () {
  "use strict";
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";
  var body = document.body;
  var shareToken = body.getAttribute("data-share");

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  // Gemeinsamer API-Aufruf: CSRF-Token und ggf. Permalink-Token mitsenden
  function api(url, payload) {
    var headers = { "X-CSRF-Token": csrf };
    if (shareToken) headers["X-Share-Token"] = shareToken;
    var opts = { method: payload === undefined ? "GET" : "POST", headers: headers, credentials: "same-origin" };
    if (payload !== undefined) {
      headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(payload);
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.status === 401 && data.identify) { openDialog("guest-dialog"); throw new Error("identify"); }
        if (!r.ok) { alert(data.error || "Fehler " + r.status); throw new Error(data.error); }
        return data;
      });
    });
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // ---------- Dialoge ----------
  function openDialog(id) {
    var d = document.getElementById(id);
    if (d && d.showModal) { d.showModal(); var f = d.querySelector("input:not([type=hidden]), textarea"); if (f) f.focus(); }
  }
  document.addEventListener("click", function (ev) {
    var o = ev.target.closest("[data-open-dialog]");
    if (o) { ev.preventDefault(); openDialog(o.getAttribute("data-open-dialog")); return; }
    var c = ev.target.closest("[data-close-dialog]");
    if (c) { c.closest("dialog").close(); }
  });

  // ---------- Auswahl (Herz) ----------
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-fav]");
    if (!btn) return;
    ev.preventDefault();
    if (btn.disabled) return;
    if (body.hasAttribute("data-needs-identity")) { openDialog("guest-dialog"); return; }
    var id = btn.getAttribute("data-fav");
    api("/api/auswahl/" + id, {}).then(function (data) {
      $all('[data-fav="' + id + '"]').forEach(function (b) {
        b.setAttribute("aria-pressed", data.selected ? "true" : "false");
        b.classList.toggle("is-fav", data.selected);
        var fig = b.closest(".thumb");
        if (fig) fig.classList.toggle("is-fav", data.selected);
      });
      var film = $(".filmstrip .current");
      if (film) film.classList.toggle("is-fav", data.selected);
      $all("[data-sel-count]").forEach(function (el) { el.textContent = data.count; });
      var cb = $("[data-complete-btn]");
      if (cb) cb.disabled = !data.count;
    }).catch(function () {});
  });

  // Auswahl abschliessen
  var completeForm = $("[data-complete-form]");
  if (completeForm) {
    completeForm.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var albumId = completeForm.getAttribute("data-complete-form");
      api("/api/alben/" + albumId + "/auswahl-abschliessen", { note: completeForm.note.value })
        .then(function () { window.location.reload(); }).catch(function () {});
    });
  }

  // ---------- Bildbetrachter ----------
  if (body.classList.contains("page-viewer")) {
    var stage = $("[data-stage]");
    var img = $("[data-viewer-img]");
    var layer = $("[data-anno-layer]");
    var imageId = body.getAttribute("data-image");
    var isAdmin = body.hasAttribute("data-admin");
    var marking = false, pendingMark = null, zoomed = false;

    document.addEventListener("keydown", function (ev) {
      if (ev.target.matches("input, textarea, select")) return;
      if (ev.key === "z" || ev.key === "Z") { toggleZoom(); return; }
      if (ev.key === "Escape" && (zoomed || marking)) { if (marking) setMarking(false); else toggleZoom(); return; }
      var sel = { ArrowLeft: "[data-key-prev]", ArrowRight: "[data-key-next]", Escape: "[data-key-back]" }[ev.key];
      var link = sel && $(sel);
      if (link) window.location.href = link.href;
    });

    // Wischgesten
    var x0 = null;
    stage.addEventListener("touchstart", function (e) { if (!zoomed && !marking) x0 = e.touches[0].clientX; }, { passive: true });
    stage.addEventListener("touchend", function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0;
      x0 = null;
      if (Math.abs(dx) < 50) return;
      var link = $(dx > 0 ? "[data-key-prev]" : "[data-key-next]");
      if (link) window.location.href = link.href;
    });

    // Filmstreifen horizontal zentrieren
    var strip = $("[data-filmstrip]");
    var cur = strip && $(".current", strip);
    if (cur) strip.scrollLeft = cur.offsetLeft - strip.clientWidth / 2 + cur.clientWidth / 2;

    // Markierungs-Ebene deckungsgleich über das Bild legen
    function placeLayer() {
      layer.style.left = img.offsetLeft + "px";
      layer.style.top = img.offsetTop + "px";
      layer.style.width = img.offsetWidth + "px";
      layer.style.height = img.offsetHeight + "px";
    }
    img.addEventListener("load", placeLayer);
    window.addEventListener("resize", placeLayer);
    if (img.complete) placeLayer();

    // Zoom (100 % der Vorschau) mit Verschieben per Maus
    function toggleZoom() {
      zoomed = !zoomed;
      stage.classList.toggle("zoomed", zoomed);
      var zb = $("[data-zoom]");
      if (zb) zb.classList.toggle("active", zoomed);
      placeLayer();
      if (zoomed) {
        stage.scrollLeft = (stage.scrollWidth - stage.clientWidth) / 2;
        stage.scrollTop = (stage.scrollHeight - stage.clientHeight) / 2;
      }
    }
    var zbtn = $("[data-zoom]");
    if (zbtn) zbtn.addEventListener("click", toggleZoom);
    img.addEventListener("dblclick", toggleZoom);
    var pan = null;
    stage.addEventListener("mousedown", function (e) {
      if (!zoomed || marking || e.target.closest(".nav-arrow, .anno")) return;
      pan = { x: e.clientX, y: e.clientY, l: stage.scrollLeft, t: stage.scrollTop };
      stage.classList.add("panning");
      e.preventDefault();
    });
    window.addEventListener("mousemove", function (e) {
      if (!pan) return;
      stage.scrollLeft = pan.l - (e.clientX - pan.x);
      stage.scrollTop = pan.t - (e.clientY - pan.y);
    });
    window.addEventListener("mouseup", function () { pan = null; stage.classList.remove("panning"); });

    // Tabs in der Infospalte
    function showTab(name) {
      $all("[data-tab]").forEach(function (t) { t.classList.toggle("active", t.getAttribute("data-tab") === name); });
      $all("[data-panel]").forEach(function (p) { p.hidden = p.getAttribute("data-panel") !== name; });
    }
    $all("[data-tab]").forEach(function (t) {
      t.addEventListener("click", function () { showTab(t.getAttribute("data-tab")); });
    });

    // ---------- Kommentare & Markierungen ----------
    var list = $("[data-comment-list]");
    var form = $("[data-comment-form]");
    var markBtn = $("[data-mark-btn]");
    var markState = $("[data-mark-state]");
    var comments = [];

    function setMarking(on) {
      marking = on;
      layer.classList.toggle("marking", on);
      if (markBtn) markBtn.classList.toggle("active", on);
      if (on && markState) markState.textContent = "Klicke für einen Punkt oder ziehe einen Bereich auf.";
      if (!on && markState && !pendingMark) markState.textContent = "";
    }

    function renderMarkers() {
      $all(".anno", layer).forEach(function (el) { el.remove(); });
      var n = 0;
      comments.forEach(function (c) {
        if (c.parent_id || c.x == null) return;
        n++;
        var el = document.createElement("div");
        el.className = "anno kind-" + c.kind + (c.w ? " anno-rect" : " anno-pin") + (c.resolved ? " resolved" : "");
        el.style.left = (c.x * 100) + "%";
        el.style.top = (c.y * 100) + "%";
        if (c.w) { el.style.width = (c.w * 100) + "%"; el.style.height = (c.h * 100) + "%"; }
        el.innerHTML = "<span>" + n + "</span>";
        el.title = c.kind_label + ": " + c.body;
        el.setAttribute("data-anno-for", c.id);
        el.addEventListener("click", function (e) {
          e.stopPropagation();
          showTab("comments");
          var li = $('[data-comment-id="' + c.id + '"]');
          if (li) { li.scrollIntoView({ block: "nearest" }); li.classList.add("flash-hl"); setTimeout(function () { li.classList.remove("flash-hl"); }, 1200); }
        });
        c._n = n;
        layer.appendChild(el);
      });
      if (pendingMark) {
        var p = document.createElement("div");
        p.className = "anno pending" + (pendingMark.w ? " anno-rect" : " anno-pin");
        p.style.left = (pendingMark.x * 100) + "%";
        p.style.top = (pendingMark.y * 100) + "%";
        if (pendingMark.w) { p.style.width = (pendingMark.w * 100) + "%"; p.style.height = (pendingMark.h * 100) + "%"; }
        p.innerHTML = "<span>+</span>";
        layer.appendChild(p);
      }
    }

    function renderComments() {
      if (!list) return;
      var roots = comments.filter(function (c) { return !c.parent_id; });
      var counter = $("[data-comment-count]");
      var open = roots.filter(function (c) { return !c.resolved; }).length;
      if (counter) counter.textContent = roots.length ? (open + (open !== roots.length ? "/" + roots.length : "")) : "";
      renderMarkers();
      if (!roots.length) { list.innerHTML = '<li class="muted small empty-comments">Noch keine Kommentare zu diesem Bild.</li>'; return; }
      list.innerHTML = roots.map(function (c) {
        var replies = comments.filter(function (r) { return r.parent_id === c.id; });
        return '<li class="comment' + (c.resolved ? " resolved" : "") + '" data-comment-id="' + c.id + '">' +
          '<div class="comment-head">' +
            (c._n ? '<span class="anno-num kind-' + c.kind + '">' + c._n + "</span>" : "") +
            '<span class="kind-tag kind-' + c.kind + '">' + esc(c.kind_label) + "</span>" +
            "<b>" + esc(c.author) + "</b>" +
            '<span class="muted small">' + esc(c.created) + "</span>" +
          "</div>" +
          '<p class="comment-body">' + esc(c.body) + "</p>" +
          replies.map(function (r) {
            return '<div class="reply' + (r.from_admin ? " from-admin" : "") + '"><b>' + esc(r.author) + '</b> <span class="muted small">' + esc(r.created) + "</span>" +
              (r.mine || isAdmin ? ' <button class="linkbtn-dark small" data-del="' + r.id + '">löschen</button>' : "") +
              "<p>" + esc(r.body) + "</p></div>";
          }).join("") +
          '<div class="comment-actions">' +
            (isAdmin ? '<button class="linkbtn-dark small" data-resolve="' + c.id + '">' + (c.resolved ? "↺ wieder öffnen" : "✓ erledigt") + "</button>" : (c.resolved ? '<span class="pill pill-ok">✓ erledigt</span>' : "")) +
            '<button class="linkbtn-dark small" data-reply="' + c.id + '">antworten</button>' +
            (c.mine || isAdmin ? '<button class="linkbtn-dark small" data-del="' + c.id + '">löschen</button>' : "") +
          "</div></li>";
      }).join("");
    }

    function loadComments() {
      if (!body.hasAttribute("data-can-comment")) return;
      api("/api/bilder/" + imageId + "/kommentare").then(function (data) {
        comments = data.comments;
        renderComments();
      }).catch(function () {});
    }

    if (list) {
      loadComments();
      if (location.hash === "#kommentare") showTab("comments");

      list.addEventListener("click", function (ev) {
        var t = ev.target;
        if (t.hasAttribute("data-resolve")) {
          api("/api/kommentare/" + t.getAttribute("data-resolve") + "/erledigt", {}).then(loadComments).catch(function () {});
        } else if (t.hasAttribute("data-del")) {
          if (confirm("Kommentar löschen?")) api("/api/kommentare/" + t.getAttribute("data-del") + "/loeschen", {}).then(loadComments).catch(function () {});
        } else if (t.hasAttribute("data-reply")) {
          if (body.hasAttribute("data-needs-identity")) { openDialog("guest-dialog"); return; }
          var li = t.closest("li");
          if ($(".reply-form", li)) return;
          var f = document.createElement("form");
          f.className = "reply-form";
          f.innerHTML = '<textarea rows="2" maxlength="2000" required placeholder="Antwort…"></textarea><button class="btn btn-sm btn-primary">Antworten</button>';
          f.addEventListener("submit", function (e) {
            e.preventDefault();
            api("/api/bilder/" + imageId + "/kommentare", { body: $("textarea", f).value, parent_id: parseInt(t.getAttribute("data-reply"), 10) })
              .then(loadComments).catch(function () {});
          });
          li.appendChild(f);
          $("textarea", f).focus();
        }
      });
      list.addEventListener("mouseover", function (ev) {
        var li = ev.target.closest("[data-comment-id]");
        $all(".anno.hl", layer).forEach(function (a) { a.classList.remove("hl"); });
        if (li) { var a = $('[data-anno-for="' + li.getAttribute("data-comment-id") + '"]', layer); if (a) a.classList.add("hl"); }
      });
    }

    if (markBtn) markBtn.addEventListener("click", function () { setMarking(!marking); });

    // Punkt setzen oder Bereich aufziehen
    var drag = null;
    function rel(e) {
      var r = layer.getBoundingClientRect();
      return { x: Math.max(0, Math.min(1, (e.clientX - r.left) / r.width)), y: Math.max(0, Math.min(1, (e.clientY - r.top) / r.height)) };
    }
    layer.addEventListener("mousedown", function (e) {
      if (!marking) return;
      e.preventDefault();
      drag = rel(e);
    });
    window.addEventListener("mousemove", function (e) {
      if (!drag) return;
      var p = rel(e);
      pendingMark = { x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: Math.abs(p.x - drag.x), h: Math.abs(p.y - drag.y) };
      if (pendingMark.w < 0.01 && pendingMark.h < 0.01) pendingMark = { x: drag.x, y: drag.y };
      renderMarkers();
    });
    window.addEventListener("mouseup", function (e) {
      if (!drag) return;
      var p = rel(e);
      var w = Math.abs(p.x - drag.x), h = Math.abs(p.y - drag.y);
      pendingMark = (w < 0.01 && h < 0.01) ? { x: drag.x, y: drag.y } : { x: Math.min(drag.x, p.x), y: Math.min(drag.y, p.y), w: w, h: h };
      drag = null;
      setMarking(false);
      if (markState) markState.innerHTML = (pendingMark.w ? "Bereich markiert" : "Punkt gesetzt") + ' <button type="button" class="linkbtn-dark" data-clear-mark>✕</button>';
      renderMarkers();
      var ta = form && $("textarea", form);
      if (ta) ta.focus();
    });
    // Touch: Antippen setzt einen Punkt
    layer.addEventListener("touchend", function (e) {
      if (!marking) return;
      e.preventDefault();
      var t = e.changedTouches[0];
      pendingMark = rel(t);
      setMarking(false);
      if (markState) markState.innerHTML = 'Punkt gesetzt <button type="button" class="linkbtn-dark" data-clear-mark>✕</button>';
      renderMarkers();
    });
    document.addEventListener("click", function (ev) {
      if (ev.target.hasAttribute("data-clear-mark")) { pendingMark = null; markState.textContent = ""; renderMarkers(); }
    });

    if (form) {
      form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var payload = { body: form.body.value, kind: (form.querySelector("input[name=kind]:checked") || {}).value };
        if (pendingMark) { payload.x = pendingMark.x; payload.y = pendingMark.y; payload.w = pendingMark.w || null; payload.h = pendingMark.h || null; }
        api("/api/bilder/" + imageId + "/kommentare", payload).then(function () {
          form.body.value = "";
          pendingMark = null;
          if (markState) markState.textContent = "";
          loadComments();
        }).catch(function () {});
      });
    }
  }

  // ---------- Kopieren ----------
  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest("[data-copy]");
    if (!btn) return;
    navigator.clipboard.writeText(btn.getAttribute("data-copy")).then(function () {
      var t = btn.textContent;
      btn.textContent = "Kopiert ✓";
      setTimeout(function () { btn.textContent = t; }, 1500);
    });
  });

  // Bestätigung für gefährliche Aktionen
  document.addEventListener("submit", function (ev) {
    var msg = ev.target.getAttribute("data-confirm");
    if (msg && !confirm(msg)) ev.preventDefault();
  });

  // ---------- Upload mit Drag & Drop und Fortschritt ----------
  var drop = $("[data-upload]");
  if (drop) {
    var input = $("input[type=file]", drop);
    var ulist = $("[data-upload-list]");
    var url = drop.getAttribute("data-upload");
    var replaceBox = $("[data-upload-replace]");
    var queue = [], rawQueue = [], running = 0, done = 0, failed = 0, total = 0, replaced = 0;
    // RAW-Dateien werden dem gleichnamigen Bild angehängt – daher erst nach allen Bildern hochladen
    var RAW_RE = /\.(dng|cr2|cr3|crw|nef|nrw|arw|srf|sr2|raf|orf|rw2|pef|srw|x3f|3fr|fff|iiq|erf|mef|mos|rwl|raw|dcr|kdc)$/i;
    var bar = $("[data-upload-progress]");

    function updateBar() {
      if (!bar) return;
      bar.hidden = false;
      $("span", bar).style.width = (total ? ((done + failed) / total) * 100 : 0) + "%";
      $("em", bar).textContent = (done + failed) + " / " + total + " verarbeitet" +
        (replaced ? " · " + replaced + " neue Versionen" : "") + (failed ? " · " + failed + " Fehler" : "");
    }

    function next() {
      if (!queue.length && !running && rawQueue.length) { queue = rawQueue; rawQueue = []; }
      while (running < 3 && queue.length) upload(queue.shift());
      if (!running && !queue.length && !rawQueue.length && total && done && !failed) setTimeout(function () { window.location.reload(); }, 800);
    }

    function upload(file) {
      running++;
      var row = document.createElement("li");
      row.innerHTML = '<span class="up-name"></span><span class="up-bar"><i></i></span><span class="up-state">…</span>';
      $(".up-name", row).textContent = file.name;
      ulist.appendChild(row);
      var fd = new FormData();
      fd.append("file", file);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", url + "?ersetzen=" + (replaceBox && !replaceBox.checked ? 0 : 1));
      xhr.setRequestHeader("X-CSRF-Token", csrf);
      xhr.upload.onprogress = function (e) {
        if (e.lengthComputable) $("i", row).style.width = (e.loaded / e.total * 100) + "%";
        if (e.loaded === e.total) $(".up-state", row).textContent = "verarbeite…";
      };
      xhr.onload = function () {
        running--;
        var ok = xhr.status === 200, data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) {}
        row.classList.add(ok ? "ok" : "err");
        $(".up-state", row).textContent = ok ? (data.raw ? (data.replaced ? "✓ RAW ersetzt" : "✓ RAW angehängt") : data.replaced ? "✓ neue Version" : "✓") : (data.error || "Fehler");
        if (ok) { done++; if (data.replaced) replaced++; } else { failed++; }
        updateBar();
        next();
      };
      xhr.onerror = function () { running--; failed++; row.classList.add("err"); $(".up-state", row).textContent = "Netzwerkfehler"; updateBar(); next(); };
      xhr.send(fd);
    }

    function add(files) {
      for (var i = 0; i < files.length; i++) { (RAW_RE.test(files[i].name) ? rawQueue : queue).push(files[i]); total++; }
      updateBar();
      next();
    }

    input.addEventListener("change", function () { add(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.add("drag"); });
    });
    ["dragleave", "drop"].forEach(function (t) {
      drop.addEventListener(t, function (e) { e.preventDefault(); drop.classList.remove("drag"); });
    });
    drop.addEventListener("drop", function (e) { add(e.dataTransfer.files); });
  }

  // Alle Bilder auswählen (Admin)
  var selAll = $("[data-select-all]");
  if (selAll) {
    selAll.addEventListener("change", function () {
      $all("input[name=image_ids]").forEach(function (c) { c.checked = selAll.checked; });
    });
  }

  // ---------- Workflow-Board (Drag & Drop) ----------
  function setStatus(albumId, status) {
    return api("/api/admin/alben/" + albumId + "/status", { status: status });
  }
  var board = $("[data-board]");
  if (board) {
    var dragged = null;
    board.addEventListener("dragstart", function (e) {
      dragged = e.target.closest("[data-album-id]");
      if (!dragged) return;
      dragged.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", dragged.getAttribute("data-album-id"));
    });
    board.addEventListener("dragend", function () { if (dragged) dragged.classList.remove("dragging"); dragged = null; });
    $all("[data-dropzone]", board).forEach(function (zone) {
      zone.addEventListener("dragover", function (e) { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", function () { zone.classList.remove("over"); });
      zone.addEventListener("drop", function (e) {
        e.preventDefault();
        zone.classList.remove("over");
        if (!dragged) return;
        var from = dragged.parentNode;
        zone.appendChild(dragged);
        setStatus(dragged.getAttribute("data-album-id"), zone.getAttribute("data-dropzone")).then(function () {
          $all(".board-col", board).forEach(function (col) {
            $(".count", col).textContent = $all("[data-album-id]", col).length;
          });
        }).catch(function () { from.appendChild(dragged); });
      });
    });
  }
  var statusSelect = $("[data-status-select]");
  if (statusSelect) {
    statusSelect.addEventListener("change", function () {
      setStatus(statusSelect.getAttribute("data-status-select"), statusSelect.value).catch(function () {});
    });
  }

  // Schieberegler mit Anzeige (Einstellungen)
  $all("[data-range]").forEach(function (r) {
    var out = r.parentNode.querySelector("[data-range-out]");
    r.addEventListener("input", function () { if (out) out.textContent = r.value; });
  });

  // ---------- Benutzermenü: schliessen bei Klick daneben oder Esc ----------
  var userMenu = document.querySelector(".usermenu");
  if (userMenu) {
    document.addEventListener("click", function (ev) {
      if (userMenu.open && !userMenu.contains(ev.target)) userMenu.open = false;
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && userMenu.open) { userMenu.open = false; userMenu.querySelector("summary").focus(); }
    });
  }
})();
