<#
.SYNOPSIS
    Builds a "The Unknown" (Dead by Daylight) chorus voice from two ElevenLabs
    sources: a male and a female take of the SAME phrase.

.DESCRIPTION
    Flow: ElevenLabs (mp3/wav) -> ffmpeg (normalize to wav) -> SoX (layers + desync + character).

    Layers:
      LOW   (male)   - guttural bottom, formants shifted down, overdrive
      MID   (male)   - dry base, keeps intelligibility, slight detune
      HIGH  (female) - cracked whisper on top, formants shifted up, highpass
      GHOST (female) - female dragged DOWN into male register.
                       This is the key layer: it turns "a duet" into "a thing
                       wearing stolen voices".

.EXAMPLE
    .\Build-UnknownVoice.ps1 -MaleFile male.mp3 -FemaleFile fem.mp3

.EXAMPLE
    .\Build-UnknownVoice.ps1 -MaleFile male.wav -FemaleFile fem.wav -Dread 1.4 -KeepStems

.NOTES
    ASCII-only on purpose. Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI,
    so any Cyrillic in here would break the parser.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$MaleFile,

    [Parameter(Mandatory = $true)]
    [string]$FemaleFile,

    [string]$OutFile = "unknown_chorus.wav",

    # Global dread multiplier: 0.5 = subtle, 1.0 = base, 2.0 = cringe
    [ValidateRange(0.1, 3.0)]
    [double]$Dread = 1.0,

    # Extra dB pushed onto the female layers (HIGH whisper + GHOST). 0 = old mix.
    [ValidateRange(0, 12)]
    [double]$FemBoost = 4,

    # Distortion baked into the final chorus/character stage. 1.0 = base, 0 = clean.
    [ValidateRange(0, 3)]
    [double]$Grit = 1.0,

    # Global drawl on the final via tempo (keeps pitch/formants). 1.0 = off.
    [ValidateRange(0.75, 1.0)]
    [double]$Drag = 0.92,

    # Periodic distortion: a hard-overdriven sidechain gated by a slow LFO,
    # mixed under the voice so it "breaks up" every few seconds. 0 = off.
    [ValidateRange(0, 3)]
    [double]$Pulse = 1.0,

    # Pitch warble: slow deep chorus in the character stage = "can't hold a note". 0 = off.
    [ValidateRange(0, 3)]
    [double]$Warble = 1.0,

    # Stereo width: pans the layers across L/R at mix time (gain-based, mono-safe).
    # GHOST+LOW lean left, HIGH right, MID center. 0 = mono out.
    [ValidateRange(0, 1)]
    [double]$Wide = 0.6,

    # Sub-bass rumble bed: octave-down sub derived from the voice. 0 = off.
    [ValidateRange(0, 6)]
    [double]$Rumble = 1.0,

    # Bitcrush texture: 8-bit + samplerate-decimated parallel copy blended low. 0 = off.
    [ValidateRange(0, 3)]
    [double]$Crush = 0.0,

    # Reverse-reverb pre-swell: a ghost of each phrase swells in before it speaks.
    # Delays the onset by the swell length. 0 = off.
    [ValidateRange(0, 3)]
    [double]$Preverb = 0.0,

    # Occasional reverb: wet room gated by a slow LFO so the space swells in and
    # out instead of washing constantly. 0 = fully dry.
    [ValidateRange(0, 3)]
    [double]$Reverb = 1.0,

    # Keep intermediate layers in _stems for manual inspection
    [switch]$KeepStems,

    [string]$SoxPath    = "C:\sox-14-4-2\sox.exe",
    [string]$FfmpegPath = "ffmpeg"
)

$ErrorActionPreference = "Stop"

# Force '.' decimal separator so SoX never receives '0,82' on nb-NO etc.
# PS interpolation of [double] uses the current culture; SoX only parses '.'.
[System.Threading.Thread]::CurrentThread.CurrentCulture =
    [System.Globalization.CultureInfo]::InvariantCulture

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---------------------------------------------------------------- checks
# ffmpeg: prefer the copy bundled next to this script, then PATH.
if (-not $PSBoundParameters.ContainsKey('FfmpegPath')) {
    $bundled = Join-Path $scriptDir 'ffmpeg\bin\ffmpeg.exe'
    $FfmpegPath =
        if (Test-Path $bundled)                         { $bundled }
        elseif (Get-Command ffmpeg -ErrorAction Ignore) { 'ffmpeg' }
        else { throw 'ffmpeg not found (no bundled copy, not on PATH)' }
}

# SoX: hardcoded path, then PATH fallback.
if (-not (Test-Path $SoxPath)) {
    $onPath = Get-Command sox -ErrorAction Ignore
    if ($onPath) { $SoxPath = $onPath.Source } else { throw "SoX not found: $SoxPath" }
}

foreach ($f in @($MaleFile, $FemaleFile)) {
    if (-not (Test-Path $f)) { throw "Input file missing: $f" }
}

$tmp = Join-Path (Get-Location) "_stems"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

function Invoke-Sox {
    param([string[]]$SoxArgs)
    # Native stderr under $ErrorActionPreference='Stop' can throw NativeCommandError
    # in PS 5.1 before the exit-code check runs. Relax locally, trust $LASTEXITCODE.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    & $SoxPath @SoxArgs 2>&1 | Where-Object { $_ -notmatch 'clipped' } | Write-Verbose
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0) { throw "SoX failed: $($SoxArgs -join ' ')" }
}

# Equal-power pan gains for a mono layer. Pan -1 = hard L, 0 = center, +1 = hard R.
# Returns @(Lgain, Rgain); gain-based panning stays mono-compatible on downmix.
function Get-PanGain {
    param([double]$Pan)
    $theta = ($Pan + 1.0) * [math]::PI / 4.0
    return @([math]::Round([math]::Cos($theta), 3), [math]::Round([math]::Sin($theta), 3))
}

# ------------------------------------------------- 0. normalize input
# Everything goes through ffmpeg to 44.1kHz / 16-bit / mono.
# SoX on Windows cannot read mp3, and mono kills stereo phase mush on -m.
function ConvertTo-Stem {
    param([string]$Src, [string]$Name)
    $dst = Join-Path $tmp "$Name.wav"
    & $FfmpegPath -y -loglevel error -i $Src -ac 1 -ar 44100 -acodec pcm_s16le $dst
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg failed on: $Src" }
    return $dst
}

Write-Host "[1/5] Normalizing input..." -ForegroundColor Cyan
$male   = ConvertTo-Stem -Src $MaleFile   -Name "src_male"
$female = ConvertTo-Stem -Src $FemaleFile -Name "src_female"

# ------------------------------------------------- params from $Dread
# Speeds are clamped: past ~0.6..1.6 the tempo-restore ratio explodes and the
# formant shift degrades into aliasing artifacts instead of dread.
$lowSpeed   = [math]::Max(0.6,  [math]::Round(1 - (0.18 * $Dread), 3))  # 0.82 @ Dread=1
$lowTempo   = [math]::Round(1 / $lowSpeed, 3)
$highSpeed  = [math]::Min(1.6,  [math]::Round(1 + (0.15 * $Dread), 3))  # 1.15 @ Dread=1
$highTempo  = [math]::Round(1 / $highSpeed, 3)
$ghostSpeed = [math]::Max(0.55, [math]::Round(1 - (0.28 * $Dread), 3))  # 0.72 @ Dread=1
$ghostTempo = [math]::Round(1 / $ghostSpeed, 3)
$drive      = [math]::Round(8 * $Dread, 1)
$ghostDrive = [math]::Round($drive * 0.6, 1)
$padLow     = [math]::Round(0.06 * $Dread, 3)
$padHigh    = [math]::Round(0.11 * $Dread, 3)
$padGhost   = [math]::Round(0.04 * $Dread, 3)

# --- fem highlight + chorus grit ---
# gain -n -X: less-negative = louder. HIGH is the recognizably-female whisper,
# so it gets the full boost; GHOST reads more male, so only a partial lift.
$highLevel  = [math]::Round(-12 + $FemBoost, 1)          # -8.0 @ FemBoost=4
$ghostLevel = [math]::Round(-9  + ($FemBoost * 0.6), 1)  # -6.6 @ FemBoost=4
$gritDrive  = [math]::Round(6 * $Grit, 1)                # 6.0  @ Grit=1

# periodic-distortion sidechain
$brokenDrive = [math]::Round(14 * $Pulse, 1)             # heavy grit on the sidechain
$pulseLevel  = [math]::Round(-14 + ($Pulse * 4), 1)      # -10 @ Pulse=1, louder as it rises
$pulseRate   = 0.3                                        # LFO Hz -> one swell every ~3.3s

# warble + stereo width
$warbleDepth = [math]::Round(3.5 * $Warble, 2)           # chorus depth ms -> pitch drift
# equal-power pan gains per layer, spread scaled by $Wide (0 = all centered = mono)
$gLow   = Get-PanGain (-0.60 * $Wide)
$gMid   = Get-PanGain ( 0.00)
$gHigh  = Get-PanGain ( 0.70 * $Wide)
$gGhost = Get-PanGain (-0.85 * $Wide)

# sub-bass rumble + bitcrush
$rumbleLevel = [math]::Round([math]::Min(-4, -20 + (4 * $Rumble)), 1)  # -16 @1, caps at -4 (Rumble>=4) so the sub never clips or buries the voice
$crushRate   = [int]([math]::Max(3000, 9000 - (2000 * $Crush)))  # 7000 @1, 3000 @3 (lower = grungier)
$crushLevel  = [math]::Round(-18 + (4 * $Crush), 1)      # -14 @ Crush=1
$preverbAmt  = [int]([math]::Min(90, 40 + (15 * $Preverb)))  # reverberance 55 @1 .. 85 @3 (longer = bigger swell)
$verbRate    = 0.15                                       # LFO Hz -> room swells in ~every 6-7s
$verbLevel   = [math]::Round([math]::Min(-2, -12 + (4 * $Reverb)), 1)  # -8 @1, caps at -2

$L_low   = Join-Path $tmp "L_low.wav"
$L_mid   = Join-Path $tmp "L_mid.wav"
$L_high  = Join-Path $tmp "L_high.wav"
$L_ghost = Join-Path $tmp "L_ghost.wav"
$mix     = Join-Path $tmp "mix.wav"

# ------------------------------------------------- 1. layers
Write-Host "[2/5] Building layers (Dread=$Dread)..." -ForegroundColor Cyan

# LOW - male, guttural. speed+tempo shift FORMANTS (pitch cannot do that).
Invoke-Sox @($male, $L_low, "gain","-6",
    "speed","$lowSpeed", "rate","44100", "tempo","$lowTempo",
    "overdrive","$drive","20",
    "gain","-n","-3")

# MID - male, dry base. Keeps words readable so the mix is not pure mush.
Invoke-Sox @($male, $L_mid, "gain","-6",
    "pitch","-40",
    "gain","-n","-6")

# HIGH - female, cracked whisper on top.
Invoke-Sox @($female, $L_high, "gain","-6",
    "speed","$highSpeed", "rate","44100", "tempo","$highTempo",
    "highpass","800",
    "gain","-n","$highLevel")

# GHOST - female dragged down. Ear hears a male register but the formants are
# female, so the brain cannot resolve it into a person. This is the whole trick.
Invoke-Sox @($female, $L_ghost, "gain","-6",
    "speed","$ghostSpeed", "rate","44100", "tempo","$ghostTempo",
    "overdrive","$ghostDrive","15",
    "gain","-n","$ghostLevel")

# ------------------------------------------------- 2. desync
Write-Host "[3/5] Desyncing layers..." -ForegroundColor Cyan
$L_low_d   = Join-Path $tmp "L_low_d.wav"
$L_high_d  = Join-Path $tmp "L_high_d.wav"
$L_ghost_d = Join-Path $tmp "L_ghost_d.wav"

Invoke-Sox @($L_low,   $L_low_d,   "pad","$padLow")
Invoke-Sox @($L_high,  $L_high_d,  "pad","$padHigh")
Invoke-Sox @($L_ghost, $L_ghost_d, "pad","$padGhost")

# ------------------------------------------------- 3. mix (stereo spread)
Write-Host "[4/5] Mixing chorus (stereo spread)..." -ForegroundColor Cyan
# Pan each layer to stereo (gain-based remix = mono-compatible, no Haas comb),
# then sum. GHOST+LOW lean left, HIGH leans right, MID centered.
$L_low_p   = Join-Path $tmp "L_low_p.wav"
$L_mid_p   = Join-Path $tmp "L_mid_p.wav"
$L_high_p  = Join-Path $tmp "L_high_p.wav"
$L_ghost_p = Join-Path $tmp "L_ghost_p.wav"
Invoke-Sox @($L_low_d,   $L_low_p,   "remix", "1v$($gLow[0])",   "1v$($gLow[1])")
Invoke-Sox @($L_mid,     $L_mid_p,   "remix", "1v$($gMid[0])",   "1v$($gMid[1])")
Invoke-Sox @($L_high_d,  $L_high_p,  "remix", "1v$($gHigh[0])",  "1v$($gHigh[1])")
Invoke-Sox @($L_ghost_d, $L_ghost_p, "remix", "1v$($gGhost[0])", "1v$($gGhost[1])")
Invoke-Sox @("-m", $L_low_p, $L_mid_p, $L_high_p, $L_ghost_p, $mix, "gain","-n","-3")

# ------------------------------------------------- 4. character
Write-Host "[5/5] Applying character..." -ForegroundColor Cyan
# Character stage (mono). Built dynamically so -Warble can inject a slow, deep
# single-voice chorus = pitch drift ("can't hold a note"). tempo $Drag drawls
# delivery without re-pitching. Two -t chorus voices = thicker "several throats";
# gain-in/out pulled back to leave headroom so overdrive grits, not clips to noise.
$char = Join-Path $tmp "char.wav"
$charArgs = @($mix, $char, "tempo","$Drag")
if ($Warble -gt 0) {
    $charArgs += @("chorus","0.7","0.9","40","0.4","0.2","$warbleDepth","-s")
}
$charArgs += @(
    "chorus","0.6","0.85","55","0.4","0.25","2","-t","60","0.32","0.4","2.3","-t",
    "overdrive","$gritDrive","30",
    "bandpass","1200","3q",
    "equalizer","250","1q","+5",
    "equalizer","3500","2q","-4",
    "gain","-n","-3")
Invoke-Sox $charArgs

# Fold in the periodic-distortion sidechain (still mono) -> premaster.
$premaster = Join-Path $tmp "premaster.wav"
if ($Pulse -gt 0) {
    # Hard-overdriven copy of the mix, gated by a slow deep tremolo so the
    # distortion swells in and out, then tucked under the clean voice.
    $L_broken = Join-Path $tmp "L_broken.wav"
    Invoke-Sox @($mix, $L_broken,
        "tempo","$Drag",
        "overdrive","$brokenDrive","40",
        "highpass","350",
        "tremolo","$pulseRate","90",
        "gain","-n","$pulseLevel")
    Invoke-Sox @("-m", $char, $L_broken, $premaster, "gain","-n","-2")
} else {
    Copy-Item $char $premaster -Force
}

# --- sub-bass + bitcrush beds (mono, folded in before the stereo spread) ---
$mono = $premaster

if ($Rumble -gt 0) {
    # Octave-down sub derived from the voice itself, so it pulses with the speech
    # instead of droning. Overdrive adds harmonics so it survives small speakers.
    $L_rumble = Join-Path $tmp "L_rumble.wav"
    Invoke-Sox @($mono, $L_rumble,
        "lowpass","110",
        "pitch","-1200",
        "lowpass","80",
        "overdrive","6","20",
        "gain","-n","$rumbleLevel")
    $rmix = Join-Path $tmp "rmix.wav"
    Invoke-Sox @("-m", $mono, $L_rumble, $rmix, "gain","-n","-2")
    $mono = $rmix
}

if ($Crush -gt 0) {
    # Corrupted-transmission texture on a parallel copy, blended low. -b 8 quantizes
    # on write (bitcrush); rate NNNN -> 44100 decimates bandwidth (aliasing grunge).
    $L_crush = Join-Path $tmp "L_crush.wav"
    Invoke-Sox @($mono, "-b","8","-e","unsigned-integer", $L_crush,
        "rate","$crushRate",
        "rate","44100",
        "overdrive","5","20",
        "highpass","250",
        "gain","-n","$crushLevel")
    $cmix = Join-Path $tmp "cmix.wav"
    Invoke-Sox @("-m", $mono, $L_crush, $cmix, "gain","-n","-2")
    $mono = $cmix
}

if ($Reverb -gt 0) {
    # Occasional room: wet-only reverb gated by a slow deep tremolo so the space
    # swells in and out instead of the constant wash the old fixed reverb gave.
    $L_verb = Join-Path $tmp "L_verb.wav"
    Invoke-Sox @($mono, $L_verb,
        "reverb","-w","75","50","100","100","20","0",
        "tremolo","$verbRate","90",
        "gain","-n","$verbLevel")
    $vmix = Join-Path $tmp "vmix.wav"
    Invoke-Sox @("-m", $mono, $L_verb, $vmix, "gain","-n","-2")
    $mono = $vmix
}

# Final. The stereo image was built at mix time by panning the layers, so the
# whole character chain has already run in stereo (reverb/chorus add extra width).
# Wide=0 leaves every layer centered; collapse that dual-mono back to true mono.
if ($Wide -gt 0) {
    Invoke-Sox @($mono, $OutFile, "gain","-n","-1")
} else {
    Invoke-Sox @($mono, $OutFile, "remix","1-2", "gain","-n","-1")
}

# Reverse-reverb pre-swell (post-process on the finished output). reverse -> reverb
# -> reverse flips each reverb tail so it LEADS the onset instead of trailing it.
# Default reverb keeps the dry voice, so this only adds the anticipatory swell.
if ($Preverb -gt 0) {
    Write-Host "[+] Reverse-reverb pre-swell..." -ForegroundColor Cyan
    $preOut = Join-Path $tmp "preverb.wav"
    Invoke-Sox @($OutFile, $preOut,
        "reverse",
        "reverb","$preverbAmt","50","100","100","0","0",
        "reverse",
        "gain","-n","-1")
    Move-Item -Force $preOut $OutFile
}

if (-not $KeepStems) { Remove-Item -Recurse -Force $tmp }

Write-Host "`nDone: $OutFile" -ForegroundColor Green
if ($KeepStems) { Write-Host "Stems kept in: $tmp" -ForegroundColor DarkGray }
