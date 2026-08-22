"use strict";

// ----- tuning ---------------------------------------------------------------
const POLL_MS = 5000;
const TL_PAGE = 50;

// ----- global state ---------------------------------------------------------
const state = {
    offset: 0,
    hasMore: false,
    rowsShown: 0,
    loading: false,
    pollTimer: null,
    lastTags: [],
};

// ----- tiny helpers ---------------------------------------------------------
function $(sel) { return document.querySelector(sel); }

function esc(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function fmtTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return iso;
    return d.toISOString().replace("T", " ").replace("Z", "Z").slice(0, 19);
}

function humanBytes(n) { return String(n); }

async function api(path, params) {
    let url = path;
    if (params) {
        const q = new URLSearchParams(params).toString();
        url += (url.indexOf("?") >= 0 ? "&" : "?") + q;
    }
    const res = await fetch(url, { cache: "no-store" });
    return res.json();
}

// ----- backlog status (page footer) --------------------------------------
async function renderBacklog() {
    const [data, telemetry] = await Promise.all([
        api("/api/overview"),
        api("/api/telemetry"),
    ]);
    const environment = $("#data-environment");
    const l1 = $("#backlog-line1");
    const l2 = $("#backlog-line2");
    if (environment) {
        environment.textContent = "data: " + (data.environment || "unknown");
    }
    if (!l1 || !l2) return;

    l1.textContent = data.processed + "/" + data.total;

    let msg;
    if (data.remaining === 0) {
        msg = "all caught up";
     } else if (!data.has_speed) {
        msg = "no speed data yet";
     } else {
        msg = "≈ " + data.eta_human + " left";
     }
    l2.textContent = msg;
    renderProcessingChart(telemetry);
}

function renderProcessingChart(rows) {
    const host = $("#backlog-chart");
    const meta = $("#backlog-chart-meta");
    if (!host || !meta) return;

    const recent = (Array.isArray(rows) ? rows : [])
        .filter((row) => Number.isFinite(Number(row.vision_latency_s)))
        .slice(-20);
    host.innerHTML = "";
    meta.textContent = recent.length ? recent.length + " processed" : "no data yet";

    if (!recent.length) {
        host.innerHTML = '<span class="muted backlog-chart-empty">no processing times yet</span>';
        return;
    }

    const max = Math.max(...recent.map((row) => Number(row.vision_latency_s)), 1);
    for (const row of recent) {
        const seconds = Number(row.vision_latency_s);
        const bar = document.createElement("div");
        bar.className = "backlog-bar";
        bar.style.height = Math.max((seconds / max) * 100, 3) + "%";
        bar.title = (row.filename || "processed picture") + "\n" +
            "Processed: " + (row.timestamp ? fmtTime(row.timestamp) : "—") + "\n" +
            "Duration: " + seconds.toFixed(2) + "s";
        bar.setAttribute("aria-label", bar.title);
        host.appendChild(bar);
    }
}

// ----- search (main surface) ------------------------------------------------------
function currentFilters() {
    return {
        q: $("#filter-q").value.trim(),
        status: $("#filter-status").value,
        tag: $("#filter-tag").value,
    };
}

async function loadTimeline(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) state.offset = 0;

    const f = currentFilters();
    const params = {
        limit: TL_PAGE,
        offset: state.offset,
    };
    if (f.q) params.q = f.q;
    if (f.status && f.status !== "all") params.status = f.status;
    if (f.tag) params.tag = f.tag;

    const data = await api("/api/timeline", params);

    const host = $("#timeline");
    if (reset) host.innerHTML = "";
    for (const row of data.rows) host.appendChild(renderRow(row));

    state.rowsShown = data.shown;
    state.offset += data.rows.length;
    state.hasMore = data.has_more;

    $("#load-more-wrap").style.display = data.has_more ? "block" : "none";
    const parts = [];
    parts.push("showing " + data.shown + " of " + data.shown_total + " matched");
    if (data.total_rows !== data.shown_total) {
        parts.push("(" + data.total_rows + " total)");
    }
    $("#timeline-status").textContent = parts.join(" ") + ".";
    state.loading = false;
}

function renderRow(row) {
    const el = document.createElement("div");
    el.className = "tl-row";

    const thumbUrl    = "/thumb/" + encodeURIComponent(row.filename);
    const originalUrl = thumbUrl + "?original=1";
    const openAttr = ' class="tl-thumb-link" data-original="' + originalUrl + '"';
    const thumb = row.has_thumb
           ? '<a' + openAttr + '><img class="tl-thumb" src="' + thumbUrl + '" alt="" loading="lazy"></a>'
           : '<div class="tl-thumb">no thumbnail</div>';

    const statusDot =
        ' <span class="tl-status-dot ' + row.status +
        '" title="status: ' + row.status + '"></span>';

    const quality = (row.quality != null ? "Q" + row.quality : "Q—");
    const lat = (row.telem_latency_s != null ?
        row.telem_latency_s + "s" : "—");
    const inWiki = row.in_wiki
        ? '<span class="tag-chip">in-wiki</span>' : "";

    el.innerHTML =
        thumb +
        '<div class="tl-main">' +
          '<div class="tl-head">' +
            statusDot +
            '<span class="tl-filename">' + esc(row.filename) + "</span>" +
            '<span class="tl-mtime">' + fmtTime(row.mtime_iso) + "Z</span>" +
            '<span class="tl-quality">' + quality + "</span>" +
          "</div>" +
          '<div class="tl-caption">' + esc(row.caption || "—") + "</div>" +
          '<div class="tl-tags">' +
            row.tags.map((t) =>
                '<span class="tag-chip" data-tag="' + esc(t) + '">' +
                esc(t) + "</span>").join("") +
            inWiki +
          "</div>" +
          '<div class="tl-ocr">' +
            esc(row.ocr_text.join("\n")) +
            (row.ocr_truncated ? " ⋯" : "") +
            "</div>" +
            '<div class="tl-meta">' +
              "took " + lat +
              (row.entities.length ? " · " + row.entities.length + " entities" : "") +
            "</div>" +
        "</div>";

    el.querySelector(".tl-main").addEventListener("click",
        (e) => openRecord(row.filename));
    el.querySelectorAll(".tag-chip[data-tag]").forEach((c) =>
        c.addEventListener("click", (e) => {
            e.stopPropagation();
            $("#filter-tag").value = c.getAttribute("data-tag");
            loadTimeline(true);
        }));
    return el;
}

// ----- record side-panel ----------------------------------------------------
async function openRecord(filename) {
    const rec = await api("/api/record", { filename: filename });
    const body = $("#record-body");
    if (!rec || rec.error) {
        body.innerHTML = "<p class='muted'>not found</p>";
        openPanel(true);
        return;
    }

    const pathBlock =
        '<h3>original</h3>' +
        fileLink(rec.original_path, rec.filename);

    const ocrBlock =
        '<h3>OCR text (' + rec.ocr_text.length + " lines)</h3>" +
        "<pre>" + esc((rec.ocr_text || []).join("\n") || "—") + "</pre>";

    const tagsBlock =
        '<h3>tags</h3>' +
        (rec.tags && rec.tags.length
            ? '<div class="tl-tags">' +
                rec.tags.map((t) =>
                    '<span class="tag-chip">' + esc(t) + "</span>").join("") +
              "</div>"
            : '<p class="muted">—</p>');

    const entitiesBlock =
        '<h3>entities</h3>' +
        (rec.entities && rec.entities.length
            ? "<pre>" + esc(rec.entities.join("\n")) + "</pre>"
            : '<p class="muted">—</p>');

    body.innerHTML =
        '<h3>record</h3>' +
        '<p class="fname">' + esc(rec.filename) + "</p>" +
        '<p class="muted">mtime ' + fmtTime(rec.mtime_iso) + "Z · " +
          "quality " + (rec.quality_score != null ? rec.quality_score : "—") +
          " · " + (rec.caption ? esc(rec.caption) : "no caption") + "</p>" +
        pathBlock + tagsBlock + entitiesBlock + ocrBlock;

    openPanel(true);
}

function fileLink(path, fallback) {
    if (!path) {
        return '<p class="muted">no original path</p>';
    }
    const uri = "file://" + encodeURIComponent(path);
    return (
        '<a class="record-path" href="' + uri + '" target="_blank" ' +
        'rel="noopener" title="open original (may be blocked for iCloud ' +
        'file:// paths)">' + esc(path) + "</a>" +
        '<p class="muted" style="margin-top:4px;font-size:10px;">' +
        "open-original may be blocked by the browser; select + copy the path." +
        "</p>"
    );
}

function openPanel(open) {
    $("#record-panel").classList.toggle("open", open);
}

// ----- section 3: tags ------------------------------------------------------
async function renderTags() {
    const data = await api("/api/tags");
    const tags = data.top_tags || [];

    renderTagCloud(tags);

    $("#tags-meta").textContent =
        "total screenshots: " + humanBytes(data.total_screenshots || 0) +
        " · unique tags: " + humanBytes(data.unique_tags || 0) +
        " · edges: " + humanBytes((data.edges || []).length);

    populateTagFilter(tags);

    const host = $("#top-tags");
    host.innerHTML = "";
    const max = tags.length ? tags[0].count : 1;
    for (const t of tags) {
        const row = document.createElement("div");
        row.className = "top-tag-row";
        row.innerHTML =
            '<div class="top-tag-name" title="' + esc(t.tag) + '">' +
              esc(t.tag) + "</div>" +
            '<div class="top-tag-track">' +
              '<div class="top-tag-fill" style="width:' +
                Math.max((t.count / max) * 100, 0.5) + '%"></div>' +
            "</div>" +
            '<div class="top-tag-count">' + humanBytes(t.count) + "</div>";
        row.querySelector(".top-tag-name").addEventListener("click", () => {
            $("#filter-tag").value = t.tag;
            loadTimeline(true);
            document.querySelector("#timeline-section").scrollIntoView({ block: "start" });
        });
        host.appendChild(row);
    }

    const edges = (data.edges || []).slice(0, 25);
    const ehost = $("#edges");
    ehost.innerHTML = '<div class="tags-meta">co-occurrence (top ' +
        edges.length + ")</div>";
    for (const e of edges) {
        const row = document.createElement("div");
        row.className = "edge-row";
        row.innerHTML =
            '<span class="edge-weight">' + humanBytes(e.weight) + "</span>" +
            "<span>" + esc(e.source) + "</span>" +
            '<span class="edge-arrow">↔</span>' +
            "<span>" + esc(e.target) + "</span>";
        ehost.appendChild(row);
    }
}

function renderTagCloud(tags) {
    const host = $("#tag-cloud");
    const meta = $("#tag-cloud-meta");
    if (!host || !meta) return;

    host.innerHTML = "";
    if (!tags.length) {
        meta.textContent = "no tags yet";
        host.innerHTML = '<span class="muted">no captured tags</span>';
        return;
    }

    const counts = tags.map((t) => Number(t.count) || 0);
    const max = Math.max(...counts, 1);
    const min = Math.min(...counts);
    meta.textContent = tags.length + " most frequent";

    for (const tag of tags) {
        const count = Number(tag.count) || 0;
        const ratio = max === min ? 1 : (count - min) / (max - min);
        const size = 0.78 + ratio * 1.18;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "cloud-tag";
        button.style.fontSize = size.toFixed(2) + "rem";
        button.title = "filter by " + tag.tag + " (" + count + ")";
        button.innerHTML = esc(tag.tag) +
            '<span class="cloud-tag-count">' + count + "</span>";
        button.addEventListener("click", () => {
            $("#filter-tag").value = tag.tag;
            loadTimeline(true);
            document.querySelector("#timeline-section").scrollIntoView({ block: "start" });
        });
        host.appendChild(button);
    }
}

function populateTagFilter(tags) {
    state.lastTags = tags;
    const sel = $("#filter-tag");
    const current = sel.value;
    sel.innerHTML = '<option value="">tag: all</option>' +
        tags.map((t) =>
            '<option value="' + esc(t.tag) + '">' +
            esc(t.tag) + " (" + t.count + ")</option>").join("");
    if (current) sel.value = current;
}

// ----- polling + wiring -----------------------------------------------------
function setPoll(on) {
    const dot = $("#live-dot");
    dot.classList.toggle("off", !on);
    dot.title = on ? "auto-refresh on" : "auto-refresh off";
    if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
    }
    if (on) state.pollTimer = setInterval(refreshAll, POLL_MS);
}

async function refreshAll() {
    await Promise.all([
        renderBacklog().catch((e) => {}),
        renderTags().catch((e) => {}),
        loadTimeline(state.offset === 0).catch((e) => {}),
    ]);
    $("#last-updated").textContent = "updated " +
        new Date().toLocaleTimeString();
}

function wireControls() {
    $("#poll-enabled").addEventListener("change", (e) =>
        setPoll(e.target.checked));

    let tl;
    $("#filter-q").addEventListener("input", () => {
        clearTimeout(tl);
        tl = setTimeout(() => loadTimeline(true), 250);
    });
    $("#filter-status").addEventListener("change", () => loadTimeline(true));
    $("#filter-tag").addEventListener("change", () => loadTimeline(true));
    $("#clear-filters").addEventListener("click", () => {
        $("#filter-q").value = "";
        $("#filter-status").value = "all";
        $("#filter-tag").value = "";
        loadTimeline(true);
    });

    $("#load-more").addEventListener("click", () => loadTimeline(false));
     $("#record-close").addEventListener("click", () => openPanel(false));
      $("#original-close").addEventListener("click", () => closeOriginal());


      // Thumbnail -> full-res original lightbox.
      $("#timeline").addEventListener("click", (e) => {
           const link = e.target.closest(".tl-thumb-link");
           if (link && link.dataset.original) {
               e.stopPropagation();
               openOriginal(link.dataset.original);
           }
      });

     document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            openPanel(false);
            closeOriginal();
        }
     });
 }

 // ----- full-res original lightbox ----------------------------------------
 async function openOriginal(originalUrl) {
     const box = $("#original-box");
     const img = $("#original-img");
     box.style.display = "flex";
     img.style.display = "none";
     $("#original-msg").textContent = "loading original…";
     $("#original-msg").style.display = "block";
     img.onload = () => {
         img.style.display = "block";
         $("#original-msg").style.display = "none";
      };
     img.src = originalUrl + "&t=" + Date.now();
 }

 function closeOriginal() {
     const box = $("#original-box");
     if (box) box.style.display = "none";
 }

// ----- boot -----------------------------------------------------------------
async function main() {
    wireControls();
    setPoll(true);
    await refreshAll();
}

document.addEventListener("DOMContentLoaded", main);
