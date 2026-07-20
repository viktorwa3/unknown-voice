<#
.SYNOPSIS
    Renders five style presets of the Unknown voice side by side into versions/.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\Test-Styles.ps1
    powershell -ExecutionPolicy Bypass -File .\scripts\Test-Styles.ps1 -MaleFile male.mp3 -FemaleFile fem.mp3
.NOTES
    ASCII-only, same PS 5.1 constraint as Build-UnknownVoice.ps1.
#>
[CmdletBinding()]
param(
    [string]$MaleFile,
    [string]$FemaleFile
)

$root  = Split-Path -Parent $PSScriptRoot          # repo root (scripts/ is one level down)
$build = Join-Path $PSScriptRoot "Build-UnknownVoice.ps1"
if (-not $MaleFile)   { $MaleFile   = Join-Path $root "male.mp3" }
if (-not $FemaleFile) { $FemaleFile = Join-Path $root "fem.mp3" }

$outDir = Join-Path $root "versions"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$common = @{ MaleFile = $MaleFile; FemaleFile = $FemaleFile }

# Each hashtable = one preset. Tweak in place, re-run.
$styles = @(
    # Signature: balanced reference, close to defaults.
    @{ Name='01_signature';   Dread=1.0; FemBoost=4; Grit=1.0; Drag=0.92; Pulse=1.0; Warble=1.0; Wide=0.60; Rumble=1.2; Crush=0.0; Reverb=1.0; Preverb=0.0 }

    # Buried: deep, slow, huge sub, mostly dry. Guttural thing from underground.
    @{ Name='02_buried';      Dread=1.5; FemBoost=2; Grit=1.4; Drag=0.80; Pulse=0.8; Warble=0.5; Wide=0.50; Rumble=4.0; Crush=0.0; Reverb=0.6; Preverb=0.0 }

    # Swarm: chorus-forward, wide, warbly, female up front. Many voices around you.
    @{ Name='03_swarm';       Dread=1.1; FemBoost=7; Grit=0.9; Drag=0.90; Pulse=0.7; Warble=2.2; Wide=0.95; Rumble=1.0; Crush=0.0; Reverb=1.2; Preverb=0.0 }

    # Broken: glitchy, corrupted signal. Bitcrush + heavy pulse + grit.
    @{ Name='04_broken';      Dread=1.2; FemBoost=4; Grit=2.2; Drag=0.95; Pulse=2.0; Warble=1.0; Wide=0.70; Rumble=1.0; Crush=1.8; Reverb=0.5; Preverb=0.0 }

    # Approaching: cinematic, spacious, pre-swell. The "about to speak" version.
    @{ Name='05_approaching'; Dread=1.3; FemBoost=5; Grit=1.0; Drag=0.82; Pulse=0.8; Warble=1.2; Wide=0.85; Rumble=2.0; Crush=0.0; Reverb=1.6; Preverb=1.5 }
)

foreach ($s in $styles) {
    $name = $s.Name
    $p = $s.Clone(); $p.Remove('Name')
    $p.OutFile = Join-Path $outDir "$name.wav"
    Write-Host "`n=== $name.wav ===" -ForegroundColor Yellow
    & $build @common @p
}

Write-Host "`nAll five rendered to $outDir. Compare 01-05 and pick a base to fine-tune." -ForegroundColor Green
