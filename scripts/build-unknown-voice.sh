#!/usr/bin/env bash
#
# Builds a "The Unknown" (Dead by Daylight) chorus voice from two ElevenLabs
# sources: a male and a female take of the SAME phrase.
#
# Flow: ElevenLabs (mp3/wav) -> ffmpeg (normalize) -> SoX (layers + desync
#       + pan mix + character + optional beds -> stereo).
#
# Layers:
#   LOW   (male)   - guttural bottom, formants down, overdrive
#   MID   (male)   - dry base, keeps intelligibility, slight detune
#   HIGH  (female) - cracked whisper on top, formants up, highpass
#   GHOST (female) - female dragged DOWN into male register. The key layer:
#                    turns "a duet" into "a thing wearing stolen voices".
#
# Usage:
#   ./build-unknown-voice.sh -m male.mp3 -f fem.mp3
#   ./build-unknown-voice.sh -m male.wav -f fem.wav -o out.wav -d 1.4 --wide 0.8 --keep-stems
#
set -euo pipefail

# ---------------------------------------------------------------- defaults
MALE=""
FEMALE=""
OUT="unknown_chorus.wav"
DREAD="1.0"
FEMBOOST="4"
GRIT="1.0"
DRAG="0.92"
PULSE="1.0"
WARBLE="1.0"
WIDE="0.6"
RUMBLE="1.0"
CRUSH="0.0"
REVERB="1.0"
PREVERB="0.0"
KEEP_STEMS=0
SOX_BIN="${SOX_BIN:-}"
FFMPEG_BIN="${FFMPEG_BIN:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: build-unknown-voice.sh -m MALE -f FEMALE [options]

  -m, --male FILE        male source (mp3/wav) [required]
  -f, --female FILE      female source (mp3/wav) [required]
  -o, --out FILE         output wav (default: unknown_chorus.wav)
  -d, --dread N          dread multiplier 0.1-3.0 (default: 1.0; useful 0.6-1.6)
      --femboost N       female layer boost dB 0-12 (default: 4)
      --grit N           final distortion 0-3 (default: 1.0)
      --drag N           final drawl via tempo 0.75-1.0 (default: 0.92)
      --pulse N          periodic distortion 0-3, 0=off (default: 1.0)
      --warble N         pitch-drift chorus 0-3, 0=off (default: 1.0)
      --wide N           stereo width by layer pan 0-1, 0=mono (default: 0.6)
      --rumble N         sub-bass bed 0-6, 0=off (default: 1.0)
      --crush N          bitcrush texture 0-3, 0=off (default: 0.0)
      --reverb N         occasional gated reverb 0-3, 0=dry (default: 1.0)
      --preverb N        reverse-reverb pre-swell 0-3, 0=off (default: 0.0)
      --keep-stems       keep intermediate layers in ./_stems
      --sox PATH         override sox binary
      --ffmpeg PATH      override ffmpeg binary
  -h, --help             this
EOF
}

# ---------------------------------------------------------------- arg parse
while [[ $# -gt 0 ]]; do
    case "$1" in
        -m|--male)       MALE="$2"; shift 2 ;;
        -f|--female)     FEMALE="$2"; shift 2 ;;
        -o|--out)        OUT="$2"; shift 2 ;;
        -d|--dread)      DREAD="$2"; shift 2 ;;
        --femboost)      FEMBOOST="$2"; shift 2 ;;
        --grit)          GRIT="$2"; shift 2 ;;
        --drag)          DRAG="$2"; shift 2 ;;
        --pulse)         PULSE="$2"; shift 2 ;;
        --warble)        WARBLE="$2"; shift 2 ;;
        --wide)          WIDE="$2"; shift 2 ;;
        --rumble)        RUMBLE="$2"; shift 2 ;;
        --crush)         CRUSH="$2"; shift 2 ;;
        --reverb)        REVERB="$2"; shift 2 ;;
        --preverb)       PREVERB="$2"; shift 2 ;;
        --keep-stems)    KEEP_STEMS=1; shift ;;
        --sox)           SOX_BIN="$2"; shift 2 ;;
        --ffmpeg)        FFMPEG_BIN="$2"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *)               die "unknown arg: $1 (see --help)" ;;
    esac
done

[[ -n "$MALE"   ]] || die "missing -m/--male (see --help)"
[[ -n "$FEMALE" ]] || die "missing -f/--female (see --help)"
[[ -f "$MALE"   ]] || die "input file missing: $MALE"
[[ -f "$FEMALE" ]] || die "input file missing: $FEMALE"

# range checks via awk (portable, no bc dependency)
in_range() { awk -v x="$1" -v lo="$2" -v hi="$3" 'BEGIN{exit !(x>=lo && x<=hi)}'; }
in_range "$DREAD"    0.1  3.0  || die "dread out of range 0.1-3.0: $DREAD"
in_range "$FEMBOOST" 0    12   || die "femboost out of range 0-12: $FEMBOOST"
in_range "$GRIT"     0    3    || die "grit out of range 0-3: $GRIT"
in_range "$DRAG"     0.75 1.0  || die "drag out of range 0.75-1.0: $DRAG"
in_range "$PULSE"    0    3    || die "pulse out of range 0-3: $PULSE"
in_range "$WARBLE"   0    3    || die "warble out of range 0-3: $WARBLE"
in_range "$WIDE"     0    1    || die "wide out of range 0-1: $WIDE"
in_range "$RUMBLE"   0    6    || die "rumble out of range 0-6: $RUMBLE"
in_range "$CRUSH"    0    3    || die "crush out of range 0-3: $CRUSH"
in_range "$REVERB"   0    3    || die "reverb out of range 0-3: $REVERB"
in_range "$PREVERB"  0    3    || die "preverb out of range 0-3: $PREVERB"

# ---------------------------------------------------------------- autodetect
# Search order: explicit override -> PATH -> common install locations
# (covers native Linux, WSL, and Windows sox.exe reachable from WSL/git-bash).
autodetect() {
    local name="$1"; shift
    local override="$1"; shift
    if [[ -n "$override" ]]; then
        command -v "$override" >/dev/null 2>&1 && { echo "$override"; return 0; }
        [[ -x "$override" ]] && { echo "$override"; return 0; }
        die "$name override not found: $override"
    fi
    if command -v "$name" >/dev/null 2>&1; then
        command -v "$name"; return 0
    fi
    local c
    for c in "$@"; do
        [[ -x "$c" ]] && { echo "$c"; return 0; }
    done
    return 1
}

SOX="$(autodetect sox "$SOX_BIN" \
    "/usr/bin/sox" "/usr/local/bin/sox" "/opt/homebrew/bin/sox" \
    "/c/sox-14-4-2/sox.exe" "/mnt/c/sox-14-4-2/sox.exe" \
    "sox.exe")" \
    || die "sox not found. install: sudo apt install sox libsox-fmt-all"

FFMPEG="$(autodetect ffmpeg "$FFMPEG_BIN" \
    "$SCRIPT_DIR/../ffmpeg/bin/ffmpeg.exe" \
    "/usr/bin/ffmpeg" "/usr/local/bin/ffmpeg" "/opt/homebrew/bin/ffmpeg" \
    "/c/ffmpeg/bin/ffmpeg.exe" "/mnt/c/ffmpeg/bin/ffmpeg.exe" \
    "ffmpeg.exe")" \
    || die "ffmpeg not found. install: sudo apt install ffmpeg"

echo "sox    : $SOX"
echo "ffmpeg : $FFMPEG"

# ---------------------------------------------------------------- workspace
TMP="./_stems"
mkdir -p "$TMP"
cleanup() { [[ "$KEEP_STEMS" -eq 0 ]] && rm -rf "$TMP"; }
trap cleanup EXIT

# suppress SoX "clipped" spam but keep real errors
sox_run() {
    "$SOX" "$@" 2>&1 | grep -v 'clipped' >&2 || true
    # shellcheck disable=SC2181
    [[ "${PIPESTATUS[0]}" -eq 0 ]] || die "sox failed: $*"
}

# normalize any input to 44.1kHz / 16-bit / mono wav.
to_stem() {
    local src="$1" dst="$TMP/$2.wav"
    "$FFMPEG" -y -loglevel error -i "$src" -ac 1 -ar 44100 -acodec pcm_s16le "$dst" \
        || die "ffmpeg failed on: $src"
    echo "$dst"
}

# ---------------------------------------------------------------- math helpers
gt0()     { awk -v x="$1" 'BEGIN{exit !(x>0)}'; }
# equal-power pan gains: pan -1=hard L, 0=center, +1=hard R -> "Lgain Rgain".
pangain() { awk -v p="$1" 'BEGIN{th=(p+1)*atan2(0,-1)/4; printf "%.3f %.3f\n", cos(th), sin(th)}'; }

# ---------------------------------------------------------------- params
# clamps mirror the PowerShell version: speeds pinned so tempo-restore stays sane.
LOW_SPEED=$(awk  -v d="$DREAD" 'BEGIN{v=1-0.18*d; if(v<0.6)v=0.6;   printf "%.3f", v}')
LOW_TEMPO=$(awk  -v s="$LOW_SPEED" 'BEGIN{printf "%.3f", 1/s}')
HIGH_SPEED=$(awk -v d="$DREAD" 'BEGIN{v=1+0.15*d; if(v>1.6)v=1.6;   printf "%.3f", v}')
HIGH_TEMPO=$(awk -v s="$HIGH_SPEED" 'BEGIN{printf "%.3f", 1/s}')
GHOST_SPEED=$(awk -v d="$DREAD" 'BEGIN{v=1-0.28*d; if(v<0.55)v=0.55; printf "%.3f", v}')
GHOST_TEMPO=$(awk -v s="$GHOST_SPEED" 'BEGIN{printf "%.3f", 1/s}')
DRIVE=$(awk       -v d="$DREAD" 'BEGIN{printf "%.3f", 8*d}')
GHOST_DRIVE=$(awk -v x="$DRIVE" 'BEGIN{printf "%.3f", x*0.6}')
PAD_LOW=$(awk     -v d="$DREAD" 'BEGIN{printf "%.3f", 0.06*d}')
PAD_HIGH=$(awk    -v d="$DREAD" 'BEGIN{printf "%.3f", 0.11*d}')
PAD_GHOST=$(awk   -v d="$DREAD" 'BEGIN{printf "%.3f", 0.04*d}')

HIGH_LEVEL=$(awk  -v b="$FEMBOOST" 'BEGIN{printf "%.1f", -12+b}')
GHOST_LEVEL=$(awk -v b="$FEMBOOST" 'BEGIN{printf "%.1f", -9+b*0.6}')
GRIT_DRIVE=$(awk  -v g="$GRIT" 'BEGIN{printf "%.1f", 6*g}')

BROKEN_DRIVE=$(awk -v p="$PULSE" 'BEGIN{printf "%.1f", 14*p}')
PULSE_LEVEL=$(awk  -v p="$PULSE" 'BEGIN{printf "%.1f", -14+p*4}')
PULSE_RATE="0.3"

WARBLE_DEPTH=$(awk -v w="$WARBLE" 'BEGIN{printf "%.2f", 3.5*w}')

# equal-power pan gains per layer, spread scaled by WIDE (0 = all centered = mono)
read -r GLOW_L GLOW_R   < <(pangain "$(awk -v w="$WIDE" 'BEGIN{printf "%.4f", -0.60*w}')")
read -r GMID_L GMID_R   < <(pangain "0")
read -r GHIGH_L GHIGH_R < <(pangain "$(awk -v w="$WIDE" 'BEGIN{printf "%.4f",  0.70*w}')")
read -r GGHOST_L GGHOST_R < <(pangain "$(awk -v w="$WIDE" 'BEGIN{printf "%.4f", -0.85*w}')")

RUMBLE_LEVEL=$(awk -v r="$RUMBLE" 'BEGIN{v=-20+4*r; if(v>-4)v=-4; printf "%.1f", v}')
CRUSH_RATE=$(awk   -v c="$CRUSH"  'BEGIN{v=9000-2000*c; if(v<3000)v=3000; printf "%d", v}')
CRUSH_LEVEL=$(awk  -v c="$CRUSH"  'BEGIN{printf "%.1f", -18+4*c}')
PREVERB_AMT=$(awk  -v p="$PREVERB" 'BEGIN{v=40+15*p; if(v>90)v=90; printf "%d", v}')
VERB_RATE="0.15"
VERB_LEVEL=$(awk   -v r="$REVERB" 'BEGIN{v=-12+4*r; if(v>-2)v=-2; printf "%.1f", v}')

# ---------------------------------------------------------------- 0. input
echo "[1/5] normalizing input..."
MALE_W="$(to_stem "$MALE" src_male)"
FEMALE_W="$(to_stem "$FEMALE" src_female)"

L_LOW="$TMP/L_low.wav"
L_MID="$TMP/L_mid.wav"
L_HIGH="$TMP/L_high.wav"
L_GHOST="$TMP/L_ghost.wav"
MIX="$TMP/mix.wav"

# ---------------------------------------------------------------- 1. layers
echo "[2/5] building layers (dread=$DREAD)..."

# LOW - male, guttural. speed+tempo shift FORMANTS (pitch cannot do that).
sox_run "$MALE_W" "$L_LOW" gain -6 \
    speed "$LOW_SPEED" rate 44100 tempo "$LOW_TEMPO" \
    overdrive "$DRIVE" 20 \
    gain -n -3

# MID - male, dry base. keeps words readable so mix is not pure mush.
sox_run "$MALE_W" "$L_MID" gain -6 \
    pitch -40 \
    gain -n -6

# HIGH - female, cracked whisper on top.
sox_run "$FEMALE_W" "$L_HIGH" gain -6 \
    speed "$HIGH_SPEED" rate 44100 tempo "$HIGH_TEMPO" \
    highpass 800 \
    gain -n "$HIGH_LEVEL"

# GHOST - female dragged down. ear hears male register, formants are female,
# brain cannot resolve it into a person. this is the whole trick.
sox_run "$FEMALE_W" "$L_GHOST" gain -6 \
    speed "$GHOST_SPEED" rate 44100 tempo "$GHOST_TEMPO" \
    overdrive "$GHOST_DRIVE" 15 \
    gain -n "$GHOST_LEVEL"

# ---------------------------------------------------------------- 2. desync
echo "[3/5] desyncing layers..."
L_LOW_D="$TMP/L_low_d.wav"
L_HIGH_D="$TMP/L_high_d.wav"
L_GHOST_D="$TMP/L_ghost_d.wav"

sox_run "$L_LOW"   "$L_LOW_D"   pad "$PAD_LOW"
sox_run "$L_HIGH"  "$L_HIGH_D"  pad "$PAD_HIGH"
sox_run "$L_GHOST" "$L_GHOST_D" pad "$PAD_GHOST"

# ---------------------------------------------------------------- 3. mix (stereo)
echo "[4/5] mixing chorus (stereo spread)..."
# Pan each layer to stereo (gain-based remix = mono-compatible, no Haas comb),
# then sum. GHOST+LOW lean left, HIGH leans right, MID centered.
L_LOW_P="$TMP/L_low_p.wav"
L_MID_P="$TMP/L_mid_p.wav"
L_HIGH_P="$TMP/L_high_p.wav"
L_GHOST_P="$TMP/L_ghost_p.wav"
sox_run "$L_LOW_D"   "$L_LOW_P"   remix "1v${GLOW_L}"   "1v${GLOW_R}"
sox_run "$L_MID"     "$L_MID_P"   remix "1v${GMID_L}"   "1v${GMID_R}"
sox_run "$L_HIGH_D"  "$L_HIGH_P"  remix "1v${GHIGH_L}"  "1v${GHIGH_R}"
sox_run "$L_GHOST_D" "$L_GHOST_P" remix "1v${GGHOST_L}" "1v${GGHOST_R}"
sox_run -m "$L_LOW_P" "$L_MID_P" "$L_HIGH_P" "$L_GHOST_P" "$MIX" gain -n -3

# ---------------------------------------------------------------- 4. character
echo "[5/5] applying character..."
# Built dynamically so --warble can inject a slow deep chorus = pitch drift.
# tempo $DRAG drawls delivery without re-pitching. Two -t chorus voices thicken.
CHAR="$TMP/char.wav"
CHAR_ARGS=( "$MIX" "$CHAR" tempo "$DRAG" )
if gt0 "$WARBLE"; then
    CHAR_ARGS+=( chorus 0.7 0.9 40 0.4 0.2 "$WARBLE_DEPTH" -s )
fi
CHAR_ARGS+=( chorus 0.6 0.85 55 0.4 0.25 2 -t 60 0.32 0.4 2.3 -t \
             overdrive "$GRIT_DRIVE" 30 \
             bandpass 1200 3q \
             equalizer 250 1q +5 \
             equalizer 3500 2q -4 \
             gain -n -3 )
sox_run "${CHAR_ARGS[@]}"

# fold in the periodic-distortion sidechain (stereo) -> premaster
PREMASTER="$TMP/premaster.wav"
if gt0 "$PULSE"; then
    L_BROKEN="$TMP/L_broken.wav"
    sox_run "$MIX" "$L_BROKEN" \
        tempo "$DRAG" overdrive "$BROKEN_DRIVE" 40 highpass 350 \
        tremolo "$PULSE_RATE" 90 gain -n "$PULSE_LEVEL"
    sox_run -m "$CHAR" "$L_BROKEN" "$PREMASTER" gain -n -2
else
    cp "$CHAR" "$PREMASTER"
fi

MONO="$PREMASTER"

# sub-bass rumble bed derived from the voice (pulses with speech, does not drone)
if gt0 "$RUMBLE"; then
    L_RUMBLE="$TMP/L_rumble.wav"
    sox_run "$MONO" "$L_RUMBLE" \
        lowpass 110 pitch -1200 lowpass 80 overdrive 6 20 gain -n "$RUMBLE_LEVEL"
    RMIX="$TMP/rmix.wav"
    sox_run -m "$MONO" "$L_RUMBLE" "$RMIX" gain -n -2
    MONO="$RMIX"
fi

# bitcrush "broken transmission": 8-bit quantize + samplerate decimation, low blend
if gt0 "$CRUSH"; then
    L_CRUSH="$TMP/L_crush.wav"
    sox_run "$MONO" -b 8 -e unsigned-integer "$L_CRUSH" \
        rate "$CRUSH_RATE" rate 44100 overdrive 5 20 highpass 250 gain -n "$CRUSH_LEVEL"
    CMIX="$TMP/cmix.wav"
    sox_run -m "$MONO" "$L_CRUSH" "$CMIX" gain -n -2
    MONO="$CMIX"
fi

# occasional room: wet-only reverb gated by a slow LFO (swells in and out)
if gt0 "$REVERB"; then
    L_VERB="$TMP/L_verb.wav"
    sox_run "$MONO" "$L_VERB" \
        reverb -w 75 50 100 100 20 0 tremolo "$VERB_RATE" 90 gain -n "$VERB_LEVEL"
    VMIX="$TMP/vmix.wav"
    sox_run -m "$MONO" "$L_VERB" "$VMIX" gain -n -2
    MONO="$VMIX"
fi

# final: stereo image already built by panning. WIDE=0 -> collapse to true mono.
if gt0 "$WIDE"; then
    sox_run "$MONO" "$OUT" gain -n -1
else
    sox_run "$MONO" "$OUT" remix 1-2 gain -n -1
fi

# reverse-reverb pre-swell (post-process): reverse -> reverb -> reverse flips the
# tail so it LEADS each onset. default reverb keeps dry, adds only the swell.
if gt0 "$PREVERB"; then
    echo "[+] reverse-reverb pre-swell..."
    PRE_OUT="$TMP/preverb.wav"
    sox_run "$OUT" "$PRE_OUT" \
        reverse reverb "$PREVERB_AMT" 50 100 100 0 0 reverse gain -n -1
    mv -f "$PRE_OUT" "$OUT"
fi

echo ""
echo "done: $OUT"
if [[ "$KEEP_STEMS" -eq 1 ]]; then
    echo "stems kept in: $TMP"
fi
