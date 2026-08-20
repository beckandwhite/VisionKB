#!/usr/bin/env bash
#
# reset.sh — clear *generated* pipeline artifacts so the project can be rebuilt
# from scratch. Safe by default: it only removes the cheap, fast-to-rebuild KB
# layer (exports/, kb/data/, caches). The expensive raw classifier output
# (_annotations.jsonl / telemetry.log / _tracker.json, ~90 s/image to regenerate)
# is left in place unless you pass --full.
#
# After a reset, rebuild the KB layer with:   python3 kb/build_kb.py
#
# Usage:
#    ./reset.sh              dry-run: list what would be deleted (nothing removed)
#    ./reset.sh --apply      delete the KB layer, after a confirmation prompt
#    ./reset.sh --yes        delete the KB layer, non-interactively
#    ./reset.sh --full --apply  also delete the raw classifier output
#    ./reset.sh -h           print this help
#
set -euo pipefail

# Resolve the repo root from this script's location, so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DELETING=0             # default: dry-run, remove nothing
FULL=0                 # also remove raw classifier output
ASSUME_YES=0           # --yes skips the confirmation prompt

# ------------------------------------------------------------------ parsing
for arg in "$@"; do
    case "$arg" in
        --dry-run|-n)   DELETING=0 ;;
        --apply)        DELETING=1 ;;
        --yes|-y)       DELETING=1; ASSUME_YES=1 ;;
        --full)         FULL=1 ;;
        -h|--help|help)
            cat <<EOF
usage: $(basename "$0") [--apply] [--yes|-y] [--full] [--dry-run|-n] [-h]

    (default)         dry-run: list what would be deleted, remove nothing
    --apply           actually delete the KB layer, after a confirmation prompt
    --yes, -y         delete without prompting (for scripts/cron)
    --full            also delete expensive raw output (_annotations.jsonl,
                      telemetry.log, _tracker.json); ~90 s/image to regenerate.
                      Default: keep.
    --dry-run, -n     show what would be removed, change nothing (default)
    -h, --help        print this help and exit

Targets:
   KB layer (always, cheap; rebuild with python3 kb/build_kb.py):
      exports/   kb/data/   __pycache__/
   RAW output (only with --full; rebuild with python3 classify_images.py):
      _annotations.jsonl   telemetry.log   _tracker.json
EOF
            exit 0 ;;
        *)
            printf '%s: unknown argument %s — try "%s -h".\n' \
                 "$(basename "$0")" "$arg" "$(basename "$0")" >&2
            exit 2 ;;
    esac
done

# ------------------------------------------------------------------ targets
# Always-on (cheap / regenerable in seconds via kb/build_kb.py):
ALWAYS=""
[ -d "exports" ]       && ALWAYS="$ALWAYS exports"
[ -d "kb/data" ]       && ALWAYS="$ALWAYS kb/data"
[ -d "kb/__pycache__" ] && ALWAYS="$ALWAYS kb/__pycache__"
[ -d "__pycache__" ]   && ALWAYS="$ALWAYS __pycache__"

# Optional --full (expensive: needs the vision model to regenerate):
RAW=""
[ -f "_annotations.jsonl" ] && RAW="$RAW _annotations.jsonl"
[ -f "telemetry.log" ]      && RAW="$RAW telemetry.log"
[ -f "_tracker.json" ]       && RAW="$RAW _tracker.json"

# ------------------------------------------------------------------ safety net
# A reset only makes sense if we really are in the project root.
if [ ! -f "classify_images.py" ] || [ ! -f "kb/build_kb.py" ]; then
    printf '%s: refusing to run — not in the project root (%s).\n' \
        "$(basename "$0")" "$PWD" >&2
    printf '       expected to find classify_images.py + kb/build_kb.py here.\n' >&2
    exit 1
fi

# Nothing to do?
if [ -z "$ALWAYS" ] && [ -z "$RAW" ]; then
    printf 'Nothing to delete — the generated workspace is already clean.\n'
    exit 0
fi

# ------------------------------------------------------------------ report
mode="DRY-RUN (no files will be removed)"
[ "$DELETING" -eq 1 ] && mode="DELETING"
printf '=== %s reset — %s ===\n' "$(basename "$0")" "$mode"
printf 'Project root: %s\n\n' "$SCRIPT_DIR"

if [ -n "$ALWAYS" ]; then
    printf 'KB layer (rebuilt with: python3 kb/build_kb.py):\n'
    for item in $ALWAYS; do printf '  [KB ]  %s\n' "$item"; done
else
    printf 'KB layer: (nothing present)\n'
fi
printf '\n'

if [ "$FULL" -eq 1 ]; then
    if [ -n "$RAW" ]; then
        printf 'RAW output (--full, requires vision model to regenerate, ~90 s/image):\n'
        for item in $RAW; do printf '  [RAW]  %s\n' "$item"; done
    else
        printf 'RAW output: (none present)\n'
    fi
else
    printf 'RAW output: SKIPPED (pass --full to also delete _annotations.jsonl / telemetry.log / _tracker.json)\n'
fi
printf '\n'

# ------------------------------------------------------------------ confirm
if [ "$DELETING" -eq 0 ]; then
     printf "%s\n" "-> Dry run: nothing removed. Re-run with --apply (with a prompt) or --yes (no prompt) to delete$( [ "$FULL" -eq 1 ] && printf ' (incl. --full)' )."
    exit 0
fi

if [ "$ASSUME_YES" -eq 0 ]; then
     read -r -p "Proceed and delete these paths? [y/N] " answer || answer=""
    if [ "$answer" != "y" ] && [ "$answer" != "Y" ]; then
         printf 'Aborted — nothing deleted.\n'
        exit 0
    fi
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

if [ "$FULL" -eq 1 ]; then
    for item in $RAW; do
        if [ -e "$item" ]; then
            rm -f -- "$item"
            deleted=$((deleted + 1))
            printf 'rm -f  %s\n' "$item"
        fi
    done
fi

printf '\nDone. Removed %s path(s).\n' "$deleted"
printf 'Rebuild the KB layer:  python3 kb/build_kb.py\n'
if [ "$FULL" -eq 1 ]; then
    printf 'Rebuild raw output:  python3 classify_images.py --count N\n'
fi
