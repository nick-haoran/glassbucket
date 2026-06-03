# glassbucket

glassbucket is a replacement-slot toolkit for Unity Android rhythm games that use CRIWARE audio and Addressables chart bundles.

It takes a game APK, a target song slot, replacement chart files, and a replacement WAV, then builds a signed APK where the target slot plays the custom audio and charts.

This repository intentionally does not include APKs, extracted assets, music files, generated bundles, or signed output packages.

## Scope

The current workflow replaces an existing song slot instead of adding a new catalog entry. It keeps the original slot IDs in the game package:

- `AS_<SLOT>` for audio
- `TG_<SLOT>` for the song group
- `CH_<SLOT>_<DIFFICULTY>` for chart headers and chart data

That means the custom chart appears under an existing in-game song slot. Cover art and unrelated visual assets are not replaced by default.

## Included Tools

- `scripts/encode_cri_awb.py`: encode a 16-bit PCM WAV into encrypted HCA, package it into an AWB, and copy an existing ACB template.
- `scripts/polytone_replace.py`: replace audio and chart data inside an APK, patch Addressables catalog metadata, and optionally sign the result.
- `scripts/download_uber_apk_signer.ps1`: download `uber-apk-signer` for APK signing.
- `scripts/convert_awb.py`: helper for CRI AWB/HCA extraction and decoding experiments.
- `scripts/compare_audio_spectrum.py`: compare a reference WAV against a decoded candidate WAV.
- `scripts/hca_key_probe.py`: probe candidate HCA keys.
- `scripts/arm64_xref.py`: lightweight IL2CPP/ARM64 string reference helper.

## Requirements

- Python 3.10+
- Java runtime
- A target slot's original `.acb` and `.awb` files from the APK
- Replacement chart files for one or more difficulties
- A replacement `16-bit PCM WAV`

For the currently supported target build, the CRI HCA keycode is global for the audio system rather than per song. The encoder uses this key by default:

```text
10029784319315621076
```

You can use the same key for any replacement song in this target build. Override it with `--key` only if you are working with a different build.

Install Python dependencies:

```powershell
python -m pip install -r requirements.txt
```

Download the signing helper:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\download_uber_apk_signer.ps1
```

`polytone_replace.py --sign` expects the signer at:

```text
tools/uber-apk-signer/uber-apk-signer-1.3.0.jar
```

## Prepare Inputs

Choose a target slot already present in the game. The default slot used by the tool is:

```text
ALCHEMY
```

For a slot named `TARGET_SLOT`, the original audio files are expected to be:

```text
AS_TARGET_SLOT.acb
AS_TARGET_SLOT.awb
```

Prepare chart files using the target game's chart text format. Difficulty labels accepted by the tool are:

```text
BASIC
ADVANCED
HARD
EXPERT
```

Example workspace layout:

```text
work/
  input/
    game.apk
    AS_TARGET_SLOT.acb
    AS_TARGET_SLOT.awb
    custom_song.wav
    chart_basic.txt
    chart_advanced.txt
    chart_hard.txt
    chart_expert.txt
  encoded/
  build/
```

## Step 1: Encode WAV To ACB/AWB

Use the target slot's original AWB as the container template and the original ACB as the cue/sheet template:

```powershell
python scripts\encode_cri_awb.py `
  --wav work\input\custom_song.wav `
  --template-awb work\input\AS_TARGET_SLOT.awb `
  --template-acb work\input\AS_TARGET_SLOT.acb `
  --out-awb work\encoded\AS_TARGET_SLOT.awb `
  --out-acb work\encoded\AS_TARGET_SLOT.acb `
  --out-hca work\encoded\AS_TARGET_SLOT.hca `
  --write-hcakey
```

Notes:

- The supported target build uses the built-in key by default.
- Pass `--key <keycode>` only when you need to override it.
- AWB subkey is read from `--template-awb` automatically.
- The input WAV must be 16-bit PCM.
- The generated ACB is currently the target slot's original ACB copied as a template.
- The generated AWB contains the newly encoded encrypted HCA audio.

## Step 2: Verify Encoded Audio

If you have `vgmstream-cli`, decode the generated AWB and compare it with the source WAV:

```powershell
vgmstream-cli.exe -o work\encoded\decoded_check.wav work\encoded\AS_TARGET_SLOT.awb

python scripts\compare_audio_spectrum.py `
  work\input\custom_song.wav `
  work\encoded\decoded_check.wav
```

A good result should have matching duration, sample rate, channels, and a high spectrum correlation. This catches broken encryption, missing keys, or obviously corrupted audio before rebuilding the APK.

## Step 3: Replace The Target Slot In The APK

Use the encoded audio and chart files to replace the selected slot:

```powershell
python scripts\polytone_replace.py `
  --apk work\input\game.apk `
  --out work\build\custom-slot-unsigned.apk `
  --slot TARGET_SLOT `
  --audio-acb work\encoded\AS_TARGET_SLOT.acb `
  --audio-awb work\encoded\AS_TARGET_SLOT.awb `
  --chart BASIC=work\input\chart_basic.txt `
  --chart ADVANCED=work\input\chart_advanced.txt `
  --chart HARD=work\input\chart_hard.txt `
  --chart EXPERT=work\input\chart_expert.txt `
  --patch-all-headers `
  --title "Custom Song Title" `
  --artist "Custom Artist" `
  --genre "Custom Genre" `
  --bpm 180 `
  --level BASIC=4 `
  --level ADVANCED=8 `
  --level HARD=12 `
  --level EXPERT=15 `
  --sign `
  --signed-out-dir work\build\signed
```

The signed APK is written to the folder passed with `--signed-out-dir`.

## What The Replacement Script Patches

`polytone_replace.py` changes:

- `assets/Audio/AS_<SLOT>.acb`
- `assets/Audio/AS_<SLOT>.awb`
- `TG_<SLOT>` group JSON
- `CH_<SLOT>_<DIFFICULTY>` chart header JSON
- `CH_<SLOT>_<DIFFICULTY>` chart text data
- Addressables catalog metadata for modified chartdata bundles

When chartdata bundles are changed, the script automatically patches `assets/aa/catalog.bin` by setting bundle CRC to `0` and updating `BundleSize`. Without this catalog patch, the game may reject modified chart bundles and show an empty song list.

## Install Notes

The default signing flow uses a debug certificate through `uber-apk-signer`.

If the original game is installed with a different certificate, Android will not allow direct overwrite installation. Uninstall the existing app first, or use a signing strategy compatible with your test environment.

## Current Limitations

- This is a replacement-slot workflow, not a true new-song insertion workflow.
- Cover art and other visual assets are not replaced by default.
- The generated ACB is copied from the target slot as a cue/sheet template. If a game relies on exact ACB duration metadata, additional ACB patching may be needed.
- The tool is tailored for the verified Addressables/catalog layout. Other builds may need catalog offset checks before use.

## Safety

Keep private inputs outside the repository:

- APK files
- extracted assets
- audio files
- generated bundles
- generated `.hcakey` files
- signed APK outputs

The `.gitignore` is configured to avoid committing those files.
