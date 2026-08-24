# Environment Administration Plan

Simplified: `.workspace/` **is** the default environment; named environments are
subfolders under it; one **template** seeds every config. Replace `reset.sh` with
`environment_admin.sh` exposing only **`init`** and **`decomm`**.

## Goal

Make the quickstart effortless (UserJourney UC1/UC2: usable in ~5 minutes) and keep
named environments isolated and disposable. A single template is the source of truth
for every config; the power user edits `config.json` directly when they need more
(a WebUI config section is a later, separate effort).

## Locked decisions

- **Default env** = the `.workspace/` directory itself. It holds `config.json`,
  `_tracker.json`, `_annotations.jsonl`, `wiki.ndjson`, `tags_index.json`, and
  `thumbnails/` directly. It is **special**: no name selects it — you just **omit
  `-env`**.
- **Named envs** = `.workspace/<ENV>/` subfolders. The existing `DEV`, `QA`,
  `PRD-iCloud-Screenshots`, `PRD-OneDrive-Pictures` are kept as-is.
- **Single source of truth**: a new repo-root **`config.template.json`** replaces
  the hard-coded `DEFAULT_CONFIG` dict in `config_loader.py` (no drift). The
  template carries a **complete, canonical `TAG_LIST`**, so every created env is
  self-contained.
- **`init`** is both **auto** (first run bootstraps a missing default `config.json`)
  and **explicit** (`environment_admin.sh init [NAME] --source PATH`). Interactive
  prompts ask **only name + source folder**; every other value comes from the
  template.
- **Auto-create on `-env X`**: `backend.py` creates a missing named env on first
  run, inheriting the root default's `source_dir`. No gate — a typo such as
  `-env QAl` is allowed to create `.workspace/QAl`.
- **`decomm`**: full nuke of a named subfolder (RAW + KB + config + thumbnails +
  lock). The **root/default is forbidden** as a target. Source images are never
  touched.
- **Drop `reset` and `unlock`** — workers run separately (per-worker selection
  handles rework), and stale-lock recovery is just `--wait` or a manual delete.
- **Out of scope**: per-worker selection (§9 `--only`/`--work`/`enable`),
  KB-artifact generation (no generator exists in-repo yet; `wiki.db`/`wiki.ndjson`/
  `tags_index.json` are today read-only-consumed by `frontend.py`), and
  `.gitignore` migration.

## 1. Template + `config_loader.py` refactor

- **Add `config.template.json`** at repo root: the full config schema **plus a
  complete `TAG_LIST`**. It is the only place defaults are written.
- **`config_loader.py`**:
   - `load_defaults()` reads `config.template.json`; delete the hard-coded
     `DEFAULT_CONFIG` dict and the `SUPPORTED_ENVIRONMENTS` tuple.
   - **Root sentinel**: a name meaning the default maps to `env_dir = WORKSPACE_DIR`
     (`.workspace/`) and `config_path = .workspace/config.json`; any other name maps
     to `.workspace/<name>/`.
   - **`available_environments()`**: the root default **plus** every immediate
     subfolder of `.workspace/` that contains a `config.json`, sorted. No hard-coded
     list.
   - **`load_config(env)`**: if the config path is missing, **auto-bootstrap** by
     copying the template and writing the resolved `source_dir` into it. Root copies
     the template as-is; a named env inherits the **root default's `source_dir`**
     unless `init --source` set one.
   - **Delete the `env != "DEV"` TAG_LIST inheritance branch** (`config_loader.py:91-95`);
     the template's canonical `TAG_LIST` replaces it.
- **`backend.py` / `frontend.py` `-env` redesign**: drop `argparse choices=`
  (it cannot express "omit = root, arbitrary names allowed, auto-create").
   - `backend.py`: validate/resolve via `available_environments()` for known names,
     but **auto-create** any unknown name on first run (no gate).
   - `frontend.py`: **hard-error** on an unknown/missing env and print the available
     list as a hint.

## 2. `environment_admin.sh` (renamed from `reset.sh`)

Subcommands: `init`, `decomm`, plus a global `-h`.

### 2.1 `init`
```bash
./environment_admin.sh init                                # refresh/create DEFAULT (.workspace/)
./environment_admin.sh init --source ~/iCloud/Screens/     # refresh DEFAULT with a source
./environment_admin.sh init QA --source ~/iCloud/Screens/  # create a named env with a source
./environment_admin.sh init QA                             # interactive (no flags): prompts name + source
```
- **No name** → target is the **default root**: copy template to
  `.workspace/config.json`; write the `source_dir` in (default or `--source`).
- **With name** → validate the name (`[A-Za-z0-9._-]`, no `/`, no `..`, no leading
  `.` — blocks path traversal), create `.workspace/<NAME>/`, copy the template in,
  and write `--source` (or the prompted source) into `source_dir`.
- The only interactive questions are **name** and **source folder**; all other
  fields are inherited from the template.
- Refuse to overwrite an existing `.workspace/<NAME>/config.json` unless an explicit
  `--force` replace is supplied.
- Keep the **project-root safety check** (refuse to run outside the repo root).

### 2.2 `decomm`
```bash
./environment_admin.sh decomm QA
./environment_admin.sh decomm QA --yes
```
- Target **must be a named subfolder**; refuse the root/default ("cannot
  decommission the default workspace").
- Refuse if the env's `.pipeline.lock` is actively held (attempt a non-blocking
  `flock`; remove only a stale lock).
- **Print** the exact `.workspace/<NAME>/` path and its contents (RAW:
  `_annotations.jsonl`, `_tracker.json`, `work*.jsonl`, `duplicatefinder.jsonl`;
  KB: `wiki.db`, `wiki.ndjson`, `tags_index.json`, `thumbnails/`; plus
  `.pipeline.lock`, `config.json`) before removal.
- Confirm, or `--yes` for automation. Then `rm -rf` the subfolder. **Never delete
  source images.** Note that per-env configs are git-tracked, so this surfaces as a
  git deletion.

## 3. Remove `reset.sh`

Delete `reset.sh` — its KB-only clear is no longer a command. Update
`Plans/readme_newuser_journey_readme.md:12` and any other doc mention.

## 4. README updates

- New script name and the `init`/`decomm` subcommands with `-h`.
- Default = the `.workspace/` root; how to omit `-env`; auto-bootstrap
  (first run) and auto-create (`backend.py -env X`, typos allowed).
- `init` examples (default refresh + named create) and the two-prompt interactive
  flow.
- `decomm` warnings: full nuke, root forbidden, source images safe, git-deletion
  note.
- Remove all `reset`/`unlock` mentions.
- Note that config values come from `config.template.json` and the power user edits
  `config.json` directly.

## 5. Verification

A small **`tests/`-style shell + `python3` smoke script** (run before merge; no
framework):

- `init` (no args) creates a usable `.workspace/config.json` from the template with
  a complete `TAG_LIST`.
- `init QA --source …` creates `.workspace/QA/config.json` with that `source_dir`;
  name validation rejects `../evil`.
- `available_environments()` lists the root default + subfolders; a freshly init'd
  env is accepted by `backend.py`.
- `backend.py -env NEWENV` auto-creates on first run; `frontend.py -env MISSING`
  errors and lists the available envs.
- `decomm QA --yes` removes the whole subfolder; `decomm` of the root is refused; a
  decomm with an active lock is refused.
- Source images are untouched by `decomm`.
- Existing `DEV`/`QA`/`PRD-*` still load unchanged.
- `bash -n environment_admin.sh`; `python3 -m py_compile` on every touched module.

## 6. Out of scope

- Per-worker selection from the old §9 (`--only`/`--work` + `enable` subcommand).
- KB-artifact generation (`wiki.db`/`wiki.ndjson`/`tags_index.json` have no
  in-repo generator yet).
- `.gitignore` migration (per-env configs stay git-tracked as today).
- `unlock` subcommand.
