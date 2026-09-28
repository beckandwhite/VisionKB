# Security Policy

This repository is **public**. It also integrates with a **self-hosted CI runner**
that can reach a local Ollama vision endpoint on a private network. Both facts
shape the policy below. See [Plans/QualityAssurance-CI.md](Plans/QualityAssurance-CI.md)
and [#30](https://github.com/beckandwhite/VisionKB/issues/30) / [#31](https://github.com/beckandwhite/VisionKB/issues/31)
for the full rationale.

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub's
**"Report a vulnerability"** button on the repository's **Security** tab
(Private Vulnerability Reporting). Do **not** open a public issue for a security
report.

We aim to acknowledge reports within a few days. Since this is an experimental,
personal-scale project, there is no formal SLA.

## Self-hosted runner policy

The QA / model-eval jobs run on a **self-hosted macOS runner** on a private LAN.
Because the repo is public, untrusted code must never execute on that runner:

- Self-hosted jobs trigger **only** on `workflow_dispatch` and `push` to trusted
  branches (`main`). They never run on fork `pull_request`, and never use
  `pull_request_target`.
- **"Require approval for all external contributors"** is enabled for Actions
  (Settings → Actions → Fork pull request workflows).
- PR-time lint / SAST / secret-scanning run on **GitHub-hosted** runners only.
- The runner runs as a **dedicated non-admin user** with an ephemeral working
  directory; secrets are not persisted between jobs.
- The Ollama endpoint is bound to loopback + a private LAN/mesh interface,
  firewalled from the WAN, and reached over LAN or a mesh VPN (Tailscale /
  WireGuard) — **never** exposed to the open internet.

If you believe a workflow or setting weakens these guarantees, treat it as a
security report (above).

## Secrets & sensitive data

- `config.template.json` contains **placeholders only** (host names, model ids) —
  no real secrets. Secret scanning (gitleaks) runs in CI.
- Runtime endpoints and API keys (`OLLAMA_BASE`, any judge API key such as
  `ANTHROPIC_API_KEY`) are supplied via repository secrets or runner environment,
  never committed.
- **Personal images are never committed.** Source screenshots live outside the
  repo; the model-eval dataset (`eval/dataset/`) is gitignored and must be
  sanitized or synthetic. See [#32](https://github.com/beckandwhite/VisionKB/issues/32).
- The eval **API judge** receives derived **text only** (candidate model outputs
  + rubric), never images.

## Supported versions

This is an experimental project; only the latest `main` is supported.

## Setup tab write route

The Setup tab's **Edit Configuration** form (`POST /api/config`) writes changes to the
active environment's `config.json`. Security properties:

- **Localhost-only.** The server binds to `127.0.0.1` by default — this route is not
  reachable from the network without an explicit `--host` override.
- **No authentication.** This is a single-operator tool; the WebUI has no auth layer.
- **Allowlisted fields only.** The endpoint rejects unknown keys and wrong types
  (HTTP 400). `works[]` and `TAG_LIST` are not writable. Secrets (keys whose name
  contains `key`, `token`, or `secret`) are redacted in the GET response and cannot
  be written via this route.
- **Atomic write.** Changes are written via a temp-file + `os.replace` (same path as
  other config writes) and the current config is backed up as
  `config.json.<UTC-timestamp>.bak` before each save (5 newest kept).
- **Restart required.** The running server never hot-reloads `ENV_CONFIG`; changes
  take effect only after restarting `frontend.py`.

Example usage (with the server running on port 8000):

```bash
# Read the active config (secrets redacted)
curl http://localhost:8000/api/config

# Update the vision model
curl -X POST http://localhost:8000/api/config \
     -H "Content-Type: application/json" \
     -d '{"vision_model": "muse-glimmer:30b-mlx"}'
```
