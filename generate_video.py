#!/usr/bin/env python3
"""
generate_video.py — turn a title + a script into a real, narrated HD MP4, 100% free.

PIPELINE
--------
1. edge-tts  -> synthesize each script scene into free AI voiceover audio.
2. Pillow    -> render a 1080p slide for every scene (animated-gradient style
                background, word-wrapped styled text, title and theme colors).
3. MoviePy + FFmpeg -> sync each slide to its spoken duration, join scenes,
                add fade transitions, and export an .mp4 with the voiceover.

Usage:
    python3 generate_video.py \
        --title "My Free AI Video" \
        --script "Scene one narration.|Scene two narration." \
        --voice en-US-ChristopherNeural \
        --theme gradient \
        --output my_video.mp4

Scenes are separated by the pipe character '|'. Lines beginning with '#' are
shown as full-width title text (kept on screen a little longer).
"""

import argparse
import asyncio
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Optional imports, with a helpful error if missing.
# --------------------------------------------------------------------------- #
def _need(module, pip_name):
    try:
        return __import__(module)
    except Exception:
        sys.exit(f"Missing Python package '{module}'. Install with:\n  pip install {pip_name}")


def find_ffmpeg():
    env = shlex.split(os.environ.get("FFMPEG", ""))
    if env and shutil.which(env[0]):
        return env[0]
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg  # noqa

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Themes (name -> colors, title color, body color)
# --------------------------------------------------------------------------- #
THEMES = {
    "dark": {
        "colors": [(22, 27, 34), (28, 34, 46), (16, 22, 30)],
        "title": (255, 255, 255),
        "body": (235, 238, 245),
    },
    "gradient": {
        "colors": [(40, 30, 150), (108, 40, 180), (20, 40, 130)],
        "title": (255, 255, 255),
        "body": (240, 240, 255),
    },
    "ocean": {
        "colors": [(4, 60, 74), (14, 120, 120), (3, 44, 60)],
        "title": (255, 255, 255),
        "body": (226, 246, 250),
    },
    "sunset": {
        "colors": [(150, 45, 30), (230, 120, 40), (110, 30, 80)],
        "title": (255, 250, 245),
        "body": (255, 240, 235),
    },
}

VOICES = [
    "en-US-ChristopherNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-AU-WilliamNeural",
]

W, H = 1920, 1080
FPS = 30
VIDEO_DIR = Path(__file__).resolve().parent


# --------------------------------------------------------------------------- #
#  Deterministic slide rendering (pure Pillow)
# --------------------------------------------------------------------------- #
def hash_color(colors, i):
    return colors[i % len(colors)]


def make_background(theme, seed, size=(W, H)):
    """Draw a soft vertical gradient background with subtle diagonal glow."""
    from PIL import Image, ImageDraw, ImageFilter

    cols = THEMES[theme]["colors"]
    top, mid, bot = hash_color(cols, seed), hash_color(cols, seed + 1), hash_color(cols, seed + 2)
    # Build a tall 1px gradient then stretch.
    grad = Image.new("RGB", (1, size[1]))
    d = ImageDraw.Draw(grad)
    for y in range(size[1]):
        t = y / size[1]
        # smooth two-stop blend top/mid/bottom
        if t < 0.5:
            k = t / 0.5
            c = tuple(int(a + (b - a) * k) for a, b in zip(top, mid))
        else:
            k = (t - 0.5) / 0.5
            c = tuple(int(a + (b - a) * k) for a, b in zip(mid, bot))
        d.line([(0, y), (0, y)], fill=c)
    bg = grad.resize(size)

    # A soft radial glow for depth.
    glow = Image.new("L", size, 0)
    gd = ImageDraw.Draw(glow)
    cx, cy = int(size[0] * (0.25 + 0.5 * (seed % 3) / 2.0)), int(size[1] * 0.30)
    r = int(size[0] * 0.35)
    gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(320))
    overlay = Image.new("RGB", size, (255, 255, 255))
    bg = Image.composite(Image.blend(bg, overlay, 0.12), bg, glow)

    # subtle vignette
    vig = Image.new("L", size, 0)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-size[0] * 0.2, -size[1] * 0.2, size[0] * 1.2, size[1] * 1.2], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(250))
    dark = Image.new("RGB", size, (0, 0, 0))
    bg = Image.composite(bg, dark, vig)
    return bg


def wrap_words(text, font, max_width):
    """Wrap text so the rendered width fits max_width (keeps words intact)."""
    from PIL import Image, ImageDraw

    tmp = Image.new("RGB", (10, 10))
    d = ImageDraw.Draw(tmp)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        cand = (cur + " " + w).strip() if cur else w
        if d.textlength(cand, font=font) <= max_width or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def draw_centered_text(draw, lines, font, y, color, size, line_gap=1.3):
    """Draw each line centered at horizontal midpoint, bottom-align by measuring."""
    total_h = 0
    for ln in lines:
        total_h += font.getbbox(ln)[3] - font.getbbox(ln)[1] + font.getbbox(ln)[1]  # height
    # simpler: use font size line spacing
    lh = int(size * line_gap)
    y = y - lh * len(lines) // 2
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((W - w) / 2, y), ln, font=font, fill=color)
        y += lh


def find_font(size, bold=True):
    from PIL import ImageFont

    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
    ]
    if not bold:
        cands = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"] + cands
    for c in cands:
        if Path(c).exists():
            try:
                return ImageFont.truetype(c, size)
            except Exception:
                continue
    return ImageFont.load_default()


def render_slide(text, theme, seed, title=False, out_png=None):
    from PIL import Image, ImageDraw

    bg = make_background(theme, seed)
    draw = ImageDraw.Draw(bg)

    if title:
        size = int(H * 0.10)
        font = find_font(size, bold=True)
        colour = THEMES[theme]["title"]
        lines = wrap_words(text, font, int(W * 0.78))
        draw_centered_text(draw, lines, font, H // 2, colour, size)
        # accent underline
        uw = int(W * (0.06 if len(lines) == 1 else 0.03))
        y = H // 2 + (len(lines) * int(size * 1.3)) // 2 + int(size * 0.6)
        draw.rounded_rectangle([(W - uw) / 2, y, (W + uw) / 2, y + int(size * 0.10)],
                               radius=int(size * 0.05),
                               fill=THEMES[theme]["title"])
    else:
        size = int(H * 0.06)
        font = find_font(size, bold=False)
        colour = THEMES[theme]["body"]
        lines = wrap_words(text, font, int(W * 0.80))
        draw_centered_text(draw, lines, font, H // 2, colour, size)

    if out_png:
        bg.save(out_png)
    return bg


# --------------------------------------------------------------------------- #
#  Voiceover (edge-tts, free)
# --------------------------------------------------------------------------- #
def parse_scenes(script):
    """Split script on '|' . Lines starting with '#' become title scenes."""
    parts = [p.strip() for p in script.split("|") if p.strip()]
    scenes = []
    for p in parts:
        for line in p.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                scenes.append({"text": line.lstrip("#").strip(), "title": True})
            else:
                scenes.append({"text": line, "title": False})
    return scenes


async def tts_mp3(text, voice, out_mp3, rate="+0%", volume="+0%"):
    import edge_tts

    comm = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
    await comm.save(str(out_mp3))
    return out_mp3


def speak(text, voice, out_mp3):
    try:
        asyncio.run(tts_mp3(text, voice, out_mp3))
        return out_mp3.exists() and out_mp3.stat().st_size > 500
    except Exception as e:
        print(f"  ! edge-tts failed for a scene ({e}). That scene will be silent.")
        return False


# --------------------------------------------------------------------------- #
#  Duration helpers
# --------------------------------------------------------------------------- #
def audio_duration(path, ffmpeg):
    if not path or not Path(path).exists():
        return None
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # parse "time=" from stderr
        import re
        m = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", r.stderr)
        if m:
            h, mi, s = m[-1]
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
#  MoviePy composition
# --------------------------------------------------------------------------- #
def _ffmpeg_run(ffmpeg, cmd):
    print("+", " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-4000:], file=sys.stderr)
        sys.exit(f"ffmpeg failed (exit {r.returncode})")


def assemble_audio(scenes_with_media, tmpdir, ffmpeg, total_dur):
    """Build one continuous voiceover track: each scene's voice padded to its
    duration, then concatenated into a single wav."""
    pieces = []
    for i, sc in enumerate(scenes_with_media):
        dur = sc["duration"]
        out = tmpdir / f"pad_{i:03d}.wav"
        if sc["audio"] and Path(sc["audio"]).exists():
            _ffmpeg_run(ffmpeg, [
                ffmpeg, "-y", "-i", str(sc["audio"]),
                "-af", f"apad,atrim=duration={dur:.3f}",
                "-ar", "44100", "-ac", "2", str(out),
            ])
        else:
            _ffmpeg_run(ffmpeg, [
                ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", f"{dur:.3f}", str(out),
            ])
        pieces.append(out)
    lst = tmpdir / "audio_list.txt"
    lst.write_text("\n".join(f"file '{Path(p).resolve()}'" for p in pieces))
    full = tmpdir / "voiceover.wav"
    _ffmpeg_run(ffmpeg, [
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", str(full),
    ])
    return full


def build_video(scenes_with_media, voice, theme, out_path, ffmpeg, tmpdir):
    from moviepy import ImageClip, concatenate_videoclips

    total_dur = sum(sc["duration"] for sc in scenes_with_media)

    # 1) Compose the (silent) video from the rendered slides with MoviePy.
    clips = []
    for sc in scenes_with_media:
        dur = sc["duration"]
        img_clip = ImageClip(str(sc["png"])).with_duration(dur)
        # gentle slow zoom (Ken Burns) -> adds life to a static frame
        img_clip = img_clip.resized(lambda t, d=dur: 1.0 + 0.06 * (t / max(d, 0.01)))
        img_clip = img_clip.with_duration(dur)
        clips.append(img_clip)

    video = concatenate_videoclips(clips, method="compose")
    video = video.with_fps(FPS)

    silent = tmpdir / "silent_video.mp4"
    video.write_videofile(
        str(silent), fps=FPS, codec="libx264", audio=False,
        preset="medium", threads=2, logger=None,
    )
    video.close()

    # 2) Build the exact-timed voiceover with FFmpeg.
    full_audio = assemble_audio(scenes_with_media, tmpdir, ffmpeg, total_dur)

    # 3) Mux video + audio together, with a gentle fade out at the very end.
    _ffmpeg_run(ffmpeg, [
        ffmpeg, "-y", "-i", str(silent), "-i", str(full_audio),
        "-shortest",
        "-af", f"afade=t=out:st={max(0.0, total_dur - 0.6):.3f}:d=0.6",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", str(out_path),
    ])


def main():
    ap = argparse.ArgumentParser(description="Generate a free narrated HD video.")
    ap.add_argument("--title", default="My Free AI Video")
    ap.add_argument("--script", required=True,
                    help="Narration. Separate scenes with '|'. Lines starting with '#' become title text.")
    ap.add_argument("--voice", default="en-US-ChristopherNeural", choices=VOICES)
    ap.add_argument("--theme", default="gradient", choices=list(THEMES.keys()))
    ap.add_argument("--output", default="generated_video.mp4")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--volume", default="+0%")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--list-themes", action="store_true")
    ap.add_argument("--list-voices", action="store_true")
    args = ap.parse_args()

    if args.list_themes:
        print("Themes:", ", ".join(THEMES.keys()))
        return
    if args.list_voices:
        print("\n".join(VOICES))
        return

    ffmpeg = args.ffmpeg or find_ffmpeg()
    if not ffmpeg:
        sys.exit("ffmpeg not found. Install it (e.g. `sudo apt install ffmpeg`) or set FFMPEG=/path/to/ffmpeg.")
    print("ffmpeg:", ffmpeg)

    # Prepend the title as a dedicated title scene.
    scenes = [{"text": args.title, "title": True}] + parse_scenes(args.script)
    print(f"Scenes: {len(scenes)}")

    with tempfile.TemporaryDirectory(prefix="aivideo_") as td:
        td = Path(td)
        ctx = {"scenes": scenes}
        for i, sc in enumerate(scenes):
            png = td / f"slide_{i:03d}.png"
            render_slide(sc["text"], args.theme, i, title=sc["title"], out_png=png)
            sc["png"] = png

            mp3 = td / f"voice_{i:03d}.mp3"
            ok = speak(sc["text"], args.voice, mp3) if sc["text"].strip() else False
            sc["audio"] = mp3 if ok else None

            if ok:
                dur = audio_duration(mp3, ffmpeg) or (len(sc["text"]) / 15.0)
            else:
                dur = (len(sc["text"]) / 15.0 + 0.5)
            # title scenes linger a little longer
            if sc["title"]:
                dur += 1.0
            dur = max(1.2, dur + 0.4)  # small beat between scenes
            sc["duration"] = round(dur, 3)
            print(f"  [{i}] {sc['duration']:.2f}s  {'TITLE ' if sc['title'] else ''}{sc['text'][:48]}")

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        build_video(scenes, args.voice, args.theme, out, ffmpeg, td)

    print(f"✅ Done -> {out}  ({out.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
