# Security Policy

This repository is **public**. It also integrates with a **self-hosted CI runner**
that can reach a local Ollama vision endpoint on a private network. Both facts
shape the policy below. See [Plans/QualityAssurance-CI.md](Plans/QualityAssurance-CI.md)
and [Issues/003](Issues/003-security-supply-chain.md) / [Issues/004](Issues/004-private-runner.md)
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
  sanitized or synthetic. See [Issues/005](Issues/005-model-eval-harness.md).
- The eval **API judge** receives derived **text only** (candidate model outputs
  + rubric), never images.

## Supported versions

This is an experimental project; only the latest `main` is supported.
