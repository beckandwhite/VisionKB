"use strict";

// ----- pipeline-stage config (extend here to add a stage) -------------------
// Each stage shown in the funnel. `color` falls back to STAGE_COLORS on server
// if not set here.
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

// ----- section 1: backlog dashboard -----------------------------------------
async function renderDashboard() {
    const data = await api("/api/overview");
    renderFunnel(data.stages, data.total);
    renderEta(data);
    renderStatusChips(data.status_counts);
    renderSparkline(data.sparkline);
}

function renderFunnel(stages, total) {
    const host = $("#funnel");
    host.innerHTML = "";
    for (const st of stages) {
        const row = document.createElement("div");
        row.className = "funnel-row";
        row.innerHTML =
            '<div class="funnel-label">' + esc(st.name) + "</div>" +
            '<div class="funnel-bar-track">' +
              '<div class="funnel-bar-fill" style="width:' +
                Math.max(st.pct, 0.3) + "%;background:" + esc(st.color) + '"></div>' +
            "</div>" +
            '<div class="funnel-count">' +
              humanBytes(st.count) + " / " + humanBytes(total) +
              ' <span class="pct">(' + st.pct + "%)</span>" +
            "</div>";
        host.appendChild(row);
    }
}

function renderEta(data) {
    $("#eta-human").textContent = data.eta_human || "—";
    const detail = [];
    if (data.remaining > 0) {
        detail.push(data.remaining + " remaining");
        detail.push("avg " + data.avg_latency_s + "s/img");
    } else {
        detail.push("all caught up");
    }
    if (data.projected_finish_iso) {
        detail.push("→ " + fmtTime(data.projected_finish_iso) + "Z");
    }
    $("#eta-detail").textContent = detail.join("  ·  ");
}

function renderStatusChips(counts) {
    const host = $("#status-chips");
    host.innerHTML = "";
    const defs = [
        ["ok", "ok"],
        ["fail", "fail"],
        ["pending", "pending"],
    ];
    for (const [key, label] of defs) {
        const chip = document.createElement("div");
        chip.className = "chip " + key;
        chip.innerHTML =
            '<span class="dot"></span>' +
            '<span class="num">' + humanBytes(counts[key] || 0) + "</span>" +
            '<span class="lbl">' + esc(label) + "</span>";
        host.appendChild(chip);
    }
}

function renderSparkline(points) {
    const svg = $("#sparkline");
    if (!points.length) {
        svg.innerHTML = "";
        return;
    }
    const W = 600, H = 100, pad = 6;
    const lat = points.map((p) => p.latency_s);
    const maxV = Math.max.apply(null, lat) || 1;
    const step = points.length > 1 ? (W - pad * 2) / (points.length - 1) : 0;
    const y = (v) => H - pad - (v / maxV) * (H - pad * 2);
    const x = (i) => pad + step * i;

    let d = "";
    points.forEach((p, i) => {
        d += (i === 0 ? "M" : "L") + x(i).toFixed(1) + " " +
             y(p.latency_s).toFixed(1) + " ";
    });
    const dots = points.map((p, i) => {
        const cls = p.status === "ok" ? "dot-ok" : p.status === "fail" ? "dot-fail" : "dot-ok";
        return '<circle class="' + cls + '" cx="' + x(i).toFixed(1) + '" cy="' +
              y(p.latency_s).toFixed(1) + '" r="3">' +
              '<title>' + esc(p.filename) + " · " + p.latency_s + "s · " +
              esc(p.status) + "</title></circle>";
    }).join("");
    svg.innerHTML = '<path class="line" d="' + d + '"></path>' + dots;
}

// ----- section 2: timeline --------------------------------------------------
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

    const thumbUrl = "/thumb/" + encodeURIComponent(row.filename);
    const thumb = row.has_thumb
        ? '<img class="tl-thumb" src="' + thumbUrl + '" alt="" loading="lazy">'
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
            "latency " + lat +
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
        renderDashboard().catch((e) => {}),
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
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") openPanel(false);
    });
}

// ----- boot -----------------------------------------------------------------
async function main() {
    wireControls();
    setPoll(true);
    await refreshAll();
}

document.addEventListener("DOMContentLoaded", main);
