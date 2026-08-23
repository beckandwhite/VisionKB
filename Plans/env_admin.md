opencode -s ses_fd26bc45dffexohmZvn2MoH0DE



# Environment Administration Plan

## Goal

Replace `reset.sh` with `environment_admin.sh`, providing one administrative entry point for initializing, resetting, unlocking, and decommissioning isolated environments.

## 1. Rename and command structure

Rename `reset.sh` to `environment_admin.sh` and update documentation references.

Use explicit subcommands:

```bash
./environment_admin.sh init
./environment_admin.sh reset --env QA
./environment_admin.sh unlock --env QA
./environment_admin.sh decomm --env QA
```

Keep `--help` available globally and provide clear errors for missing or invalid arguments.

## 2. Initialize an environment

Implement `init` with interactive prompts and corresponding non-interactive options for:

- environment name
- source folder
- vision model
- embedding model
- Ollama URL
- processing limit
- maximum image dimension
- checkpoint frequency
- supported image extensions and tag list, where applicable

The command should:

1. Validate the environment name and requested settings.
2. Expand and validate the source folder where appropriate.
3. Create `.workspace/<env>/`.
4. Generate `.workspace/<env>/config.json` using the existing defaults from `config_loader.py`.
5. Write a complete configuration, including inherited values such as `TAG_LIST`.
6. Refuse to overwrite an existing environment unless an explicit replacement option is supplied.

Update environment discovery so newly initialized environments are recognized without requiring a hard-coded change to `SUPPORTED_ENVIRONMENTS`.

## 3. Reset an environment

Move the current reset behavior into the `reset` subcommand, with these changes:

- `reset` must default to dry-run for every environment, including `DEV`.
- Select the target using `--env ENV`.
- Preserve expensive raw processing artifacts:
  - `_annotations.jsonl`
  - `_tracker.json`
- Report or remove only regenerable KB artifacts:
  - `wiki.db`
  - `wiki.ndjson`
  - `tags_index.json`
  - `thumbnails/`
  - relevant Python caches
- Add `--apply` for confirmed deletion and `--yes` for non-interactive use.
- Refuse to reset while the target environment's pipeline lock is actively held.
- Retain the project-root safety check and show the full list of targets before deletion.

## 4. Unlock an environment

Implement `unlock` for the per-environment `.pipeline.lock` used by `pipeline.py`.

The command should:

1. Resolve the target environment.
2. Attempt a non-blocking lock acquisition.
3. If the lock is held, report that processing is active and refuse to remove the file.
4. If the lock is not held, remove the stale lock file.
5. Optionally support an explicit `--force` escape hatch for damaged lock metadata.

Document that deleting a lock file while a process is still running can allow concurrent writers. The command should therefore check lock ownership before removal rather than blindly deleting the path.

## 5. Decommission an environment

Implement `decomm` to remove an environment completely.

The command should:

- Require an explicit environment name.
- Refuse to operate while the environment lock is active.
- Display the exact `.workspace/<env>/` directory and its contents before removal.
- Require confirmation, with `--yes` for automation.
- Remove configuration, annotations, tracker, database, exports, thumbnails, and the lock file.
- Require an additional explicit safeguard for production-named environments, such as a second confirmation or a force flag.

Decommissioning should not remove source images from the configured source folder unless that behavior is explicitly added later as a separate, clearly named operation.

## 6. Update configuration loading

Modify `config_loader.py` so environment validation and discovery are based on initialized directories under `.workspace/`, while preserving the existing default environment behavior.

Ensure one of the following remains true:

- every initialized config contains a complete `TAG_LIST`; or
- the existing `DEV` fallback remains valid for older configurations.

Keep existing consumers such as `pipeline.py`, `frontend.py`, and `run.sh` compatible with the new environment discovery behavior.

## 7. Documentation

Update `README.md` with:

- the new script name
- the four subcommands
- interactive and non-interactive initialization examples
- reset dry-run and apply behavior
- stale-lock handling and its safety implications
- decommissioning warnings and confirmation behavior

Example usage:

```bash
./environment_admin.sh init
./environment_admin.sh reset --env QA
./environment_admin.sh reset --env QA --apply
./environment_admin.sh unlock --env QA
./environment_admin.sh decomm --env QA
```

## 8. Verification

Add focused checks for:

- initialization creating a usable environment folder and config
- newly initialized environments being accepted by the pipeline
- reset showing targets without changing files by default
- reset `--apply` removing only generated artifacts
- unlock removing stale locks and refusing active locks
- decommissioning removing the complete environment
- existing environments continuing to load unchanged
- source images remaining untouched by decommissioning

Run shell syntax checks and Python compile checks for all affected scripts and modules.
