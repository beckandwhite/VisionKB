"use strict";

function initTelemetry() {
    renderBacklog().catch(() => {});
    renderErrors().catch(() => {});
}

async function renderErrors() {
    const host = document.getElementById("telemetry-errors");
    if (!host) return;
    let rows;
    try {
        const res = await fetch("/api/logs", { cache: "no-store" });
        rows = await res.json();
    } catch (_) {
        host.innerHTML = "<p class='muted' style='padding:18px'>failed to load error log</p>";
        return;
    }

    if (!Array.isArray(rows) || rows.length === 0) {
        host.innerHTML =
            "<section class='section'>" +
              "<h1 class='section-title'>Recent Errors</h1>" +
              "<p class='muted' style='font-size:11px;padding:4px 0'>no errors recorded</p>" +
            "</section>";
        return;
    }

    var tbodyRows = "";
    for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        var ts = r.last_error_at ? r.last_error_at.replace("T", " ").slice(0, 19) : "—";
        tbodyRows +=
            "<tr>" +
              "<td class='err-filename'>" + _escTel(r.filename || "—") + "</td>" +
              "<td class='err-work'>"     + _escTel(r.work_name || "—") + "</td>" +
              "<td class='err-msg'>"      + _escTel(r.last_error || "—") + "</td>" +
              "<td class='err-time'>"     + _escTel(ts) + "</td>" +
            "</tr>";
    }

    host.innerHTML =
        "<section class='section'>" +
          "<h1 class='section-title'>Recent Errors</h1>" +
          "<div class='err-table-wrap'>" +
            "<table class='err-table'>" +
              "<thead><tr>" +
                "<th>File</th><th>Work</th><th>Error</th><th>Time</th>" +
              "</tr></thead>" +
              "<tbody>" + tbodyRows + "</tbody>" +
            "</table>" +
          "</div>" +
        "</section>";
}

function _escTel(s) {
    return String(s == null ? "" : s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}
