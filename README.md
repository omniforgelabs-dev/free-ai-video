# 📋 Free AI Video Generation (GitHub Actions)

Turn a **title + a script** into a custom **HD MP4 with an AI voiceover** — **100% free**,
directly inside GitHub Actions. No paid APIs, no software licences, no cloud subscriptions.
Just edit the on-screen form, press **Run**, and download your MP4.

---

## 🎯 Handover prompt — how to generate AI videos for free using GitHub Actions

You can trigger and download custom AI videos directly from the GitHub web interface:

1. **Go to GitHub Actions**
   - Open your repository in the browser.
   - Click the **Actions** tab at the top of the page.

2. **Select the video workflow**
   - On the left sidebar under **"Workflows"**, click **"Generate Free AI Video"**.

3. **Trigger the workflow ("Run workflow")**
   - Click the **"Run workflow"** dropdown button on the right.
   - Fill in the video parameters:

   | Field | What it does |
   |-------|--------------|
   | **Video Title** | The title on the first scene slide (e.g. "Product Overview 2026"). |
   | **Narration Script** | The spoken script. Separate scenes with the pipe `\|`. Lines starting with `#` become title text. |
   | **Voice Choice** | Pick an AI neural voice actor. |
   | **Visual Theme** | Pick a background colour theme. |
   | **Output Filename** | Name of the rendered file (default `generated_video.mp4`). |

   *Example script:*
   ```
   Welcome to our product overview.|This video was generated 100% for free.|No paid APIs or subscriptions were required!
   ```

4. **Click the green "Run workflow" button.**

5. **Download your finished video**
   - Wait ~1–2 minutes for the run to complete (a green checkmark appears).
   - Click into the completed run.
   - Scroll to the **Artifacts** section at the bottom.
   - Click **"generated-mp4-video"** to download the zip containing your rendered `.mp4`.

---

### 🎥 Camera motion & real background media (per scene)

Every scene can optionally attach a **camera motion** and/or a **background media file**
using the `::` separator after the narration text:

```
Scene narration text
Scene narration text::motion
Scene narration text::motion::media_path
```

The **motion** value is one of:
`zoom_in` · `zoom_out` · `pan_left` · `pan_right` · `pan_up` · `pan_down` · `static`.

If you omit the motion, an automatic one cycles through the scenes for variety.
The **media_path** is a file committed in the same repo — a **photo** (`.jpg/.png/...`)
or a **short clip** (`.mp4/.mov/...`). When used it replaces the Pillow gradient
background (covering the frame with no black bars), and the chosen motion is applied
on top. The caption text still overlays it in a semi-transparent box.

**Worked example** (title + 3 scenes, mixing motion + a real image):

```
# My Product Overview
Welcome to our product overview::zoom_out
This section pans across a real photo::pan_right::assets/photo.jpg
And here is a short clip with motion::zoom_in::assets/clip.mp4
```

Scenes fade (crossfade) into each other rather than cutting hard.

---

### 🎭 Avatar Mode — multiple characters (dialogue / drama)

Avatar Mode lets you put **named characters** into the video. Each character is a static
photo, has its **own voice**, and speaks specific lines. Consecutive scenes that switch
characters cut/crossfade between the two talking heads like a **shot‑reverse‑shot**
dialogue.

#### 1. Define your characters (`characters.json`)

```json
{
  "Mother":   { "photo": "assets/mother.jpg",   "voice": "en-US-JennyNeural",   "motion": "zoom_in" },
  "Son":      { "photo": "assets/son.jpg",      "voice": "en-US-GuyNeural",     "motion": "zoom_in" },
  "Neighbor": { "photo": "assets/neighbor.jpg", "voice": "en-GB-SoniaNeural",   "motion": "zoom_in" }
}
```

- `photo` — a portrait file committed in the repo (used as the scene background).
- `voice` — the character's own `edge-tts` voice (so each character *sounds* different).
- `motion` — optional default camera motion for that character.

You can also define characters on the command line with `--avatar-map`:
`Mother=/assets/mother.jpg,Son=/assets/son.jpg`.

#### 2. Assign a character to a scene with `CHAR:Name`

Append `::CHAR:Name` at the end of a scene **or** start the line with `Name:` (the name
is stripped from the spoken line so the actor doesn't read it):

```text
Mother: Where have you been all night?::static::CHAR:Mother
Son: I was out with friends, I swear.::static::CHAR:Son
Neighbor: I saw the whole thing from my window.::pan_left::CHAR:Neighbor
Mother: We will talk about this at breakfast.::zoom_out::CHAR:Mother
```

The usual `::motion` still works alongside the character tag. Each character uses the
`voice` from `characters.json` automatically. The character's photo replaces the
gradient background, and a **name pill + spoken‑line box** overlays it for legibility.

> **Fallback (additive) behaviour:** a scene **without** `CHAR:` keeps the normal
> gradient / media / motion behavior. Avatar Mode is optional per scene — you can mix
> character and non‑character scenes freely.

#### 🧬 Lip‑sync (optional)

By default an assigned character is shown as a **static talking‑head card** with motion.
To make the mouth actually move in sync with the voice, set up the free, open‑source
**[Wav2Lip](https://github.com/Rudrabha/Wav2Lip)** model on the runner and point the
workflow at it (see "Lip‑sync setup & limitations" below). `generate_video.py` detects a
valid Wav2Lip checkout via environment variables (`WAV2LIP_DIR`, `WAV2LIP_CHECKPOINT`)
and shells out to it per character scene; if the model is missing or fails, it **falls
back** to the static card so the video is never broken.

#### ✅ What Avatar Mode can and cannot do

**Can do** (free, open‑source):
- Multiple characters, each with its own photo and voice.
- Per‑scene character assignment, shot‑reverse‑shot cutting/crossfades between them.
- Character photo background + name pill + dialogue caption.
- Optionally lip‑sync **mouth/head motion** from a **single static photo** (Wav2Lip).

**Cannot do** (out of scope for a free/open‑source approach):
- **No body movement** (no gestures, walking, head turns beyond the model's warping).
- **No shared‑scene interaction** — characters are cut between, not shown together in one
  frame talking to each other (you'd need compositing + a scene with all faces).
- **No full text‑to‑performance acting** (no directional acting, emotion beyond the still).

Going beyond this (full body, multiple faces together, acting) requires paid/cloud models
or substantial custom code — not something a free GitHub Actions build does.

---

## 🛠️ Technical architecture (for developers & maintainers)

### 1. The core Python script (`generate_video.py`)
This script handles the whole rendering pipeline:

- **Voice generation (edge-tts):** uses Microsoft Edge's free neural voice engine to
  convert each part of the script into audio.
- **Graphics & layout (Pillow):** renders high-resolution 1080p slides with background
  gradients, automatic word-wrapping and styled text (title + body).
- **Video composition (MoviePy & FFmpeg):** syncs the voiceover with the rendered slides,
  times each slide to the length of its narration, adds a gentle slow-zoom, and exports
  the final `.mp4`.

### 2. The GitHub Actions workflow (`.github/workflows/generate_video.yml`)
- Triggered on-demand via `workflow_dispatch` (the form above).
- Spins up an `ubuntu-latest` runner on GitHub.
- Installs **ffmpeg** and the Python packages (**edge-tts, moviepy, pillow, numpy**).
- Executes `generate_video.py` with your inputs.
- Uploads the generated `.mp4` as a downloadable GitHub **Artifact**.

---

## 💻 Run locally (optional)

1. **Install prerequisites:** FFmpeg and Python 3.10+.
2. **Install Python libraries:**
   ```bash
   pip install edge-tts moviepy pillow numpy
   ```
3. **Run:**
   ```bash
   python3 generate_video.py \
     --title "My Free AI Video" \
     --script "Welcome to slide 1 narration.|This is slide 2 narration." \
     --voice "en-US-ChristopherNeural" \
     --theme "gradient" \
     --output "my_video.mp4"
   ```

### Available voices
`en-US-ChristopherNeural` (M·US), `en-US-JennyNeural` (F·US), `en-US-GuyNeural` (M·US),
`en-GB-SoniaNeural` (F·UK), `en-AU-WilliamNeural` (M·AU).

### Available themes
`dark` (slate navy) · `gradient` (royal purple→blue) · `ocean` (teal/emerald) · `sunset` (warm red/orange).

### Extra options
- `--rate +8%` / `--volume -10%` — adjust narration speed/volume.
- `--list-themes` / `--list-voices` / `--list-motions` — print the available options.

### Handling media paths in the workflow
Because the `script` input is passed straight to the generator, a `::media_path` must
point to a file **committed in the same repository** (e.g. `assets/photo.jpg`). Upload
any media files you want to use into the repo first (they end up next to
`generate_video.py`), then reference them in the script.

### Lip-sync setup & limitations

To enable real talking-head mouth movement, set up Wav2Lip on the runner (free/open-source):

1. **Install the model** (one-time script in the workflow or pre-build a runner image):
   ```bash
   git clone https://github.com/Rudrabha/Wav2Lip.git /tmp/Wav2Lip
   pip install torch torchvision numpy pandas librosa opencv-python \
       face-alignment \
       --index-url https://download.pytorch.org/whl/cpu
   # Download the ~400MB wav2lip_gan.pth checkpoint (from the model's distribution source)
   # into /tmp/Wav2Lip/checkpoints/wav2lip_gan.pth
   ```
2. **Point the workflow at it** via env vars:
   ```yaml
   env:
     WAV2LIP_DIR: /tmp/Wav2Lip
     WAV2LIP_CHECKPOINT: /tmp/Wav2Lip/checkpoints/wav2lip_gan.pth
   ```
   (`generate_video.py` auto-detects these; pass `--no-lipsync` to force static cards.)

**Expected runtime:** Wav2Lip on a CPU-only GitHub runner processes roughly a few seconds of
video in about a minute to several minutes (it is GPU-accelerated in the original repo; CPU
is slow). Budget ~1–3 minutes *per second of lip-synced footage*.

**GitHub Actions time-limit risk:** free `ubuntu-latest` jobs are capped at **6 hours**;
`workflow_dispatch`-triggered workflows default to that limit. For short multi-character
scripts (a few scenes, each a few seconds) this is fine. **Long scripts** (many lip-synced
scenes, or lots of footage) can push a run toward the limit and get killed. Mitigations:
keep scenes short, only lip-sync a handful of the most important lines, or split into
multiple runs.

**Honest capability note:** the lip-sync animates the **mouth (and slight head/mask warp)
of a single fixed still photo**. It does **not** produce body movement, gestures, eye gaze,
or multiple characters interacting on screen together — those remain out of scope for a
free/open-source build (they need paid/cloud models or heavy custom code).

---

#### Run Avatar Mode locally

```bash
python3 generate_video.py \
  --title "The Missing Key" \
  --script "Mother: Where have you been all night?::static::CHAR:Mother|Son: I was out with friends, I swear.::static::CHAR:Son|Neighbor: I saw the whole thing from my window.::pan_left::CHAR:Neighbor" \
  --theme sunset --output avatar_demo.mp4
```
Helpful flags: `--characters characters.json`, `--avatar-map "A=assets/a.jpg"`,
`--no-lipsync`, `--list-characters`.
