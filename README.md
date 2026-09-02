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
- `--list-themes` / `--list-voices` — print the available options.
