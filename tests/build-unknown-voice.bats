#!/usr/bin/env bats
#
# Tests for scripts/build-unknown-voice.sh
#
# Strategy: stub sox + ffmpeg (fixtures/) onto $PATH so the real binaries are
# never needed. Stubs log their argv to $STUB_LOG; we assert on that log to
# verify the pipeline shape (layers, desync, mix, character) and the numeric
# params derived from --dread.
#
# Requires: bats-core. Install: sudo apt install bats  (or: npm i -g bats)

setup() {
    SCRIPT="${BATS_TEST_DIRNAME}/../scripts/build-unknown-voice.sh"
    FIXTURES="${BATS_TEST_DIRNAME}/fixtures"

    # isolated workspace per test
    WORK="$(mktemp -d)"
    cd "$WORK"

    # stubs win the PATH race
    cp "$FIXTURES/sox" "$FIXTURES/ffmpeg" "$WORK/"
    chmod +x "$WORK/sox" "$WORK/ffmpeg"
    export PATH="$WORK:$PATH"

    export STUB_LOG="$WORK/stub.log"
    : > "$STUB_LOG"

    # fake inputs (content irrelevant; script only checks existence + hands to ffmpeg)
    printf 'fake-mp3' > male.mp3
    printf 'fake-mp3' > fem.mp3
}

teardown() {
    cd /
    rm -rf "$WORK"
}

run_build() {
    run bash "$SCRIPT" --sox "$WORK/sox" --ffmpeg "$WORK/ffmpeg" "$@"
}

# ------------------------------------------------------------------ arg parsing

@test "fails without --male" {
    run bash "$SCRIPT" -f fem.mp3
    [ "$status" -ne 0 ]
    [[ "$output" == *"missing -m"* ]]
}

@test "fails without --female" {
    run bash "$SCRIPT" -m male.mp3
    [ "$status" -ne 0 ]
    [[ "$output" == *"missing -f"* ]]
}

@test "fails when male file does not exist" {
    run bash "$SCRIPT" -m nope.mp3 -f fem.mp3
    [ "$status" -ne 0 ]
    [[ "$output" == *"input file missing: nope.mp3"* ]]
}

@test "rejects unknown flag" {
    run bash "$SCRIPT" -m male.mp3 -f fem.mp3 --bogus
    [ "$status" -ne 0 ]
    [[ "$output" == *"unknown arg: --bogus"* ]]
}

@test "--help exits 0 and prints usage" {
    run bash "$SCRIPT" --help
    [ "$status" -eq 0 ]
    [[ "$output" == *"Usage: build-unknown-voice.sh"* ]]
}

# ------------------------------------------------------------------ dread bounds

@test "rejects dread below range" {
    run_build -m male.mp3 -f fem.mp3 -d 0.05
    [ "$status" -ne 0 ]
    [[ "$output" == *"dread out of range"* ]]
}

@test "rejects dread above range" {
    run_build -m male.mp3 -f fem.mp3 -d 3.5
    [ "$status" -ne 0 ]
    [[ "$output" == *"dread out of range"* ]]
}

@test "accepts dread at boundary 3.0" {
    run_build -m male.mp3 -f fem.mp3 -d 3.0
    [ "$status" -eq 0 ]
}

# ------------------------------------------------------------------ happy path

@test "default run succeeds and produces output" {
    run_build -m male.mp3 -f fem.mp3
    [ "$status" -eq 0 ]
    [ -f unknown_chorus.wav ]
    [[ "$output" == *"done: unknown_chorus.wav"* ]]
}

@test "custom output name is honored" {
    run_build -m male.mp3 -f fem.mp3 -o custom.wav
    [ "$status" -eq 0 ]
    [ -f custom.wav ]
}

# ------------------------------------------------------------------ pipeline shape

@test "normalizes both inputs via ffmpeg to mono 44100" {
    run_build -m male.mp3 -f fem.mp3
    grep -q "ffmpeg .*-i male.mp3 -ac 1 -ar 44100" "$STUB_LOG"
    grep -q "ffmpeg .*-i fem.mp3 -ac 1 -ar 44100" "$STUB_LOG"
}

@test "builds all four layers" {
    run_build -m male.mp3 -f fem.mp3
    grep -q "L_low.wav"   "$STUB_LOG"
    grep -q "L_mid.wav"   "$STUB_LOG"
    grep -q "L_high.wav"  "$STUB_LOG"
    grep -q "L_ghost.wav" "$STUB_LOG"
}

@test "mix uses -m (mix/sum) NOT -M (merge/multichannel)" {
    run_build -m male.mp3 -f fem.mp3
    # the panned layers are summed on the mix line; assert it carries a bare -m
    grep "L_low_p.wav" "$STUB_LOG" | grep -q -- "-m "
    # -M (merge) must appear nowhere: gain-based panning replaced the old Haas merge
    ! grep -q -- "-M " "$STUB_LOG"
}

@test "ghost layer drags female DOWN (speed < 1)" {
    run_build -m male.mp3 -f fem.mp3 -d 1.0
    # ghost speed = 1 - 0.28 = 0.720
    grep "L_ghost.wav" "$STUB_LOG" | grep -q "speed 0.720"
}

@test "low layer formant shift present (speed 0.820 at dread 1)" {
    run_build -m male.mp3 -f fem.mp3 -d 1.0
    grep "L_low.wav" "$STUB_LOG" | grep -q "speed 0.820"
}

@test "dread scales the drive parameter" {
    run_build -m male.mp3 -f fem.mp3 -d 2.0
    # drive = 8 * 2.0 = 16.000 on the low layer overdrive
    grep "L_low.wav" "$STUB_LOG" | grep -q "overdrive 16.000"
}

@test "character stage applies bandpass" {
    run_build -m male.mp3 -f fem.mp3
    grep "char.wav" "$STUB_LOG" | grep -q "bandpass 1200"
}

# ------------------------------------------------------------------ knobs

@test "stereo width pans layers via gain-based remix" {
    run_build -m male.mp3 -f fem.mp3 --wide 0.8
    grep "L_low_p.wav" "$STUB_LOG" | grep -q -- "remix 1v"
}

@test "wide 0 collapses dual-mono back to true mono (remix 1-2)" {
    run_build -m male.mp3 -f fem.mp3 --wide 0
    grep "unknown_chorus.wav" "$STUB_LOG" | grep -q -- "remix 1-2"
}

@test "warble injects the slow pitch-drift chorus" {
    run_build -m male.mp3 -f fem.mp3 --warble 1.0
    grep "char.wav" "$STUB_LOG" | grep -q -- "chorus 0.7 0.9 40 0.4 0.2"
}

@test "warble 0 omits the pitch-drift chorus" {
    run_build -m male.mp3 -f fem.mp3 --warble 0
    ! grep "char.wav" "$STUB_LOG" | grep -q -- "chorus 0.7 0.9 40"
}

@test "pulse adds a gated distortion sidechain (tremolo)" {
    run_build -m male.mp3 -f fem.mp3 --pulse 1.5
    grep -q "tremolo 0.3 90" "$STUB_LOG"
}

@test "reverb is occasional: wet-only gated by a slow LFO, not a constant wash" {
    run_build -m male.mp3 -f fem.mp3 --reverb 1.0
    grep -q "reverb -w" "$STUB_LOG"
    grep -q "tremolo 0.15 90" "$STUB_LOG"
}

@test "reverb 0 leaves it fully dry (no reverb anywhere)" {
    run_build -m male.mp3 -f fem.mp3 --reverb 0 --preverb 0
    ! grep -q "reverb" "$STUB_LOG"
}

@test "crush adds an 8-bit bitcrush layer" {
    run_build -m male.mp3 -f fem.mp3 --crush 1.0
    grep -q -- "-b 8 -e unsigned-integer" "$STUB_LOG"
}

@test "crush 0 (default) adds no bitcrush layer" {
    run_build -m male.mp3 -f fem.mp3
    ! grep -q -- "-b 8" "$STUB_LOG"
}

@test "rumble derives an octave-down sub from the voice" {
    run_build -m male.mp3 -f fem.mp3 --rumble 2
    grep -q "pitch -1200" "$STUB_LOG"
}

@test "preverb adds a reverse-reverb pre-swell pass" {
    run_build -m male.mp3 -f fem.mp3 --preverb 1.5
    grep -q "reverse reverb" "$STUB_LOG"
}

@test "femboost raises the HIGH female layer level" {
    # HIGH level = -12 + femboost; femboost 4 -> -8.0
    run_build -m male.mp3 -f fem.mp3 --femboost 4
    grep "L_high.wav" "$STUB_LOG" | grep -q "gain -n -8.0"
}

# ------------------------------------------------------------------ knob bounds

@test "rejects wide above range" {
    run_build -m male.mp3 -f fem.mp3 --wide 2
    [ "$status" -ne 0 ]
    [[ "$output" == *"wide out of range"* ]]
}

@test "rejects femboost above range" {
    run_build -m male.mp3 -f fem.mp3 --femboost 20
    [ "$status" -ne 0 ]
    [[ "$output" == *"femboost out of range"* ]]
}

@test "rejects drag below range" {
    run_build -m male.mp3 -f fem.mp3 --drag 0.5
    [ "$status" -ne 0 ]
    [[ "$output" == *"drag out of range"* ]]
}

# ------------------------------------------------------------------ stems

@test "stems removed by default" {
    run_build -m male.mp3 -f fem.mp3
    [ ! -d _stems ]
}

@test "--keep-stems retains _stems dir" {
    run_build -m male.mp3 -f fem.mp3 --keep-stems
    [ -d _stems ]
    [[ "$output" == *"stems kept in"* ]]
}

# ------------------------------------------------------------------ autodetect

@test "explicit --sox override to missing binary fails clearly" {
    run bash "$SCRIPT" -m male.mp3 -f fem.mp3 --sox /nonexistent/sox --ffmpeg "$WORK/ffmpeg"
    [ "$status" -ne 0 ]
    [[ "$output" == *"sox override not found"* ]]
}

@test "autodetects sox from PATH when no override given" {
    # no --sox / --ffmpeg: relies on stubs being first in PATH
    run bash "$SCRIPT" -m male.mp3 -f fem.mp3
    [ "$status" -eq 0 ]
    [[ "$output" == *"sox    : "* ]]
}
