# Self-hosted QA runner — runbook

Operational runbook for the **self-hosted macOS runner** that runs the gated
QA / model-eval jobs. The runner itself is provisioned manually (companion
issue [#31](https://github.com/beckandwhite/VisionKB/issues/31)); this document
covers install, run, rotate, update, and **decommission**.

> **Security first.** Because the repo is **public** and the runner can reach a
> local Ollama endpoint, the runner runs only trusted code. Read
> [../SECURITY.md](../SECURITY.md) — "Self-hosted runner policy" — before doing
> anything here. The guarantees that make this safe:
>
> - The runner is a **dedicated, non-admin Unix user**.
> - Working dir is **ephemeral**; **secrets are not persisted between jobs**.
> - Self-hosted jobs trigger **only** on `workflow_dispatch` and `push` to
>   `main` (never fork `pull_request`, never `pull_request_target`).
> - `OLLAMA_BASE` (and any judge API key) come from a **repository secret** or
>   runner environment — never committed.
> - The Ollama endpoint is bound to loopback + a private LAN/mesh interface,
>   firewalled from the WAN.

## 0. Conventions

| Item | Value |
|---|---|
| OS | macOS (the pipeline needs `sips`). |
| Runner user | a dedicated non-admin account, here called `ghrunner`. |
| Label | `self-hosted-macos-ollama` (the workflow's `runs-on` requires both `self-hosted` and this label). |
| Endpoint secret | `OLLAMA_BASE`, e.g. `http://<ollama-host>.local:11434` (LAN/mesh only). |

## 1. Install

Run all install commands as the `ghrunner` user, **not** root.

1. **Download the GitHub Actions runner** into the runner home.

```bash
# As ghrunner, in the runner home.
git clone https://github.com/actions/runner.git runner
cd runner
# Match the host architecture:
./config.sh --url https://github.com/beckandwhite/VisionKB \
            --token <REGISTRATION_TOKEN> \
            --labels self-hosted-macos-ollama
```

Replace `<REGISTRATION_TOKEN>` with a token generated from the repo
(**Settings → Actions → Runners → New self-hosted runner**). The token is
one-shot for registration and is **not** reused.

2. **Make it start on boot** via launchd (§2). Do **not** run the runner as a
   root service.
3. **Provide the endpoint secret.** On GitHub: **Settings → Secrets and
   variables → Actions → OLLAMA_BASE**, set to the LAN/mesh URL. For
   `workflow_dispatch` runs you may also pass the value as runner environment at
   launch (§2). Never put the value in the repo.

## 2. Start / stop (launchd)

Provide a **user-level LaunchAgent** so the runner runs as `ghrunner` with an
ephemeral working dir. Example
`~/Library/LaunchAgents/com.ghactions.runner.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
   "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
   <key>Label</key>
   <string>com.ghactions.runner</string>
   <key>ProgramArguments</key>
   <array>
      <string>/Users/ghrunner/runner/run.sh</string>
   </array>
   <key>EnvironmentVariables</key>
   <dict>
      <key>OLLAMA_BASE</key>
      <string>http://REPLACE-ollama-host.local:11434</string>
   </dict>
   <key>RunAtLoad</key>
   <true/>
   <key>KeepAlive</key>
   <true/>
</dict>
</plist>
```

```bash
# Start
launchctl load   ~/Library/LaunchAgents/com.ghactions.runner.plist
# Stop
launchctl unload ~/Library/LaunchAgents/com.ghactions.runner.plist
```

Prefer loading the secret from the launchd `EnvironmentVariables` (or a
`runner.env` that is `chmod 600`) rather than hard-coding it in a committed file.

## 3. Verify

Trigger the `self-hosted-runner-smoke` workflow
([.github/workflows/runner-smoke.yml](../../.github/workflows/runner-smoke.yml))
via **Run workflow** (`workflow_dispatch`). A green run means:

- the host is macOS (`which sips` succeeded), and
- the runner could reach Ollama (`$OLLAMA_BASE/api/tags` answered).

The smoke workflow is wired to fail fast with a clear message on either error.

## 4. Token rotation

Registration tokens are **one-shot**; the long-lived runner lives in GitHub's
runner list. Rotate credentials by:

1. **Discard** any registration token value you still have (it cannot be reused
   after registration).
2. To replace the runner, generate a **new** token at
   **Settings → Actions → Runners → New self-hosted runner** and re-run the §1
   `./config.sh` with the new token.
3. Rotate the `OLLAMA_BASE` endpoint secret in **Settings → Secrets → Actions**
   if the endpoint ever changed; the runner picks it up on the next launch.

## 5. Update the runner

```bash
cd ~/runner
./svc.sh stop             # or: launchctl unload .../com.ghactions.runner.plist
git checkout stable       # or the pinned commit you prefer
./config.sh --url https://github.com/beckandwhite/VisionKB \
            --token <NEW_REGISTRATION_TOKEN> \
            --labels self-hosted-macos-ollama
./svc.sh start            # or: launchctl load ...
```

Re-running `config.sh` with a fresh one-shot token is how a runner is
re-registered. Pin updates to a commit, not a floating branch, where possible.

## 6. Decommission

Decommission a runner when its host leaves the network or is repurposed:

1. **Stop & unload** the agent:

```bash
launchctl unload ~/Library/LaunchAgents/com.ghactions.runner.plist
```

2. **Unregister** the runner on GitHub: **Settings → Actions → Runners** → the
   runner row → **Delete**. This removes it from the pool so no future job is
   routed to it.
3. **Remove local state.** Because the working dir is ephemeral and secrets are
   not persisted between jobs there is normally nothing sensitive to clean, but
   delete anything left under the runner home and any `runner.env`:

```bash
rm -rf ~/runner
```

4. **Confirm** the runner no longer appears under
   **Settings → Actions → Runners**, and that the firewall rule for the Ollama
   endpoint (loopback + LAN/mesh only) is still in place.

---

### Related

- Policy rationale and guarantees: [../SECURITY.md](../SECURITY.md).
- Why the runner/CI shape is as it is: [QualityAssurance-CI.md](QualityAssurance-CI.md).
- The smoke workflow this runbook verifies:
  [../../.github/workflows/runner-smoke.yml](../../.github/workflows/runner-smoke.yml).
- Manual provisioning / registration task:
  [#31](https://github.com/beckandwhite/VisionKB/issues/31).
