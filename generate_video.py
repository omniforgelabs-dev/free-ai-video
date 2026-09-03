#!/usr/bin/env python3
"""
generate_video.py — turn a title + a script into a real, narrated HD MP4, 100% free.

PIPELINE
--------
1. edge-tts  -> synthesize each script scene into free AI voiceover audio.
2. Pillow    -> render a high-res background (gradient or a real image clip) and a
                transparent, word-wrapped text overlay for every scene.
3. MoviePy + FFmpeg -> apply per-scene camera motion (zoom / pan), crossfade between
                scenes, sync each scene to its spoken duration, and export an .mp4.

USAGE
-----
    python3 generate_video.py \
        --title "My Free AI Video" \
        --script "Scene one narration.|Scene two narration." \
        --voice en-US-ChristopherNeural \
        --theme gradient \
        --output my_video.mp4

SCRIPT SYNTAX
-------------
- Scenes are separated by the pipe character '|'.
- Lines beginning with '#' are shown as a full-width title scene.
- Optional per-scene camera motion and background media use the '::' separator:

      "This line pans left::pan_left"
      "This line uses a photo::pan_right::./assets/photo.jpg"
      "This line plays a short clip::zoom_in::./assets/loop.mp4"

  motion   : zoom_in | zoom_out | pan_left | pan_right | pan_up | pan_down | static
            (default: an automatic, cycling motion if not given)
  media    : a path to a local image (.jpg/.png/...) or short video (.mp4/.mov/...).
            If omitted, the Pillow theme gradient is used.
"""

import argparse
import asyncio
import math
import os
import re
import shlex  # <-- fix: previously missing, crashed find_ffmpeg()
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
#  Themes & constants
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

MOTIONS = ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down", "static"]
AUTO_MOTIONS = ["zoom_in", "zoom_out", "pan_right", "pan_left", "pan_up", "pan_down"]
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v", ".ogg"}

# Output resolution.
OW, OH = 1920, 1080
FPS = 30
# Backgrounds render at 1.25x resolution so pans have room to move without
# exposing edges, then the 1920x1080 camera window travels inside them.
SW, SH = int(OW * 1.25), int(OH * 1.25)  # 2400 x 1350
ZOOM = 0.16           # zoom range (16%): reads as real camera movement
DEFAULT_FADE = 0.4    # crossfade length between scenes
VIDEO_DIR = Path(__file__).resolve().parent


# =========================================================================== #
#  Pillow: backgrounds + text overlays
# =========================================================================== #
def hash_color(colors, i):
    return colors[i % len(colors)]


def make_gradient(theme, seed, size=(SW, SH)):
    """Return a soft vertical gradient background with a subtle glow."""
    from PIL import Image, ImageDraw, ImageFilter

    w, h = size
    cols = THEMES[theme]["colors"]
    top, mid, bot = hash_color(cols, seed), hash_color(cols, seed + 1), hash_color(cols, seed + 2)
    grad = Image.new("RGB", (1, h))
    d = ImageDraw.Draw(grad)
    for y in range(h):
        t = y / h
        if t < 0.5:
            k = t / 0.5
            c = tuple(int(a + (b - a) * k) for a, b in zip(top, mid))
        else:
            k = (t - 0.5) / 0.5
            c = tuple(int(a + (b - a) * k) for a, b in zip(mid, bot))
        d.line([(0, y), (0, y)], fill=c)
    bg = grad.resize((w, h))

    glow = Image.new("L", (w, h), 0)
    gd = ImageDraw.Draw(glow)
    cx, cy = int(w * (0.25 + 0.5 * (seed % 3) / 2.0)), int(h * 0.30)
    r = int(w * 0.35)
    gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=70)
    glow = glow.filter(ImageFilter.GaussianBlur(320))
    overlay = Image.new("RGB", (w, h), (255, 255, 255))
    bg = Image.composite(Image.blend(bg, overlay, 0.12), bg, glow)

    vig = Image.new("L", (w, h), 0)
    vd = ImageDraw.Draw(vig)
    vd.ellipse([-w * 0.2, -h * 0.2, w * 1.2, h * 1.2], fill=255)
    vig = vig.filter(ImageFilter.GaussianBlur(250))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    bg = Image.composite(bg, dark, vig)
    return bg


def cover_resize(pil_img, size=(SW, SH)):
    """Resize + center-crop an image so it exactly covers `size` (no black bars)."""
    from PIL import Image

    img = pil_img.convert("RGB")
    tw, th = size
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = int(sw * scale + 0.5), int(sh * scale + 0.5)
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (nw - tw) // 2
    y = (nh - th) // 2
    return img.crop((x, y, x + tw, y + th))


def load_media_still(path, size=(SW, SH)):
    from PIL import Image

    return cover_resize(Image.open(path), size)


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


def wrap_words(text, font, max_width):
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


def render_text_overlay(text, theme, title=False, size=(OW, OH), pad=0.05):
    """Return a transparent RGBA image containing the wrapped, boxed text,
    positioned so it stays perfectly still while the background moves."""
    from PIL import Image, ImageDraw

    w, h = size
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base)

    if title:
        fsize = int(h * 0.10)
        font = find_font(fsize, bold=True)
        colour = THEMES[theme]["title"]
    else:
        fsize = int(h * 0.058)
        font = find_font(fsize, bold=False)
        colour = THEMES[theme]["body"]

    lines = wrap_words(text, font, int(w * 0.78))
    lh = int(fsize * 1.35)
    block_h = lh * len(lines)

    # measure widest line to size the box
    widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
    box_pad = int(fsize * 0.6)
    bx0 = int((w - widest) / 2) - box_pad
    bx1 = int((w + widest) / 2) + box_pad
    by0 = int((h - block_h) / 2) - box_pad
    by1 = int((h + block_h) / 2) + box_pad
    # clamp box inside frame
    bx0, bx1 = max(40, bx0), min(w - 40, bx1)
    by0, by1 = max(30, by0), min(h - 30, by1)
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=int(fsize * 0.3),
                           fill=(0, 0, 0, 140), outline=(255, 255, 255, 60))
    if not title:
        # thin accent bar above the box
        bar_w = int(w * 0.06)
        draw.rounded_rectangle([(w - bar_w) / 2, by0 - int(fsize * 0.4),
                                (w + bar_w) / 2, by0 - int(fsize * 0.15)],
                               radius=int(fsize * 0.15), fill=colour)

    y = int(h / 2 - block_h / 2) + lh // 2 - fsize // 2
    for i, ln in enumerate(lines):
        lw = draw.textlength(ln, font=font)
        # text shadow for legibility
        draw.text(((w - lw) / 2 + 2, y + 2), ln, font=font, fill=(0, 0, 0, 200))
        draw.text(((w - lw) / 2, y), ln, font=font, fill=colour)
        y += lh

    return base


# =========================================================================== #
#  Script parsing with ::motion and ::media_path
# =========================================================================== #
def parse_scenes(script):
    """Split script on '|'. Each scene may carry a ::motion and/or ::media path.

    Returns a list of dicts: {text, title, motion, media}.
    """
    parts = [p.strip() for p in script.split("|") if p.strip()]
    scenes = []
    for p in parts:
        for raw in p.splitlines():
            line = raw.strip()
            if not line:
                continue
            title = line.startswith("#")
            tokens = line.split("::")
            text = tokens[0].lstrip("#").strip()
            motion, media = None, None
            for tok in tokens[1:]:
                tok = tok.strip()
                if not tok:
                    continue
                if tok in MOTIONS:
                    motion = tok
                else:
                    media = tok
            scenes.append({"text": text, "title": title, "motion": motion, "media": media})
    return scenes


def resolve_media(p):
    if not p:
        return None
    cands = [Path(p)]
    if not Path(p).is_absolute():
        cands.append(VIDEO_DIR / p)
    for c in cands:
        if c.exists():
            return str(c.resolve())
    return None


# =========================================================================== #
#  Voiceover (edge-tts, free)
# =========================================================================== #
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


# =========================================================================== #
#  Duration helpers
# =========================================================================== #
def audio_duration(path, ffmpeg):
    if not path or not Path(path).exists():
        return None
    try:
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", str(path), "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.findall(r"time=(\d+):(\d+):(\d+\.?\d*)", r.stderr)
        if m:
            h, mi, s = m[-1]
            return int(h) * 3600 + int(mi) * 60 + float(s)
    except Exception:
        pass
    return None


# =========================================================================== #
#  Camera motion (super-res base clip -> moving 1920x1080 window)
# =========================================================================== #
def camera(motion, t, play):
    """Return (zoom, x_offset, y_offset) for the window at time t.

    The base background clip is SWxSH; after zooming it becomes
    (SW*z)x(SH*z). We place it inside a 1920x1080 composite at a negative
    offset so the visible window travels according to `motion`. The clip is
    always larger than the window, so no edges are ever exposed.
    """
    p = min(1.0, t / play) if play > 0 else 1.0
    if motion == "zoom_in":
        z = 1.0 + ZOOM * p
        cx, cy = SW / 2, SH / 2
    elif motion == "zoom_out":
        z = (1.0 + ZOOM) - ZOOM * p
        cx, cy = SW / 2, SH / 2
    elif motion == "static":
        z = 1.0
        cx, cy = SW / 2, SH / 2
    elif motion == "pan_left":
        z = 1.0 + ZOOM
        cw, ch = OW / z, OH / z
        cx = (SW - cw / 2) - (SW - cw) * p
        cy = SH / 2
    elif motion == "pan_right":
        z = 1.0 + ZOOM
        cw, ch = OW / z, OH / z
        cx = (cw / 2) + (SW - cw) * p
        cy = SH / 2
    elif motion == "pan_up":
        z = 1.0 + ZOOM
        cw, ch = OW / z, OH / z
        cy = (SH - ch / 2) - (SH - ch) * p
        cx = SW / 2
    elif motion == "pan_down":
        z = 1.0 + ZOOM
        cw, ch = OW / z, OH / z
        cy = (ch / 2) + (SH - ch) * p
        cx = SW / 2
    else:
        z = 1.0
        cx, cy = SW / 2, SH / 2

    x_off = -(cx * z - OW / 2)
    y_off = -(cy * z - OH / 2)
    return z, x_off, y_off


# =========================================================================== #
#  Scene background clips (gradient / image / video) with motion + text overlay
# =========================================================================== #
def build_background_clip(sc, dur, tmpdir, ffmpeg):
    """Return a silent background clip of `dur` seconds at SWxSH."""
    from moviepy import ImageClip

    media = sc.get("media")
    motion = sc.get("motion") or "static"

    if media and media.lower().endswith(tuple(VIDEO_EXTS)):
        from moviepy import VideoFileClip
        from moviepy.video import fx as vfx

        vc = VideoFileClip(media).without_audio()
        if vc.duration <= 0.01:
            vc.close()
            vc = None
        else:
            if vc.duration < dur:
                try:
                    vc = vc.with_effects([vfx.Loop(duration=dur)])
                except Exception as e:
                    print(f"  ! could not loop media ({e}); using what's available.")
                    vc = vc.subclipped(0, min(vc.duration, dur))
            else:
                vc = vc.subclipped(0, dur)
            bg = vc.resized(new_size=(SW, SH))
            return bg.with_duration(dur)

    # Image or gradient -> static still rendered/padded to head-room size.
    if media and media.lower().endswith(tuple(IMAGE_EXTS)):
        img = load_media_still(media, (SW, SH))
    else:
        img = make_gradient(sc.get("_theme", "gradient"), sc.get("_seed", 0), (SW, SH))

    png = tmpdir / f"bg_{sc.get('_i', 0):03d}.png"
    img.save(png)
    return ImageClip(str(png)).with_duration(dur).without_audio()


def apply_motion(clip, motion, play):
    """Zoom + pan the SWxSH background inside the 1920x1080 window."""
    from moviepy.video import fx as vfx

    clip = clip.resized(lambda t: camera(motion, t, play)[0])
    clip = clip.with_position(lambda t: (camera(motion, t, play)[1], camera(motion, t, play)[2]))
    return clip


def make_scene_clip(sc, bg, overlay_png, fade, ffmpeg, tmpdir):
    """Composite the moving background with the fixed text overlay, sized to output."""
    from PIL import Image
    from moviepy import ImageClip, CompositeVideoClip

    bg = apply_motion(bg, sc["motion"], sc["duration"])
    overlay = ImageClip(str(overlay_png)).with_duration(sc["duration"] + fade)
    composed = CompositeVideoClip([bg, overlay], size=(OW, OH)).with_fps(FPS)
    return composed


# =========================================================================== #
#  FFmpeg helpers
# =========================================================================== #
def _ffmpeg_run(ffmpeg, cmd):
    print("+", " ".join(map(str, cmd)))
    r = subprocess.run(list(map(str, cmd)), capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-4000:], file=sys.stderr)
        sys.exit(f"ffmpeg failed (exit {r.returncode})")


def assemble_audio(scenes, tmpdir, ffmpeg):
    """Build one continuous voiceover track: each scene's voice padded to its
    duration, then concatenated into a single wav."""
    pieces = []
    for i, sc in enumerate(scenes):
        dur = sc["duration"]
        out = tmpdir / f"pad_{i:03d}.wav"
        if sc.get("audio") and Path(sc["audio"]).exists():
            _ffmpeg_run(ffmpeg, [ffmpeg, "-y", "-i", str(sc["audio"]),
                                 "-af", f"apad,atrim=duration={dur:.3f}",
                                 "-ar", "44100", "-ac", "2", str(out)])
        else:
            _ffmpeg_run(ffmpeg, [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                                 "-t", f"{dur:.3f}", str(out)])
        pieces.append(out)
    lst = tmpdir / "audio_list.txt"
    lst.write_text("\n".join(f"file '{Path(p).resolve()}'" for p in pieces))
    full = tmpdir / "voiceover.wav"
    _ffmpeg_run(ffmpeg, [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
                         "-c", "copy", str(full)])
    return full


# =========================================================================== #
#  Build the video (crossfades between scenes, sync voiceover, mux)
# =========================================================================== #
def build_video(scenes, voice, theme, out_path, ffmpeg, tmpdir):
    from moviepy import CompositeVideoClip
    from moviepy.video import fx as vfx

    total_dur = sum(sc["duration"] for sc in scenes)
    fade = DEFAULT_FADE

    # 1) Build each animated + overlaid scene clip, stacking starts so the last
    #    fade of scene i overlaps the first fade of scene i+1 (a crossfade).
    stack = []
    start = 0.0
    n = len(scenes)
    for i, sc in enumerate(scenes):
        overlay_png = tmpdir / f"ov_{i:03d}.png"
        sc["_overlay"].save(overlay_png)

        bg = build_background_clip(sc, sc["duration"] + fade, tmpdir, ffmpeg)
        comp = make_scene_clip(sc, bg, overlay_png, fade, ffmpeg, tmpdir)
        comp = comp.with_start(start)

        if i < n - 1:
            comp = comp.with_effects([vfx.CrossFadeOut(fade)])
        if i > 0:
            comp = comp.with_effects([vfx.CrossFadeIn(fade)])
        stack.append(comp)
        start += sc["duration"]

    video = CompositeVideoClip(stack, size=(OW, OH)).with_fps(FPS)

    silent = tmpdir / "silent_video.mp4"
    video.write_videofile(str(silent), fps=FPS, codec="libx264", audio=False,
                          preset="medium", threads=2, logger=None)
    video.close()

    # 2) Build the exact-timed voiceover with FFmpeg.
    full_audio = assemble_audio(scenes, tmpdir, ffmpeg)

    # 3) Mux video + audio, with a gentle fade out at the very end.
    _ffmpeg_run(ffmpeg, [ffmpeg, "-y", "-i", str(silent), "-i", str(full_audio),
                         "-shortest",
                         "-af", f"afade=t=out:st={max(0.0, total_dur - 0.6):.3f}:d=0.6",
                         "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                         "-movflags", "+faststart", str(out_path)])


# =========================================================================== #
#  Main
# =========================================================================== #
def main():
    ap = argparse.ArgumentParser(description="Generate a free narrated HD video.")
    ap.add_argument("--title", default="My Free AI Video")
    ap.add_argument("--script", required=True,
                    help="Narration. Separate scenes with '|'. A scene may add '::motion' "
                         "and/or '::/path/to/media'. Lines starting with '#' become title text.")
    ap.add_argument("--voice", default="en-US-ChristopherNeural", choices=VOICES)
    ap.add_argument("--theme", default="gradient", choices=list(THEMES.keys()))
    ap.add_argument("--output", default="generated_video.mp4")
    ap.add_argument("--rate", default="+0%")
    ap.add_argument("--volume", default="+0%")
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--list-themes", action="store_true")
    ap.add_argument("--list-voices", action="store_true")
    ap.add_argument("--list-motions", action="store_true")
    args = ap.parse_args()

    if args.list_themes:
        print("Themes:", ", ".join(THEMES.keys()))
        return
    if args.list_voices:
        print("\n".join(VOICES))
        return
    if args.list_motions:
        print("Motions:", ", ".join(MOTIONS))
        return

    ffmpeg = args.ffmpeg or find_ffmpeg()
    if not ffmpeg:
        sys.exit("ffmpeg not found. Install it (e.g. `sudo apt install ffmpeg`) or set FFMPEG=/path/to/ffmpeg.")
    print("ffmpeg:", ffmpeg)

    # Prepend the title as a dedicated title scene (with an intro zoom).
    parsed = [{"text": args.title, "title": True, "motion": "zoom_in", "media": None}] + parse_scenes(args.script)
    scenes = []
    auto_i = 0
    for i, sc in enumerate(parsed):
        sc = dict(sc)
        sc["_i"] = i
        sc["_theme"] = args.theme
        sc["_seed"] = i
        if not sc.get("motion"):
            motion = "zoom_in" if i == 0 else AUTO_MOTIONS[auto_i % len(AUTO_MOTIONS)]
            auto_i += 1
            sc["motion"] = motion
        if sc.get("media"):
            sc["media"] = resolve_media(sc["media"])
            if not sc["media"]:
                print(f"  ! media not found for scene {i}: using gradient.")
                sc["media"] = None
        scenes.append(sc)

    print(f"Scenes: {len(scenes)}")
    for sc in scenes:
        print(f"  [{sc['_i']}] {'TITLE ' if sc['title'] else ''}{sc['motion']:8s} "
              f"{sc['text'][:40]}{'  :: ' + sc['media'] if sc['media'] else ''}")

    with tempfile.TemporaryDirectory(prefix="aivideo_") as td:
        td = Path(td)
        for sc in scenes:
            sc["_overlay"] = render_text_overlay(sc["text"], args.theme, title=sc["title"])

            mp3 = td / f"voice_{sc['_i']:03d}.mp3"
            ok = speak(sc["text"], args.voice, mp3) if sc["text"].strip() else False
            sc["audio"] = mp3 if ok else None

            if ok:
                dur = audio_duration(mp3, ffmpeg) or (len(sc["text"]) / 15.0)
            else:
                dur = (len(sc["text"]) / 15.0 + 0.5)
            if sc["title"]:
                dur += 1.0
            sc["duration"] = round(max(1.2, dur + 0.4), 3)
            print(f"  -> [{sc['_i']}] {sc['duration']:.2f}s")

        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        build_video(scenes, args.voice, args.theme, out, ffmpeg, td)

    print(f"✅ Done -> {out}  ({out.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
