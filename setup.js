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

const EDIT_FIELDS = [
    { key: "ollama_base",      label: "Ollama base URL",                         type: "text"     },
    { key: "vision_model",     label: "Vision model",                            type: "text"     },
    { key: "embed_model",      label: "Embed model",                             type: "text"     },
    { key: "max_dim",          label: "Max image dimension",                     type: "number"   },
    { key: "save_every",       label: "Save every N files",                      type: "number"   },
    { key: "supported_images", label: "Supported image types (comma-separated)", type: "textlist" },
    { key: "source_dir",       label: "Source folders",                          type: "textarea" },
];

function initSetup() {
    var host = document.getElementById("setup-config");
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

    var rows = "<tr><th>Environment</th><td>" + _escHtml(env) + "</td></tr>";
    for (var i = 0; i < SETUP_FIELDS.length; i++) {
        var key   = SETUP_FIELDS[i][0];
        var label = SETUP_FIELDS[i][1];
        var val   = cfg[key];
        var display;
        if (val === undefined || val === null) {
            display = "<span class='muted'>—</span>";
        } else if (val === "***") {
            display = "<span class='muted'>***</span>";
        } else if (Array.isArray(val)) {
            display = _escHtml(val.join(", "));
        } else {
            display = _escHtml(String(val));
        }
        rows += "<tr><th>" + _escHtml(label) + "</th><td>" + display + "</td></tr>";
    }

    var viewerHtml =
        "<div class='setup-section'>" +
          "<div class='section-header'>" +
            "<h1 class='section-title'>Active Configuration</h1>" +
            "<span class='muted' style='font-size:11px'>read-only</span>" +
          "</div>" +
          "<table class='config-table'><tbody>" + rows + "</tbody></table>" +
        "</div>";

    host.innerHTML = viewerHtml + _buildEditForm(cfg);
    _attachFormHandlers(host);
}

function _buildEditForm(cfg) {
    var rows = "";
    for (var i = 0; i < EDIT_FIELDS.length; i++) {
        var f = EDIT_FIELDS[i];
        var val = cfg[f.key];
        var fid = "setup-f-" + f.key;
        var inputHtml;

        if (f.type === "textarea") {
            var lines = Array.isArray(val) ? val.join("\n") : (val != null ? String(val) : "");
            inputHtml =
                "<textarea class='setup-input setup-textarea' id='" + fid +
                "' name='" + f.key + "' rows='3'>" +
                _escHtml(lines) + "</textarea>" +
                "<span class='setup-hint'>one folder per line</span>";
        } else if (f.type === "textlist") {
            var listVal = Array.isArray(val) ? val.join(", ") : (val != null ? String(val) : "");
            inputHtml =
                "<input class='setup-input' type='text' id='" + fid +
                "' name='" + f.key + "' value=\"" + _escHtml(listVal) + "\">";
        } else if (f.type === "number") {
            var numVal = (val != null) ? String(val) : "";
            inputHtml =
                "<input class='setup-input' type='number' id='" + fid +
                "' name='" + f.key + "' value=\"" + _escHtml(numVal) + "\">";
        } else {
            var strVal = (val != null) ? String(val) : "";
            inputHtml =
                "<input class='setup-input' type='text' id='" + fid +
                "' name='" + f.key + "' value=\"" + _escHtml(strVal) + "\">";
        }

        rows +=
            "<div class='setup-form-row'>" +
              "<label class='setup-label' for='" + fid + "'>" + _escHtml(f.label) + "</label>" +
              "<div class='setup-field'>" + inputHtml + "</div>" +
            "</div>";
    }

    return (
        "<div class='setup-section'>" +
          "<div class='section-header'>" +
            "<h1 class='section-title'>Edit Configuration</h1>" +
          "</div>" +
          "<form id='setup-edit-form'>" +
            rows +
            "<div class='setup-form-actions'>" +
              "<button type='submit' class='btn setup-save-btn'>Save</button>" +
              "<span id='setup-save-status' class='setup-status'></span>" +
            "</div>" +
          "</form>" +
        "</div>"
    );
}

function _attachFormHandlers(host) {
    var form = host.querySelector("#setup-edit-form");
    if (!form) return;
    form.addEventListener("submit", function(e) {
        e.preventDefault();
        var status = document.getElementById("setup-save-status");
        if (status) { status.textContent = "saving…"; status.className = "setup-status"; }

        var payload = {};
        var inputs = form.querySelectorAll("[name]");
        for (var i = 0; i < inputs.length; i++) {
            var el = inputs[i];
            var key = el.getAttribute("name");
            var rawVal = el.value;

            if (key === "source_dir") {
                payload[key] = rawVal.split("\n")
                    .map(function(s) { return s.trim(); })
                    .filter(function(s) { return s.length > 0; });
            } else if (key === "supported_images") {
                payload[key] = rawVal.split(",")
                    .map(function(s) { return s.trim(); })
                    .filter(function(s) { return s.length > 0; });
            } else if (key === "max_dim" || key === "save_every") {
                var n = parseInt(rawVal, 10);
                if (!isNaN(n)) { payload[key] = n; }
            } else {
                payload[key] = rawVal;
            }
        }

        fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            cache: "no-store"
        })
        .then(function(r) {
            return r.json().then(function(body) { return { ok: r.ok, body: body }; });
        })
        .then(function(res) {
            if (!status) return;
            if (res.ok && res.body.saved) {
                status.textContent = "Saved — restart the WebUI to apply.";
                status.className = "setup-status setup-status--ok";
            } else {
                status.textContent = res.body.error || "save failed";
                status.className = "setup-status setup-status--err";
            }
        })
        .catch(function() {
            if (status) {
                status.textContent = "network error";
                status.className = "setup-status setup-status--err";
            }
        });
    });
}

function _escHtml(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
