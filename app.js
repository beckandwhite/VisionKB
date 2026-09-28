"use strict";

// ----- tuning ---------------------------------------------------------------
const POLL_MS = 5000;
const TL_PAGE = 50;

// ----- global state ---------------------------------------------------------
const state = {
    offset: 0,
    windowLimit: 50,
    hasMore: false,
    rowsShown: 0,
    loading: false,
    pollTimer: null,
    lastTags: [],
    mtimeMin: null,
    mtimeMax: null,
    mtimeFrom: null,
    mtimeTo: null,
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

function fmtEpoch(epoch) {
    if (!Number.isFinite(Number(epoch))) return "—";
    const d = new Date(Number(epoch) * 1000);
    return isNaN(d.getTime()) ? "—" : d.toISOString().slice(0, 10);
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
    const works = await api("/api/works");
    if (!Array.isArray(works) || !works.length) return;

    const environment = $("#data-environment");
    const requests = works.flatMap(w => [
        api("/api/overview", { work: w }),
        api("/api/telemetry", { work: w }),
    ]);
    const results = await Promise.all(requests);

    works.forEach((w, i) => {
        const data = results[i * 2];
        const telemetry = results[i * 2 + 1];
        if (i === 0 && environment) {
            environment.textContent = "data: " + (data.environment || "unknown");
        }
        renderWorkerBacklog(w, data, telemetry);
    });
}

function createBacklogPanel(workName) {
    const label = workName.replace(/^work(\d+)$/, "Worker $1");
    const panel = document.createElement("section");
    panel.id = workName + "-backlog";
    panel.className = "section backlog";
    panel.innerHTML =
        '<h1 class="section-title">' + esc(label) + " — Backlog</h1>" +
        '<div class="backlog-summary">' +
          "<div>" +
            '<div id="' + workName + '-backlog-line1" class="backlog-line primary">—</div>' +
            '<div id="' + workName + '-backlog-line2" class="backlog-line muted">—</div>' +
            '<div id="' + workName + '-backlog-extras" class="muted" style="font-size:11px"></div>' +
          "</div>" +
          '<div class="backlog-chart-wrap">' +
            '<div class="backlog-chart-heading">' +
              "<span>per-file metric</span>" +
              '<span id="' + workName + '-backlog-chart-meta" class="muted">last 20</span>' +
            "</div>" +
            '<div class="backlog-chart-area">' +
              '<div id="' + workName + '-backlog-chart-axis" class="backlog-chart-axis" aria-hidden="true"></div>' +
              '<div id="' + workName + '-backlog-chart" class="backlog-chart" ' +
                'aria-label="' + esc(label) + ' per-file metric for last 20 processed pictures"></div>' +
            "</div>" +
          "</div>" +
        "</div>";
    const container = document.getElementById("backlog-panels");
    if (container) container.appendChild(panel);
    return panel;
}

function renderWorkerBacklog(workName, data, telemetry) {
    if (!document.getElementById(workName + "-backlog")) {
        createBacklogPanel(workName);
    }
    const l1 = document.getElementById(workName + "-backlog-line1");
    const l2 = document.getElementById(workName + "-backlog-line2");
    const extras = document.getElementById(workName + "-backlog-extras");
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

    if (extras) {
        const parts = [];
        if (data.empty_result_rate != null)
            parts.push("empty: " + (data.empty_result_rate * 100).toFixed(1) + "%");
        if (data.error_rate != null)
            parts.push("errors: " + (data.error_rate * 100).toFixed(1) + "%");
        extras.textContent = parts.join(" · ");
    }

    renderProcessingChart(telemetry, workName);
}

function renderProcessingChart(rows, worker) {
    const host = document.getElementById(worker + "-backlog-chart");
    const axis = document.getElementById(worker + "-backlog-chart-axis");
    const meta = document.getElementById(worker + "-backlog-chart-meta");
    if (!host || !axis || !meta) return;

    const getValue = (row) => Number(row.chart_value != null ? row.chart_value : row.vision_latency_s);
    const recent = (Array.isArray(rows) ? rows : [])
        .filter((row) => Number.isFinite(getValue(row)))
        .slice(-20);
    host.innerHTML = "";
    axis.innerHTML = "";
    meta.textContent = recent.length ? recent.length + " processed" : "no data yet";

    if (!recent.length) {
        host.innerHTML = '<span class="muted backlog-chart-empty">no processing times yet</span>';
        return;
    }

    const unit = (recent[0] && recent[0].chart_unit) || "s";
    const fmtTick = (v) => unit === "s" ? v.toFixed(1) + "s" : Math.round(v) + " " + unit;
    const max = Math.max(...recent.map(getValue), 1);
    for (const tick of [max, max / 2, 0]) {
        const label = document.createElement("span");
        label.textContent = fmtTick(tick);
        axis.appendChild(label);
    }
    for (const row of recent) {
        const value = getValue(row);
        const bar = document.createElement("div");
        bar.className = "backlog-bar";
        bar.style.height = Math.max((value / max) * 100, 3) + "%";
        bar.title = (row.filename || "processed picture") + "\n" +
            "Processed: " + (row.timestamp ? fmtTime(row.timestamp) : "—") + "\n" +
            (unit === "s" ? "Duration: " + value.toFixed(2) + "s" : "Lines: " + Math.round(value));
        bar.setAttribute("aria-label", bar.title);
        host.appendChild(bar);
    }
}

// ----- search (main surface) ------------------------------------------------------
function currentFilters() {
    return {
        q: $("#filter-q").value.trim(),
        mtimeFrom: state.mtimeFrom,
        mtimeTo: state.mtimeTo,
    };
}

function renderMtimeFilter(data) {
    const minimum = Number(data.mtime_min_epoch);
    const maximum = Number(data.mtime_max_epoch);
    const heatmap = $("#mtime-heatmap");
    const from = $("#mtime-from");
    const to = $("#mtime-to");
    if (!heatmap || !from || !to || !Number.isFinite(minimum) ||
        !Number.isFinite(maximum)) {
        $("#mtime-range-label").textContent = "no modification dates";
        return;
    }

    if (state.mtimeMin == null || state.mtimeMax == null) {
        state.mtimeMin = minimum;
        state.mtimeMax = maximum;
        state.mtimeFrom = minimum;
        state.mtimeTo = maximum;
    } else {
        state.mtimeMin = minimum;
        state.mtimeMax = maximum;
        state.mtimeFrom = Math.max(minimum, Math.min(state.mtimeFrom, maximum));
        state.mtimeTo = Math.max(state.mtimeFrom, Math.min(state.mtimeTo, maximum));
    }
    from.min = String(minimum);
    from.max = String(maximum);
    from.step = "1";
    to.min = String(minimum);
    to.max = String(maximum);
    to.step = "1";
    from.value = String(state.mtimeFrom);
    to.value = String(state.mtimeTo);
    const span = maximum - minimum || 1;
    const fromPct = ((state.mtimeFrom - minimum) / span) * 100;
    const toPct = ((state.mtimeTo - minimum) / span) * 100;
    $(".mtime-track").style.background =
        "linear-gradient(to right, var(--border) 0%, var(--border) " +
        fromPct + "%, var(--accent) " + fromPct + "%, var(--accent) " +
        toPct + "%, var(--border) " + toPct + "%, var(--border) 100%)";
    from.disabled = minimum === maximum;
    to.disabled = minimum === maximum;

    const buckets = Array.isArray(data.mtime_buckets) ? data.mtime_buckets : [];
    if (buckets.length) {
        const peak = Math.max(...buckets.map((bucket) => Number(bucket.count)), 1);
        heatmap.innerHTML = buckets.map((bucket) => {
            const count = Number(bucket.count) || 0;
            const intensity = Math.max(count / peak, 0.06);
            return '<span class="mtime-cell" style="--heat:' + intensity.toFixed(3) +
                '" title="' + count + ' file' + (count === 1 ? '' : 's') + '"></span>';
        }).join("");
    }
    $("#mtime-from-label").textContent = fmtEpoch(state.mtimeFrom);
    $("#mtime-to-label").textContent = fmtEpoch(state.mtimeTo);
    $("#mtime-range-label").textContent = fmtEpoch(state.mtimeFrom) +
        " → " + fmtEpoch(state.mtimeTo);
}

async function loadTimeline(reset) {
    if (state.loading) return;
    state.loading = true;
    if (reset) state.offset = 0;

    const f = currentFilters();
    const params = {
        limit: TL_PAGE,
        offset: state.offset,
        window: state.windowLimit,
    };
    if (f.q) params.q = f.q;
    if (f.mtimeFrom != null && f.mtimeTo != null) {
        params.mtime_from = f.mtimeFrom;
        params.mtime_to = f.mtimeTo;
    }

    const data = await api("/api/timeline", params);
    renderMtimeFilter(data);

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
    el.dataset.sourceKey = row.source_key || "";

    const thumbUrl = "/thumb/" + encodeURIComponent(row.filename);
    const originalUrl = thumbUrl + "?original=1&source_key=" +
        encodeURIComponent(row.source_key || "");
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
                    '<div class="tl-caption">' + esc(row.answer || "—") + "</div>" +
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
        (e) => openRecord(row.filename, row.source_key));
    return el;
}

// ----- detail split view ----------------------------------------------------
async function openRecord(filename, sourceKey) {
    const split = document.getElementById("detail-split");
    const imgEl = document.getElementById("detail-img");
    const msgEl = document.getElementById("detail-img-msg");
    const content = document.getElementById("detail-content");

    const originalUrl = "/thumb/" + encodeURIComponent(filename) +
        "?original=1&source_key=" + encodeURIComponent(sourceKey || "");
    if (imgEl) {
        imgEl.style.display = "none";
        if (msgEl) { msgEl.textContent = "loading…"; msgEl.style.display = "block"; }
        imgEl.onload = () => {
            imgEl.style.display = "block";
            if (msgEl) msgEl.style.display = "none";
        };
        imgEl.onerror = () => {
            if (msgEl) { msgEl.textContent = "image unavailable"; }
        };
        imgEl.src = originalUrl + "&t=" + Date.now();
    }

    if (content) content.innerHTML = "<p class='muted'>loading…</p>";
    if (split) split.style.display = "grid";

    const rec = await api("/api/record", { filename: filename, source_key: sourceKey || "" });
    if (!content) return;
    if (!rec || rec.error) {
        content.innerHTML = "<p class='muted'>not found</p>";
        return;
    }

    const tagsBlock =
        "<h3>Tags</h3>" +
        (rec.tags && rec.tags.length
            ? '<div class="tl-tags">' +
                rec.tags.map((t) => '<span class="tag-chip">' + esc(t) + "</span>").join("") +
              "</div>"
            : '<p class="muted">—</p>');
    const summaryBlock =
        "<h3>Summary</h3>" +
        '<p class="detail-summary">' + esc(rec.answer || "—") + "</p>";
    const ocrBlock =
        "<h3>Full OCR (" + (rec.ocr_text || []).length + " lines)</h3>" +
        "<pre>" + esc((rec.ocr_text || []).join("\n") || "—") + "</pre>";
    content.innerHTML = tagsBlock + summaryBlock + ocrBlock;
}

function closeDetail() {
    const split = document.getElementById("detail-split");
    if (split) split.style.display = "none";
}

function initSplitDrag() {
    const split = document.getElementById("detail-split");
    const divider = document.getElementById("split-divider");
    if (!split || !divider) return;

    const STORAGE_KEY = "detail-split-ratio";
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved) split.style.setProperty("--split", saved);

    divider.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        divider.classList.add("dragging");
        divider.setPointerCapture(e.pointerId);
        function onMove(ev) {
            const rect = split.getBoundingClientRect();
            let ratio = (ev.clientX - rect.left) / rect.width;
            ratio = Math.max(0.25, Math.min(0.75, ratio));
            const pct = (ratio * 100).toFixed(1) + "%";
            split.style.setProperty("--split", pct);
            localStorage.setItem(STORAGE_KEY, pct);
        }
        function onUp() {
            divider.classList.remove("dragging");
            divider.removeEventListener("pointermove", onMove);
            divider.removeEventListener("pointerup", onUp);
        }
        divider.addEventListener("pointermove", onMove);
        divider.addEventListener("pointerup", onUp);
    });
}

// ----- tab navigation -------------------------------------------------------
function activateTab(name) {
    document.querySelectorAll(".tab").forEach(btn => {
        const on = btn.dataset.tab === name;
        btn.classList.toggle("active", on);
        btn.setAttribute("aria-selected", String(on));
    });
    document.querySelectorAll(".tab-view").forEach(v => {
        v.classList.add("hidden");
        v.classList.remove("active");
    });
    const target = document.querySelector("#tab-" + name);
    if (target) {
        target.classList.remove("hidden");
        target.classList.add("active");
    }
    location.hash = name;
    if (name === "tagforge" && typeof initTagforge === "function") initTagforge();
    if (name === "telemetry" && typeof initTelemetry === "function") initTelemetry();
    if (name === "setup" && typeof initSetup === "function") initSetup();
    if (name === "feedback" && typeof initFeedback === "function") initFeedback();
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
            $("#filter-q").value = t.tag;
            activateTab("search");
            loadTimeline(true);
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
            $("#filter-q").value = tag.tag;
            activateTab("search");
            loadTimeline(true);
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
    if (document.querySelector('#tab-search').classList.contains('active')) {
        await Promise.all([
            renderBacklog().catch((e) => {}),
            loadTimeline(state.offset === 0).catch((e) => {}),
        ]);
    }
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
      $("#result-limit").addEventListener("change", (e) => {
        state.windowLimit = Number(e.target.value) || 50;
        loadTimeline(true);
    });
    let mtimeTimer;
    function updateMtime(which, value) {
        const numeric = Number(value);
        if (which === "from") {
            state.mtimeFrom = Math.min(numeric, state.mtimeTo);
        } else {
            state.mtimeTo = Math.max(numeric, state.mtimeFrom);
        }
        renderMtimeFilter({
            mtime_min_epoch: state.mtimeMin,
            mtime_max_epoch: state.mtimeMax,
            mtime_buckets: [],
        });
        clearTimeout(mtimeTimer);
        mtimeTimer = setTimeout(() => loadTimeline(true), 180);
    }
    $("#mtime-from").addEventListener("input", (e) =>
        updateMtime("from", e.target.value));
    $("#mtime-to").addEventListener("input", (e) =>
        updateMtime("to", e.target.value));
      $("#clear-filters").addEventListener("click", () => {
          $("#filter-q").value = "";
         state.mtimeFrom = state.mtimeMin;
        state.mtimeTo = state.mtimeMax;
        loadTimeline(true);
    });

    $("#load-more").addEventListener("click", () => loadTimeline(false));
    $("#detail-close").addEventListener("click", closeDetail);

     document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
            closeDetail();
        }
     });

    document.querySelectorAll(".tab").forEach(btn => {
        btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });
    const tablist = document.querySelector(".tabs");
    if (tablist) {
        tablist.addEventListener("keydown", (e) => {
            const tabs = Array.from(document.querySelectorAll(".tab"));
            const idx = tabs.indexOf(document.activeElement);
            if (e.key === "ArrowRight" && idx >= 0 && idx < tabs.length - 1) {
                e.preventDefault();
                tabs[idx + 1].focus();
                activateTab(tabs[idx + 1].dataset.tab);
            } else if (e.key === "ArrowLeft" && idx > 0) {
                e.preventDefault();
                tabs[idx - 1].focus();
                activateTab(tabs[idx - 1].dataset.tab);
            }
        });
    }
 }

// ----- boot -----------------------------------------------------------------
async function main() {
    wireControls();
    initSplitDrag();
    activateTab(location.hash.slice(1) || "search");
    window.onhashchange = () => activateTab(location.hash.slice(1) || "search");
    setPoll(true);
    await refreshAll();
}

document.addEventListener("DOMContentLoaded", main);
