# Konspekt

A smart notepad for meetings — a local, privacy-first alternative to Granola.
Everything runs on your computer; nothing leaves your machine.
Always-on-top window, lives in the system tray.

## What it does

- Captures audio from your microphone and system output (so you hear the other side in a call)
- Transcribes speech locally: Russian goes through GigaAM, everything else through Whisper
- Identifies speakers and remembers voices across meetings — name someone once, and they get labeled automatically from then on
- Imports pre-recorded files: drag and drop into the window, and each file becomes a meeting with its own transcript

## Getting started

```bash
uv sync
uv run python -m app
```

Or double-click `konspekt.bat`.

This runs the development version straight from source with the latest changes.
It does not interfere with an installed copy: it uses its own data profile at
`%APPDATA%\Konspekt (razrabotka)`, with separate meetings, recordings, settings,
and model weights. Both copies can be open at the same time; the installed
version's meetings stay untouched.

You can tell them apart in the About dialog: the source version shows the
version number from `pyproject.toml`. The global hotkey goes to whichever copy
started first.

The profile name is set via the `KONSPEKT_PROFILE` environment variable, so you
can spin up as many copies as you like:

```bash
set KONSPEKT_PROFILE=experiment
uv run python -m app
```

Profile names must be in Latin script — they become folder and mutex names, and
the `.bat` file is read in a different code page where Cyrillic breaks.

Without the variable, the program uses the default user data directory, same as
the installed version. `KONSPEKT_DATA_DIR` overrides the path directly and takes
precedence over the profile; it exists for tests.

`uv run python -m app` shows logs in the console; `konspekt.bat` hides them.
Use the former when troubleshooting.

## Importing recordings

Drag files into the window in bulk, or click the arrow button next to "New
Meeting". The meeting title is taken from the file name.

Format detection is content-based, not extension-based: a file named `.txt`
will be parsed correctly if it contains mp3 audio, and a text file named `.mp3`
will be politely rejected. Anything ffmpeg understands works: mp3, wav, m4a,
ogg, opus, flac, and the audio track from video files.

If the recording has two channels with different voices (common in call
recordings), they are split into separate tracks. Ordinary stereo and music are
mixed down to mono.

## Keyboard shortcuts

| Shortcut       | Action                     |
|----------------|----------------------------|
| `Ctrl+Shift+K` | Show / hide window (global)|
| `Ctrl+N`       | New meeting                |
| `Ctrl+R`       | Start / stop recording     |
| `Ctrl+S`       | Save notes                 |

Closing the window hides it to the tray rather than quitting the app.
Exit through the tray menu.

## Where data lives

```
%APPDATA%\Konspekt\
  konspekt.db       meetings, notes, transcripts
  settings.json     settings and window geometry
  konspekt.log      log
  audio/            meeting recordings
models/             model weights (next to the program)
```

Models are downloaded once on first use, about 560 MB total:
GigaAM for Russian (214), Whisper small for other languages (239),
language identifier (81), and voice fingerprints (25).

## Status

Stages 1 through 3.6 are complete: window and storage, audio capture, speech
recognition, speaker diarization and language detection, and loading
pre-recorded files. Note synthesis is next. Details and the full plan are in
[ROADMAP.md](ROADMAP.md).

## Tests

```bash
uv run python smoke_test.py       # core functionality
uv run python audiofile_test.py   # audio file reading
uv run python import_test.py      # full import pipeline (requires sample files)
```

Tests that need live recordings look for them in the `audio_examples` folder.
It is not in the repository (12 MB of audio); without it those tests are skipped.

## License

Konspekt is distributed under [FSL-1.1-MIT](LICENSE.md): the source code is
open, you may freely use and modify it, with one restriction — you may not
package it into a competing commercial product. Each version becomes plain MIT
two years after release.

Third-party libraries included in the program and what this means in practice
are covered in [LICENSE.md](LICENSE.md).
