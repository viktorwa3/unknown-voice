#!/usr/bin/env bash
#
# Renders five style presets of the Unknown voice side by side for comparison.
# Mirror of Test-Styles.ps1.
#
# Usage:
#   ./scripts/test-styles.sh
#   ./scripts/test-styles.sh -m male.mp3 -f fem.mp3
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD="$SCRIPT_DIR/build-unknown-voice.sh"

MALE="$ROOT/male.mp3"
FEMALE="$ROOT/fem.mp3"
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--male)   MALE="$2"; shift 2 ;;
        -f|--female) FEMALE="$2"; shift 2 ;;
        *)           echo "unknown arg: $1" >&2; exit 1 ;;
    esac
done

OUTDIR="$ROOT/versions"
mkdir -p "$OUTDIR"

# Each row: name  dread femboost grit drag pulse warble wide rumble crush reverb preverb
# Signature : balanced reference, close to defaults.
# Buried    : deep, slow, huge sub, mostly dry.
# Swarm     : chorus-forward, wide, warbly, female up front.
# Broken    : glitchy, corrupted signal.
# Approaching: cinematic, spacious, pre-swell.
STYLES=(
    "01_signature   1.0 4 1.0 0.92 1.0 1.0 0.60 1.2 0.0 1.0 0.0"
    "02_buried      1.5 2 1.4 0.80 0.8 0.5 0.50 4.0 0.0 0.6 0.0"
    "03_swarm       1.1 7 0.9 0.90 0.7 2.2 0.95 1.0 0.0 1.2 0.0"
    "04_broken      1.2 4 2.2 0.95 2.0 1.0 0.70 1.0 1.8 0.5 0.0"
    "05_approaching 1.3 5 1.0 0.82 0.8 1.2 0.85 2.0 0.0 1.6 1.5"
)

for row in "${STYLES[@]}"; do
    read -r name d fb gr dr pu wa wi ru cr rv pv <<<"$row"
    echo ""
    echo "=== ${name}.wav ==="
    bash "$BUILD" -m "$MALE" -f "$FEMALE" -o "$OUTDIR/${name}.wav" \
        -d "$d" --femboost "$fb" --grit "$gr" --drag "$dr" --pulse "$pu" \
        --warble "$wa" --wide "$wi" --rumble "$ru" --crush "$cr" \
        --reverb "$rv" --preverb "$pv"
done

echo ""
echo "All five rendered to $OUTDIR. Compare 01-05 and pick a base to fine-tune."
