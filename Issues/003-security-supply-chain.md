# 003 · Security scanning & supply-chain hardening

**Status:** Ready
**Priority:** High
**Component:** `.github/workflows/` · `.github/dependabot.yml` (new)
**Labels:** `security` `ci` `supply-chain` `track-a`
**Depends on:** [002](002-baseline-ci.md) (workflow skeleton, SHA-pinning convention)
**Plan:** [Plans/QualityAssurance-CI.md](../Plans/QualityAssurance-CI.md)

---

## Problem

No automated security signal exists. Because the runtime is **stdlib-only**,
the highest-value scanning is not dependency CVEs but: **SAST on our own code**,
**secret scanning**, and **GitHub Actions supply-chain hardening**. Dependabot
still earns its place by keeping *Actions* versions and any dev tooling current.

A specific hazard for later tracks: [004](004-private-runner.md) introduces
self-hosted runners. Self-hosted runners that execute untrusted fork-PR code are
an RCE vector against the LAN. Branch protection + workflow-approval settings
established here are a prerequisite for doing that safely.

---

## Acceptance criteria

- [ ] **CodeQL** analysis (`python`, and `javascript` for `app.js`) runs on PRs
      and on a weekly schedule; results appear in the Security tab.
- [ ] **Bandit** (Python SAST) runs on PRs; findings surface as CI annotations
      or SARIF upload. Baseline is triaged (real issues fixed; accepted findings
      documented in config, not blanket-ignored).
- [ ] **Secret scanning**: gitleaks (or GitHub secret scanning if the repo plan
      allows) runs on PRs and fails on a new secret. `config.template.json` is
      confirmed to contain **no** real secrets (it ships placeholder hosts/models).
- [ ] **Dependabot** (`.github/dependabot.yml`) enabled for `github-actions`
      (weekly) and `pip` (only if/when a `requirements-dev.txt` exists).
- [ ] **GHA hardening**: all workflows use SHA-pinned actions, top-level
      least-privilege `permissions:`, and `persist-credentials: false` on
      checkout where a token isn't needed.
- [ ] **Branch protection on `main`** documented (and applied if the operator has
      admin): require CI + CodeQL to pass, require PR review, and **require
      approval to run workflows for PRs from first-time/fork contributors**
      (gates self-hosted runner exposure in 004).
- [ ] A short `SECURITY.md` describes reporting and the self-hosted-runner policy.
- [ ] All scans green (or triaged-and-documented) on a throwaway PR.

---

## Proposed implementation

**`.github/dependabot.yml`:**

```yaml
version: 2
updates:
  - package-ecosystem: "github-actions"
    directory: "/"
    schedule: { interval: "weekly" }
  # Enable once dev tooling is pinned in a requirements file:
  # - package-ecosystem: "pip"
  #   directory: "/"
  #   schedule: { interval: "weekly" }
```

**`.github/workflows/codeql.yml`** — standard `github/codeql-action` init →
autobuild → analyze matrix over `["python", "javascript"]`, `permissions:
{ security-events: write, contents: read }`, weekly `schedule`.

**`.github/workflows/security.yml`** (or extra jobs in `ci.yml`):

```yaml
  bandit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>
        with: { persist-credentials: false }
      - uses: actions/setup-python@<sha>
        with: { python-version: "3.11" }
      - run: pipx install bandit
      - run: bandit -r . -x ./.venv,./tests,./__pycache__ -f sarif -o bandit.sarif || true
      - uses: github/codeql-action/upload-sarif@<sha>
        with: { sarif_file: bandit.sarif }

  gitleaks:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<sha>
        with: { fetch-depth: 0, persist-credentials: false }
      - uses: gitleaks/gitleaks-action@<sha>
```

**Dev-deps hygiene (small, optional but recommended):** add
`requirements-dev.txt` pinning the tools we introduce (`ruff`, `bandit`,
optionally `pytest`) so Dependabot's `pip` ecosystem has something to track and
CI installs are reproducible. Do **not** add runtime deps — the pipeline stays
stdlib-only.

> **Bandit note:** expect a finding on `work_common.py` for `urllib.request.urlopen`
> with a non-`https` URL (Ollama is plain HTTP on the LAN). Triage as an accepted
> finding (documented) rather than suppressing broadly.

---

## Out of scope

- `pip-audit`/dependency CVE scanning — no runtime deps to scan; revisit if the
  runtime ever takes a third-party dependency (e.g. the HEIC fix in [001](001-heic-conversion.md)
  adds Pillow — at that point enable `pip-audit` and the Dependabot `pip` block).
- Standing up the self-hosted runner itself → [004](004-private-runner.md).

---

## References

- `work_common.py` (plain-HTTP Ollama call — expected Bandit finding)
- CodeQL: https://codeql.github.com/
- Hardening GitHub Actions: https://docs.github.com/actions/security-guides/security-hardening-for-github-actions
- Self-hosted runner security: https://docs.github.com/actions/hosting-your-own-runners/managing-your-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security
