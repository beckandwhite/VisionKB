#!/usr/bin/env bash
#
# reset.sh — clear generated pipeline artifacts so the project can be rebuilt
# from scratch. Only the cheap, fast-to-rebuild KB layer is removed.
#
# After a reset, rerun the configured works with:   python3 backend.py
#
# Usage:
#    ./reset.sh                  reset DEV
#    ./reset.sh -env ENV         dry-run ENV (except DEV)
#    ./reset.sh -dry-run         dry-run DEV
#    ./reset.sh -h               print this help
#
set -euo pipefail

# Resolve the repo root from this script's location, so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_NAME="DEV"
DRY_RUN=0

# ------------------------------------------------------------------ parsing
while [[ $# -gt 0 ]]; do
    case "$1" in
        -env)
            [[ $# -ge 2 ]] || { printf '%s: -env requires an environment.\n' "$(basename "$0")" >&2; exit 2; }
            ENV_NAME="$2"
            shift 2
            ;;
        -dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help|help)
            cat <<EOF
usage: $(basename "$0") [-env ENV] [-dry-run] [-h]

Reset behavior:
    No arguments       reset DEV immediately
    -env DEV           reset DEV immediately
    -env ENV           dry-run ENV and remove nothing
    -dry-run           dry-run the selected environment

Environments:
    DEV
    QA
    PRD-iCloud-Screenshots
    PRD-OneDrive-Pictures

The reset clears generated work artifacts in .workspace/ENV. The source tracker
and analytical result files are kept unless removed manually.
EOF
            exit 0
            ;;
        *)
            printf '%s: unknown argument %s — try "%s -h".\n' \
                 "$(basename "$0")" "$1" "$(basename "$0")" >&2
            exit 2
            ;;
    esac
done

case "$ENV_NAME" in
    DEV|QA|PRD-iCloud-Screenshots|PRD-OneDrive-Pictures) ;;
    *)
        printf '%s: unknown environment %s — try "%s -h".\n' \
             "$(basename "$0")" "$ENV_NAME" "$(basename "$0")" >&2
        exit 2
        ;;
esac

[[ "$ENV_NAME" == "DEV" ]] || DRY_RUN=1
ENV_DIR=".workspace/$ENV_NAME"

# ------------------------------------------------------------------ targets
# Always-on (cheap / regenerable in seconds via backend.py):
ALWAYS=""
[ -e "$ENV_DIR/wiki.db" ]       && ALWAYS="$ALWAYS $ENV_DIR/wiki.db"
[ -e "$ENV_DIR/wiki.ndjson" ]   && ALWAYS="$ALWAYS $ENV_DIR/wiki.ndjson"
[ -e "$ENV_DIR/tags_index.json" ] && ALWAYS="$ALWAYS $ENV_DIR/tags_index.json"
[ -e "$ENV_DIR/duplicatefinder.jsonl" ] && ALWAYS="$ALWAYS $ENV_DIR/duplicatefinder.jsonl"
[ -d "$ENV_DIR/thumbnails" ]    && ALWAYS="$ALWAYS $ENV_DIR/thumbnails"
[ -d "kb/__pycache__" ] && ALWAYS="$ALWAYS kb/__pycache__"
[ -d "__pycache__" ]   && ALWAYS="$ALWAYS __pycache__"

# ------------------------------------------------------------------ safety net
# A reset only makes sense if we really are in the project root.
if [ ! -f "backend.py" ]; then
    printf '%s: refusing to run — not in the project root (%s).\n' \
        "$(basename "$0")" "$PWD" >&2
    printf '       expected to find backend.py here.\n' >&2
    exit 1
fi

# Nothing to do?
if [ -z "$ALWAYS" ]; then
    printf 'Nothing to delete — the generated workspace is already clean.\n'
    exit 0
fi

# ------------------------------------------------------------------ report
mode="DRY-RUN (no files will be removed)"
[ "$DRY_RUN" -eq 0 ] && mode="DELETING"
printf '=== %s reset — %s ===\n' "$(basename "$0")" "$mode"
printf 'Project root: %s\n\n' "$SCRIPT_DIR"
printf 'Environment: %s\n\n' "$ENV_NAME"

if [ -n "$ALWAYS" ]; then
    printf 'Generated work artifacts:\n'
    for item in $ALWAYS; do printf '  [KB ]  %s\n' "$item"; done
else
    printf 'KB layer: (nothing present)\n'
fi
printf 'RAW output: kept\n'
printf '\n'

# ------------------------------------------------------------------ dry-run
if [ "$DRY_RUN" -eq 1 ]; then
    printf '%s\n' '-> Dry run: nothing removed.'
    exit 0
fi

# ------------------------------------------------------------------ do it
deleted=0
for item in $ALWAYS; do
    if [ -e "$item" ]; then
        rm -rf -- "$item"
        deleted=$((deleted + 1))
        printf 'rm -rf %s\n' "$item"
    fi
done

printf '\nDone. Removed %s path(s).\n' "$deleted"
printf 'Rerun configured works: python3 backend.py -env %s\n' "$ENV_NAME"
