#!/usr/bin/env bash
#
# environment_admin.sh — initialize and decommission isolated environments.
#
# The default environment is the .workspace/ directory itself (select it by
# omitting -env everywhere). Named environments are .workspace/<NAME>/ folders.
# Every config is seeded from .workspace/config.template.json, the single source of truth.
#
# Subcommands:
#     ./environment_admin.sh init [NAME] [--source PATH] [--force]
#     ./environment_admin.sh decomm NAME [--yes]
#     ./environment_admin.sh -h
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PROG="$(basename "$0")"
WORKSPACE=".workspace"
TEMPLATE="$WORKSPACE/config.template.json"
PY="/usr/bin/python3"

usage() {
    cat <<EOF
usage: $PROG <command> [options]

  init [NAME] [--source PATH] [--force]
        Seed a config from $TEMPLATE. No NAME -> the default (.workspace/).
        With a NAME -> create .workspace/NAME/. Missing name/source are
        prompted on a TTY; --force overwrites an existing config.json.
  decomm NAME [--yes]
        Remove a named environment's .workspace/NAME/ folder wholesale
        (RAW + KB + config + thumbnails + lock). The default is forbidden.
        --yes skips the confirmation.
  -h, --help        print this help.

    Environments:
     (default)    $WORKSPACE/config.json     selected by omitting -env
      NAME        $WORKSPACE/NAME/config.json created by 'init NAME'

     All configs seed from $TEMPLATE.

EOF
}

# ------------------------------------------------------------------ safety net
# Refuse to run outside the project root so we never touch the wrong tree.
if [ ! -f "$SCRIPT_DIR/backend.py" ] || [ ! -f "$SCRIPT_DIR/$TEMPLATE" ]; then
    printf '%s: refusing to run — not in the project root (%s).\n' \
          "$PROG" "$SCRIPT_DIR" >&2
    printf '       expected to find backend.py and %s here.\n' "$TEMPLATE" >&2
    exit 1
fi

[ -f "$TEMPLATE" ] || {
    printf '%s: missing %s.\n' "$PROG" "$TEMPLATE" >&2
    exit 1
}

validate_name() {
    # Reject empty, path separators, traversal, and leading-dot names.
    local name="$1"
    if [ -z "$name" ]; then return 0; fi            # empty = default target
    case "$name" in
          */*|*..*|..|.*)
            printf '%s: invalid environment name %s\n' "$PROG" "$name" >&2
            return 1 ;;
    esac
    printf '%s' "$name" | grep -Eq '^[A-Za-z0-9._-]+$' || {
        printf '%s: invalid environment name %s (use A-Za-z0-9._-)\n' \
                "$PROG" "$name" >&2
        return 1
      }
    return 0
}

# ------------------------------------------------------------------ init
cmd_init() {
    local name="" source="" force=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --source)
                [[ $# -ge 2 ]] || { printf '%s: --source requires a path.\n' "$PROG" >&2; exit 2; }
                source="$2"; shift 2 ;;
            --force) force=1; shift ;;
            -h|--help|help) usage; exit 0 ;;
            --) shift; break ;;
            -*)
                printf '%s: unknown option %s — try "%s -h".\n' "$PROG" "$1" "$PROG" >&2
                exit 2 ;;
            *)
                if [ -z "$name" ]; then name="$1";
                else printf '%s: unexpected argument %s\n' "$PROG" "$1" >&2; exit 2; fi
                shift ;;
        esac
    done

    # Prompt only for missing values, and only on an interactive terminal.
    if [ -z "$name" ] && [ -t 0 ]; then
        printf 'Environment name (blank = default .workspace/): '
        read -r name || true
    fi
    name="$(printf '%s' "$name" | tr -d '[:space:]')"

    if ! validate_name "$name"; then exit 1; fi

    if [ -z "$source" ]; then
        if [ -t 0 ]; then
            printf 'Source folder (blank = keep template default): '
            read -r source || true
        fi
    fi

    if [ -z "$name" ]; then
        target_dir="$WORKSPACE"
        label="default (.workspace/)"
    else
        target_dir="$WORKSPACE/$name"
        label="$name"
    fi
    target="$target_dir/config.json"

    if [ -f "$target" ] && [ "$force" -ne 1 ]; then
        printf '%s: %s config already exists: %s\n' "$PROG" "$label" "$target" >&2
        printf '       re-run with --force to overwrite it.\n' >&2
        exit 1
    fi

     mkdir -p "$target_dir"
     target_dir="$(cd "$target_dir" && pwd)"

    # Seed the config from the template, writing an explicit source folder when
    # one was given; otherwise the template's default source_dir is kept.
    INIT_SOURCE="$source" INIT_FORCE="$force" \
        "$PY" - "$TEMPLATE" "$target" <<'PY'
import json, os, sys
template, target = sys.argv[1], sys.argv[2]
with open(template, encoding="utf-8") as fh:
    config = json.load(fh)
source = os.environ.get("INIT_SOURCE", "").strip()
if source:
    config["source_dir"] = source
os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(config, fh, indent=2, ensure_ascii=False)
    fh.write("\n")
os.replace(tmp, target)
print("source_dir=%s" % config.get("source_dir"))
PY

    printf '%s: init %s -> %s\n' "$PROG" "$label" "$target"
    if [ -n "$name" ]; then
        printf '       run: python3 backend.py -env %s\n' "$name"
    else
        printf '       run: python3 backend.py   (omit -env for the default)\n'
    fi
}


# ------------------------------------------------------------------ decomm
cmd_decomm() {
    local name="" yes=0
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --yes|-y) yes=1; shift ;;
            -h|--help|help) usage; exit 0 ;;
            --) shift; break ;;
            -*)
                printf '%s: unknown option %s — try "%s -h".\n' "$PROG" "$1" "$PROG" >&2
                exit 2 ;;
            *)
                if [ -z "$name" ]; then name="$1";
                else printf '%s: unexpected argument %s\n' "$PROG" "$1" "$PROG" >&2; exit 2; fi
                shift ;;
        esac
    done

    if ! validate_name "$name"; then exit 1; fi

    # The default workspace is never a decomm target.
    if [ -z "$name" ]; then
        printf '%s: cannot decommission the default workspace.\n' "$PROG" >&2
        exit 1
    fi

    target_dir="$WORKSPACE/$name"
    if [ ! -d "$target_dir" ]; then
        printf '%s: no such environment: %s\n' "$PROG" "$target_dir" >&2
        exit 1
    fi

      # Refuse while the pipeline lock is actively held (release a stale one).
    if [ -e "$target_dir/.pipeline.lock" ]; then
        rc=0
        LOC_DIR="$target_dir" "$PY" - <<'PY' || rc=$?
import fcntl, os, sys
lock = os.path.join(os.environ["LOC_DIR"], ".pipeline.lock")
if not os.path.exists(lock):
    sys.exit(0)
fh = open(lock, "a", encoding="utf-8")
try:
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    fh.close()
    sys.exit(78)           # EX_IOERR -> lock is actively held
try:
    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)    # was stale -> release it
except OSError:
    pass
fh.close()
PY
         if [ "$rc" -eq 78 ]; then
            printf '%s: %s is locked — a pipeline run is active for it.\n' \
                   "$PROG" "$name" >&2
            printf '       stop that run (or use backend.py --wait) before decomm.\n' >&2
            exit 78
        fi
    fi

    printf '=== decommission %s ===\n' "$name"
    printf 'Target: %s\n' "$target_dir"
    printf 'Contents to be deleted (source images are never touched):\n'
    local item
    for item in "$target_dir"/*; do
        [ -e "$item" ] || continue
        printf '   %s\n' "$item"
    done
    if [ -e "$target_dir/.pipeline.lock" ]; then
        printf '   %s\n' "$target_dir/.pipeline.lock"
    fi
    printf 'Note: per-env configs are git-tracked, so this surfaces as a git deletion.\n'
    printf '\n'

    if [ "$yes" -ne 1 ] && [ -t 0 ]; then
        printf 'Remove %s entirely? [y/N] ' "$target_dir"
        read -r reply || reply="n"
        case "$reply" in
            y|Y|yes|YES) ;;
            *) printf '%s: aborted — %s kept.\n' "$PROG" "$target_dir"; exit 0 ;;
        esac
    fi

    rm -rf -- "$target_dir"
    printf '\nDone. Removed %s\n' "$target_dir"
}

# ------------------------------------------------------------------ dispatch
if [ $# -eq 0 ]; then
    usage
    exit 2
fi

cmd="${1:-}"
case "$cmd" in
    init)    shift; cmd_init "$@" ;;
    decomm|decommission|destroy) shift; cmd_decomm "$@" ;;
    -h|--help|help) usage ;;
    *)
        printf '%s: unknown command %s — try "%s -h".\n' "$PROG" "$cmd" "$PROG" >&2
        exit 2 ;;
esac
