# 004 · Private self-hosted runner for QA jobs

**Status:** Ready
**Priority:** Medium
**Component:** infra · `.github/workflows/` · runner host
**Labels:** `ci` `infra` `self-hosted` `track-b`
**Depends on:** [003](003-security-supply-chain.md) (branch protection / workflow-approval gates)
**Blocks:** [005](005-model-eval-harness.md) running *in CI*
**Plan:** [Plans/QualityAssurance-CI.md](../Plans/QualityAssurance-CI.md)

---

## Problem

Model-quality QA ([005](005-model-eval-harness.md)) needs to reach an Ollama
vision endpoint and (per Decision 3) keep macOS `sips` available. GitHub-hosted
runners can do neither. We provision a self-hosted runner with a locked topology
and a **public-repo-safe** security posture.

**Locked at grooming:**
- **Topology = Option 2** (Decision 4): a **native macOS runner** that calls
  Ollama on a **separate LAN Mac**. Native (not Docker) keeps `sips`. Co-located
  Option 1 (Ollama on the runner box, `localhost`) is the documented fallback if
  the second machine is unavailable — same workflow, only `OLLAMA_BASE` changes.
- **Repo = public** (Decision 7): a self-hosted runner on a public repo is a
  standing RCE target. Fork-PR code must be **structurally unable** to run on it.

---

## Topology (locked: Option 2)

```
 GitHub ──(long-poll)──►  macOS runner (native, non-admin user)   ── LAN ──►  Ollama Mac
                          • sips present                                       • 127.0.0.1 + LAN iface
                          • runs eval/ (005)                                   • firewalled / mesh VPN
                          • no admin, ephemeral workdir                        • models pulled per README
```

`OLLAMA_BASE` (e.g. `http://<ollama-host>.local:11434` or the mesh IP) is a repo
**secret / runner env var**, never committed — mirrors `config["ollama_base"]`.
Fallback Option 1: run Ollama on the runner box and set `OLLAMA_BASE=http://127.0.0.1:11434`.

---

## Acceptance criteria

**Runner registration & identity**
- [ ] Runner registered with a **narrow, unique label** `self-hosted-macos-ollama`;
      QA jobs target `runs-on: [self-hosted, self-hosted-macos-ollama]`.
- [ ] Runner process runs as a **dedicated non-admin macOS user**, installed as a
      launchd service, with a scoped working directory.
- [ ] Registration uses **just-in-time / ephemeral** runners where practical, or
      at minimum `--ephemeral` so state does not persist across jobs.

**Public-repo gating (hard requirement)**
- [ ] The self-hosted job triggers **only** on `workflow_dispatch` and `push` to
      trusted branches (`main`). It **never** uses `pull_request` from forks and
      **never** `pull_request_target`.
- [ ] Repo setting: **"Require approval for all external contributors"** (Settings
      → Actions → Fork pull request workflows) is enabled and documented.
- [ ] PR-time lint/SAST ([002](002-baseline-ci.md)/[003](003-security-supply-chain.md))
      stay on **GitHub-hosted** runners; nothing fork-triggered ever lands on the
      self-hosted label.
- [ ] Workflow declares least-privilege `permissions:` (`contents: read`) and
      checkout uses `persist-credentials: false`.

**Network & secrets**
- [ ] Ollama on the LAN Mac binds to `127.0.0.1` **plus** the private LAN/mesh
      interface only; a firewall rule blocks WAN. Reached over LAN or
      Tailscale/WireGuard — **never** the open internet.
- [ ] `OLLAMA_BASE` and any API keys (for 005's API judge) come from repo secrets
      / runner env; none are committed.

**Verification & docs**
- [ ] A gated `runner-smoke` (`workflow_dispatch`) workflow proves the runner
      executes, sees `sips` (`which sips`), and reaches Ollama
      (`curl -sf "$OLLAMA_BASE/api/tags"`), failing fast with a clear message.
- [ ] Runbook (in `Plans/` or `SECURITY.md`): install, launchd start/stop, rotate
      the registration token, update the runner, and **decommission** it.

---

## Proposed implementation

1. **Provision the Ollama Mac**: models pulled per `README.md`; bind + firewall
   as above; confirm `curl -sf http://<host>.local:11434/api/tags`.
2. **Provision the runner Mac**: dedicated non-admin user; confirm `sips` present.
3. **Register** (Settings → Actions → Runners → New self-hosted): run `config.sh`
   with `--labels self-hosted-macos-ollama --ephemeral`; install as launchd svc.
4. **Repo hardening**: enable "require approval for all external contributors";
   confirm branch protection from [003](003-security-supply-chain.md) is on `main`.
5. **Add `OLLAMA_BASE`** (and later API-judge key) as repo secrets.
6. **Smoke workflow** (`.github/workflows/runner-smoke.yml`):

   ```yaml
   name: runner-smoke
   on: { workflow_dispatch: {} }
   permissions: { contents: read }
   jobs:
     smoke:
       runs-on: [self-hosted, self-hosted-macos-ollama]
       steps:
         - uses: actions/checkout@<sha>
           with: { persist-credentials: false }
         - run: which sips
         - name: Ollama reachable
           env: { OLLAMA_BASE: ${{ secrets.OLLAMA_BASE }} }
           run: curl -sf "$OLLAMA_BASE/api/tags" >/dev/null && echo "ollama ok"
   ```

7. **Runbook** + decommission steps.

---

## Out of scope

- The eval logic itself → [005](005-model-eval-harness.md).
- Linux image-ops backend to make a Docker/Linux runner viable — deferred by Decision 3.
- Autoscaling multiple runners — single runner is sufficient for now.

---

## References

- `work_common.py` (`ollama_base`, `vision_model`), `config.template.json`
- `README.md` (model pull instructions), `recommendedHW.md`
- Self-hosted runner security (esp. public repos): https://docs.github.com/actions/hosting-your-own-runners/managing-your-self-hosted-runners/about-self-hosted-runners#self-hosted-runner-security
- Just-in-time / ephemeral runners: https://docs.github.com/actions/hosting-your-own-runners/managing-your-self-hosted-runners/autoscaling-with-self-hosted-runners
- Approving fork workflows: https://docs.github.com/actions/managing-workflow-runs/approving-workflow-runs-from-public-forks
