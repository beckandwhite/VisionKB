"use strict";

const SETUP_FIELDS = [
    ["ollama_base",       "Ollama base URL"],
    ["vision_model",      "Vision model"],
    ["embed_model",       "Embed model"],
    ["source_dir",        "Source directory"],
    ["max_dim",           "Max image dimension"],
    ["save_every",        "Save every N files"],
    ["supported_images",  "Supported image types"],
];

function initSetup() {
    const host = document.getElementById("setup-config");
    if (!host) return;
    host.innerHTML = "<p class='muted' style='padding:18px'>loading…</p>";
    fetch("/api/config", { cache: "no-store" })
        .then(function(r) { return r.json(); })
        .then(function(data) { _renderSetup(host, data); })
        .catch(function() {
            host.innerHTML = "<p class='muted' style='padding:18px'>failed to load config</p>";
        });
}

function _renderSetup(host, data) {
    var env = data.environment || "—";
    var cfg = data.config || {};

    var rows = '<tr><th>Environment</th><td>' + _escHtml(env) + '</td></tr>';
    for (var i = 0; i < SETUP_FIELDS.length; i++) {
        var key   = SETUP_FIELDS[i][0];
        var label = SETUP_FIELDS[i][1];
        var val   = cfg[key];
        var display;
        if (val === undefined || val === null) {
            display = '<span class="muted">—</span>';
        } else if (val === "***") {
            display = '<span class="muted">***</span>';
        } else if (Array.isArray(val)) {
            display = _escHtml(val.join(", "));
        } else {
            display = _escHtml(String(val));
        }
        rows += '<tr><th>' + _escHtml(label) + '</th><td>' + display + '</td></tr>';
    }

    host.innerHTML =
        '<div class="setup-section">' +
          '<div class="section-header">' +
            '<h1 class="section-title">Active Configuration</h1>' +
            '<span class="muted" style="font-size:11px">read-only</span>' +
          '</div>' +
          '<table class="config-table"><tbody>' + rows + '</tbody></table>' +
        '</div>';
}

function _escHtml(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
