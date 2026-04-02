#!/usr/bin/env python3
"""vid2md — Convert video tutorials to structured Markdown documents."""

from __future__ import annotations

import argparse
import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("vid2md")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fmt_ts(seconds: float) -> str:
    """Format seconds as HH:MM:SS or MM:SS."""
    h = int(seconds) // 3600
    m = (int(seconds) % 3600) // 60
    s = int(seconds) % 60
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def fmt_ts_file(seconds: float) -> str:
    """Format seconds for filenames: frame_0030."""
    return f"frame_{int(seconds):04d}"


def run(cmd: list[str], *, check: bool = True, capture: bool = True, **kw) -> subprocess.CompletedProcess:
    """Run a subprocess with logging."""
    log.debug("Running: %s", " ".join(cmd))
    return subprocess.run(cmd, check=check, capture_output=capture, text=True, **kw)


def check_dependency(name: str, install_hint: str) -> None:
    """Check that a CLI dependency is available."""
    if shutil.which(name) is None:
        print(f"Error: '{name}' not found on PATH.\n  Install: {install_hint}", file=sys.stderr)
        sys.exit(1)


def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_video(url: str, tmp_dir: str) -> tuple[str, str]:
    """Download video via yt-dlp. Returns (video_path, title)."""
    print(f"[download] Downloading: {url}")
    check_dependency("yt-dlp", "pip install yt-dlp")

    # Get metadata first
    meta_cmd = ["yt-dlp", "--dump-json", "--no-download", url]
    result = run(meta_cmd)
    meta = json.loads(result.stdout)
    title: str = meta.get("title", "video")
    # sanitise title for filesystem
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)

    out_template = os.path.join(tmp_dir, f"{safe_title}.%(ext)s")
    dl_cmd = [
        "yt-dlp",
        "-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        url,
    ]
    run(dl_cmd)

    # Find downloaded file
    for f in Path(tmp_dir).iterdir():
        if f.suffix in (".mp4", ".mkv", ".webm", ".flv"):
            return str(f), title

    raise FileNotFoundError("yt-dlp did not produce a video file")


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------

def extract_audio(video_path: str, out_wav: str) -> None:
    """Extract 16 kHz mono WAV from video."""
    print("[audio] Extracting audio …")
    check_dependency("ffmpeg", "brew install ffmpeg  OR  apt install ffmpeg")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
        out_wav,
    ]
    run(cmd)
    print(f"[audio] Saved: {out_wav}")


def _load_wav(path: str) -> tuple:
    """Load WAV raw bytes. Returns (raw_bytes, sample_rate, n_channels, sample_width)."""
    with wave.open(path, 'rb') as wf:
        return wf.readframes(wf.getnframes()), wf.getframerate(), wf.getnchannels(), wf.getsampwidth()


def _write_wav_segment(out_path: str, raw: bytes, sr: int, n_ch: int, sw: int, start: float, end: float) -> None:
    """Write a time-slice of raw PCM bytes to a new WAV file."""
    bps = sw * n_ch
    s = max(0, int(start * sr) * bps)
    e = min(len(raw), int(end * sr) * bps)
    with wave.open(out_path, 'wb') as wf:
        wf.setnchannels(n_ch)
        wf.setsampwidth(sw)
        wf.setframerate(sr)
        wf.writeframes(raw[s:e])


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------

def extract_frames_interval(video_path: str, out_dir: str, interval: int) -> list[float]:
    """Extract frames at fixed intervals. Returns list of timestamps."""
    print(f"[frames] Extracting frames every {interval}s …")
    check_dependency("ffmpeg", "brew install ffmpeg  OR  apt install ffmpeg")

    # Get duration
    probe = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ])
    duration = float(probe.stdout.strip())

    timestamps: list[float] = []
    t = 0.0
    while t < duration:
        out_file = os.path.join(out_dir, f"{fmt_ts_file(t)}.jpg")
        cmd = [
            "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
            "-frames:v", "1", "-q:v", "2", out_file,
        ]
        run(cmd)
        timestamps.append(t)
        t += interval

    print(f"[frames] Extracted {len(timestamps)} interval frames")
    return timestamps


def extract_frames_scene(video_path: str, out_dir: str, threshold: float = 10.0) -> list[float]:
    """Extract frames at scene changes using scdet filter. Returns list of timestamps."""
    print("[frames] Detecting scene changes …")
    check_dependency("ffmpeg", "brew install ffmpeg  OR  apt install ffmpeg")

    # Use scdet filter to detect scene changes, output metadata to stdout
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vf", f"scdet=threshold={threshold},metadata=print:file=-",
        "-f", "null", "-",
    ]
    result = run(cmd, capture=True, check=False)

    scene_times: list[float] = []
    # Parse "lavfi.scd.time=427.9" from stdout
    for line in result.stdout.splitlines():
        m = re.search(r"lavfi\.scd\.time=([0-9.]+)", line)
        if m:
            scene_times.append(float(m.group(1)))

    # Also check stderr for the same pattern (ffmpeg version differences)
    for line in result.stderr.splitlines():
        m = re.search(r"lavfi\.scd\.time=([0-9.]+)", line)
        if m:
            t = float(m.group(1))
            if t not in scene_times:
                scene_times.append(t)

    scene_times = sorted(set(scene_times))

    # Extract frames at detected scene change timestamps
    for t in scene_times:
        out_file = os.path.join(out_dir, f"{fmt_ts_file(t)}.jpg")
        if not os.path.exists(out_file):
            run([
                "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
                "-frames:v", "1", "-q:v", "2", out_file,
            ], check=False)
            log.debug("Saved scene frame: %s", out_file)

    print(f"[frames] Found {len(scene_times)} scene-change frames")
    return scene_times


def merge_timestamps(scene_ts: list[float], interval_ts: list[float], min_gap: float = 5.0) -> list[float]:
    """Merge timestamps: scene frames PRIMARY, interval frames fill gaps only."""
    combined = sorted(set(scene_ts))
    for it in interval_ts:
        if all(abs(it - st) > min_gap for st in combined):
            combined.append(it)
    return sorted(combined)


_WECHAT_OCR_BIN = os.environ.get("WECHAT_OCR_BIN", os.path.expanduser("~/bin/wechat-ocr"))

def ocr_frame(image_path: str) -> str:
    """OCR a frame image using wechat-ocr (local, free, Chinese+English)."""
    try:
        if not os.path.exists(_WECHAT_OCR_BIN):
            log.debug("wechat-ocr not found at %s", _WECHAT_OCR_BIN)
            return ""
        result = subprocess.run(
            [_WECHAT_OCR_BIN, image_path],
            capture_output=True, text=True, timeout=15
        )
        lines = []
        for line in result.stdout.splitlines():
            line = line.strip()
            # 过滤噪声行（单个符号、空行、"Idle"状态行）
            if line and len(line) > 1 and line not in ("Idle", "Ready"):
                lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        log.debug("OCR failed for %s: %s", image_path, e)
        return ""


def describe_frame(image_path: str) -> str:
    """Describe frame using vision LLM. Priority: 5090 (CUDA) → Mac (Metal) → skip."""
    import base64, json as _json, urllib.request as _req, os as _os

    # 端点优先级：环境变量覆盖 > Mac 本地 > 5090（5090仅当环境变量指定时使用）
    default_host = _os.environ.get("OLLAMA_DESC_HOST", "http://localhost:11434")
    default_model = _os.environ.get("OLLAMA_DESC_MODEL", "qwen2.5vl:7b")
    endpoints = [
        (default_host, default_model),
        ("http://localhost:11434", "qwen2.5vl:7b"),
        ("http://localhost:11434", "qwen3.5:9b"),
    ]

    try:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        log.debug("Failed to read image %s: %s", image_path, e)
        return ""

    for host, model in endpoints:
        try:
            payload = _json.dumps({
                "model": model,
                "messages": [{
                    "role": "user",
                    "content": "请用1-2句中文简洁描述这张截图的主要内容（界面、操作步骤、展示的参数等）。直接给描述，不要加前缀。",
                    "images": [img_b64]
                }],
                "stream": False
            }).encode()
            req = _req.Request(
                f"{host}/api/chat",
                data=payload,
                headers={"Content-Type": "application/json"}
            )
            with _req.urlopen(req, timeout=90) as resp:
                data = _json.loads(resp.read())
            result = data.get("message", {}).get("content", "").strip()
            if result:
                log.debug("DESC via %s/%s: %s", host, model, result[:60])
                return result
        except Exception as e:
            log.debug("DESC endpoint %s/%s failed: %s", host, model, e)
            continue
    return ""


def transcribe_segment(seg_path: str, lang: str, funasr_model=None) -> str:
    """Transcribe a single audio segment WAV file. Returns text."""
    try:
        if lang == "zh" and funasr_model is not None:
            result = funasr_model.generate(input=seg_path, batch_size_s=300)
            if result and len(result) > 0:
                return result[0].get("text", "").strip()
            return ""
        if sys.platform == "darwin" and not funasr_model:
            try:
                import mlx_whisper
                res = mlx_whisper.transcribe(
                    seg_path,
                    path_or_hf_repo="mlx-community/whisper-large-v3-mlx",
                    language=lang if lang != "auto" else None,
                    verbose=False,
                )
                return " ".join(s["text"].strip() for s in res.get("segments", []))
            except ImportError:
                pass
        from faster_whisper import WhisperModel
        model = WhisperModel("large-v3", compute_type="int8")
        segs, _ = model.transcribe(seg_path, beam_size=5, language=lang if lang != "auto" else None)
        return " ".join(s.text.strip() for s in segs)
    except Exception as e:
        log.warning("Segment transcription failed: %s", e)
        return ""


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def detect_language(audio_path: str) -> str:
    """Detect language from the first 30s of audio using faster-whisper."""
    print("[lang] Detecting language …")
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel("large-v3", compute_type="int8")
        segments, info = model.transcribe(audio_path, beam_size=1, language=None)
        # consume generator to get info
        _ = next(segments, None)
        lang = info.language
        print(f"[lang] Detected: {lang} (probability: {info.language_probability:.2f})")
        return lang
    except ImportError:
        print("[lang] faster-whisper not available, defaulting to 'en'")
        return "en"
    except Exception as e:
        log.warning("Language detection failed: %s", e)
        print(f"[lang] Detection failed ({e}), defaulting to 'en'")
        return "en"


def transcribe_mlx_whisper(audio_path: str, model_name: str) -> list[dict]:
    """Transcribe with mlx-whisper. Returns list of {start, end, text}."""
    print(f"[transcribe] Using mlx-whisper ({model_name}) …")
    try:
        import mlx_whisper
    except ImportError:
        print("Error: mlx-whisper is not installed.\n  Install: pip install mlx-whisper", file=sys.stderr)
        sys.exit(1)

    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model_name,
        verbose=False,
    )
    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"].strip(),
        })
    return segments


def transcribe_funasr(audio_path: str) -> Optional[list[dict]]:
    """Try transcription with FunASR (Chinese). Returns segments or None."""
    print("[transcribe] Trying FunASR (Chinese) …")
    try:
        from funasr import AutoModel

        model = AutoModel(
            model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
            punc_model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
        )
        res = model.generate(input=audio_path, batch_size_s=300)
        if not res:
            return None

        segments = []
        for item in res:
            text = item.get("text", "").strip()
            timestamps = item.get("timestamp", [])  # [[start_ms, end_ms], ...]

            if not text:
                continue

            if timestamps:
                start_sec = timestamps[0][0] / 1000.0
                end_sec = timestamps[-1][1] / 1000.0
            else:
                start_sec = item.get("start", 0)
                end_sec = item.get("end", 0)

            # Split into sentences and distribute time proportionally
            sentences = re.split(r"(?<=[。！？])", text)
            sentences = [s.strip() for s in sentences if s.strip()]

            if not sentences:
                sentences = [text]

            duration = end_sec - start_sec
            char_count = len(text)

            char_pos = 0
            for sent in sentences:
                sent_start = start_sec + (char_pos / char_count) * duration if char_count > 0 else start_sec
                char_pos += len(sent)
                sent_end = start_sec + (char_pos / char_count) * duration if char_count > 0 else end_sec
                segments.append({"start": sent_start, "end": sent_end, "text": sent})

        return segments if segments else None
    except ImportError:
        print("[transcribe] FunASR not available, falling back")
        return None
    except Exception as e:
        log.warning("FunASR failed: %s", e)
        print(f"[transcribe] FunASR failed ({e}), falling back")
        return None


def transcribe_faster_whisper(audio_path: str, model_name: str = "large-v3", language: Optional[str] = None) -> list[dict]:
    """Transcribe with faster-whisper. Returns list of {start, end, text}."""
    print(f"[transcribe] Using faster-whisper ({model_name}) …")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("Error: faster-whisper is not installed.\n  Install: pip install faster-whisper", file=sys.stderr)
        sys.exit(1)

    model = WhisperModel(model_name, compute_type="int8")
    segs, _info = model.transcribe(audio_path, beam_size=5, language=language)

    segments = []
    for seg in segs:
        segments.append({
            "start": seg.start,
            "end": seg.end,
            "text": seg.text.strip(),
        })
    return segments


def fix_terminology(text: str) -> str:
    """Fix common ASR misrecognitions for AI/tech terminology."""
    replacements = [
        # LoRA
        (r"\b[Ll]o[ao]r[a-z]*\b", "LoRA"),
        (r"路软", "LoRA"),
        (r"罗[aA]\b", "LoRA"),
        (r"[Ll]oon\s*[Ll]et", "LoRA"),
        (r"\bloura\b", "LoRA"),
        (r"\blor\b", "LoRA"),
        # Grok
        (r"[Gg]rou\s*[cC][kK]", "Grok"),
        (r"[Gg]ro\s*[oO]?[cC][kK]", "Grok"),
        # NSFW
        (r"\bASFW\b", "NSFW"),
        (r"\bASAFR\b", "NSFW"),
        # SVD/SVI
        (r"\bSVI\b", "SVD"),
        # ComfyUI numbers
        (r"一零二四", "1024"),
        (r"(?<!\d)七二零(?!\d)", "720"),
        (r"(?<!\d)六十四帧", "64帧"),
        (r"(?<!\d)二点一(?!\d)", "2.1"),
        (r"(?<!\d)十三个[Gg](?!\d)", "13GB"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    return text


def transcribe(audio_path: str, lang: str, force_whisper: bool, model_override: Optional[str]) -> list[dict]:
    """Run transcription based on language and available backends."""
    if force_whisper or model_override:
        model_name = model_override or "large-v3"
        return transcribe_faster_whisper(audio_path, model_name=model_name, language=lang if lang != "auto" else None)

    if lang == "en" and sys.platform == "darwin":
        model_name = model_override or "mlx-community/whisper-large-v3-mlx"
        return transcribe_mlx_whisper(audio_path, model_name)

    if lang == "zh":
        # Try FunASR first, fall back to faster-whisper
        result = transcribe_funasr(audio_path)
        if result is not None:
            return result
        return transcribe_faster_whisper(audio_path, language="zh")

    # Default: faster-whisper for other languages
    return transcribe_faster_whisper(audio_path, language=lang if lang != "auto" else None)


# ---------------------------------------------------------------------------
# Markdown generation
# ---------------------------------------------------------------------------

def find_silence_gaps(segments: list[dict], threshold: float = 3.0) -> list[float]:
    """Find silence gaps > threshold between segments. Returns break timestamps."""
    breaks: list[float] = []
    for i in range(1, len(segments)):
        gap = segments[i]["start"] - segments[i - 1]["end"]
        if gap >= threshold:
            breaks.append(segments[i]["start"])
    return breaks


def build_section_breaks(segments: list[dict], scene_times: list[float]) -> list[float]:
    """Determine section break points from silence gaps and scene changes."""
    silence = find_silence_gaps(segments)
    combined = set(silence)
    for t in scene_times:
        # Only add scene break if not too close to existing break
        if all(abs(t - b) > 10.0 for b in combined):
            combined.add(t)
    breaks = sorted(combined)
    # Always include 0.0 as the first section
    if not breaks or breaks[0] > 1.0:
        breaks.insert(0, 0.0)
    return breaks


def generate_markdown(
    title: str,
    source: str,
    duration: float,
    lang: str,
    segments: list[dict],
    frame_timestamps: list[float],
    scene_times: list[float],
    frames_dir: str,
    frames_rel: str,
) -> str:
    """Generate the Markdown document."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines: list[str] = []

    lines.append(f"# {title}\n")
    lines.append(f"> Source: {source}  ")
    lines.append(f"> Duration: {fmt_ts(duration)}  ")
    lines.append(f"> Language: {lang}  ")
    lines.append(f"> Generated: {now}\n")
    lines.append("---\n")

    # Determine section breaks
    breaks = build_section_breaks(segments, scene_times)

    # Assign segments and frames to sections
    section_idx = 0
    for bi, break_time in enumerate(breaks):
        next_break = breaks[bi + 1] if bi + 1 < len(breaks) else float("inf")

        # Section header
        label = "Introduction" if bi == 0 else f"Section {bi}"
        lines.append(f"## [{fmt_ts(break_time)}] {label}\n")

        # Collect segments in this section
        section_segs = [
            s for s in segments
            if s["start"] >= break_time and s["start"] < next_break
        ]

        # Collect frames in this section
        section_frames = [
            t for t in frame_timestamps
            if t >= break_time and t < next_break
        ]

        # Interleave text and frames
        frame_iter = iter(section_frames)
        next_frame = next(frame_iter, None)

        for seg in section_segs:
            # Insert any frames that come before this segment
            while next_frame is not None and next_frame <= seg["start"]:
                fname = f"{fmt_ts_file(next_frame)}.jpg"
                fpath = os.path.join(frames_dir, fname)
                if os.path.exists(fpath):
                    lines.append(f"![Frame at {fmt_ts(next_frame)}]({frames_rel}/{fname})\n")
                next_frame = next(frame_iter, None)

            lines.append(fix_terminology(seg["text"]) + "\n")

        # Any remaining frames in section
        while next_frame is not None and next_frame < next_break:
            fname = f"{fmt_ts_file(next_frame)}.jpg"
            fpath = os.path.join(frames_dir, fname)
            if os.path.exists(fpath):
                lines.append(f"![Frame at {fmt_ts(next_frame)}]({frames_rel}/{fname})\n")
            next_frame = next(frame_iter, None)

        lines.append("---\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Video duration helper
# ---------------------------------------------------------------------------

def get_duration(video_path: str) -> float:
    """Get video duration in seconds."""
    probe = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", video_path,
    ])
    return float(probe.stdout.strip())


# ---------------------------------------------------------------------------
# Markdown builder v2
# ---------------------------------------------------------------------------

def build_markdown_v2(
    title: str,
    source: str,
    duration: float,
    lang: str,
    segments: list,
    frames_dir: str,
    frame_ocr: dict,
    frame_desc: dict = None,
) -> str:
    """Build markdown: per-segment frame + OCR block + transcript text."""
    lines = [
        f"# {title}\n",
        f"> Source: {source}  ",
        f"> Duration: {fmt_ts(duration)}  ",
        f"> Language: {lang}  ",
        f"> Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
        "---\n",
    ]
    for i, seg in enumerate(segments):
        ts = seg["start"]
        frame_ts = seg.get("frame_ts")
        text = seg.get("text", "")
        label = "Introduction" if i == 0 else f"Section {i}"
        lines.append(f"## [{fmt_ts(ts)}] {label}\n")

        if frame_ts is not None:
            img_name = f"{fmt_ts_file(frame_ts)}.jpg"
            img_full = os.path.join(frames_dir, img_name)
            if os.path.exists(img_full):
                lines.append(f"![Frame at {fmt_ts(frame_ts)}](frames/{img_name})\n")
                # AI vision description
                if frame_desc:
                    desc = frame_desc.get(frame_ts, "")
                    if desc:
                        lines.append(f"> 🖼️ **画面描述：** {desc}\n")
                # OCR text
                ocr_text = frame_ocr.get(frame_ts, "")
                if ocr_text:
                    lines.append("> **📸 画面文字：**")
                    for ol in ocr_text.strip().splitlines():
                        if ol.strip():
                            lines.append(f"> {ol.strip()}")
                    lines.append("")

        if text:
            lines.append(text)
            lines.append("")

        lines.append("---\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="vid2md",
        description="Convert video tutorials to structured Markdown documents.",
    )
    parser.add_argument("input", help="Video file path, YouTube URL, or Bilibili URL")
    parser.add_argument("-o", "--output", default=None, help="Output directory (default: ./output/<video_title>)")
    parser.add_argument("-i", "--interval", type=int, default=30, help="Frame capture interval in seconds (default: 30)")
    parser.add_argument("--lang", default="auto", choices=["zh", "en", "auto"], help="Force language (default: auto)")
    parser.add_argument("--model", default=None, help="Override STT model")
    parser.add_argument("--no-frames", action="store_true", help="Skip frame extraction")
    parser.add_argument("--no-ocr", action="store_true", help="Skip OCR and vision description on frames")
    parser.add_argument("--no-desc", action="store_true", help="Skip vision description (keep OCR)")
    parser.add_argument("--whisper-only", action="store_true", help="Force whisper for all languages")
    parser.add_argument("--scene-threshold", type=float, default=10.0, help="Scene detection threshold (default: 10.0). Lower = more sensitive")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    # Logging setup
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    )

    # Temp directory with cleanup
    tmp_dir = tempfile.mkdtemp(prefix="vid2md_")
    atexit.register(lambda: shutil.rmtree(tmp_dir, ignore_errors=True))
    log.debug("Temp dir: %s", tmp_dir)

    # Step 1: Resolve input
    input_val: str = args.input
    video_path: str
    title: str
    source: str = input_val

    if is_url(input_val):
        try:
            video_path, title = download_video(input_val, tmp_dir)
        except subprocess.CalledProcessError as e:
            print(f"Error: yt-dlp download failed.\n{e.stderr}", file=sys.stderr)
            sys.exit(1)
        except FileNotFoundError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        video_path = os.path.abspath(input_val)
        if not os.path.isfile(video_path):
            print(f"Error: File not found: {video_path}", file=sys.stderr)
            sys.exit(1)
        title = Path(video_path).stem

    print(f"[vid2md] Title: {title}")

    # Output directory
    safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
    out_dir = args.output or os.path.join("output", safe_title)
    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, "frames")
    if not args.no_frames:
        os.makedirs(frames_dir, exist_ok=True)

    # Step 2: Extract audio (skip if no audio stream)
    audio_path = os.path.join(tmp_dir, "audio.wav")
    _has_audio = run(["ffprobe", "-v", "error", "-select_streams", "a",
                      "-show_entries", "stream=codec_type",
                      "-of", "csv=p=0", video_path], capture=True).stdout.strip()
    has_audio = bool(_has_audio)
    if has_audio:
        extract_audio(video_path, audio_path)
    else:
        print("[audio] No audio stream detected, skipping transcription")

    # Step 3: Get duration
    duration = get_duration(video_path)
    print(f"[vid2md] Duration: {fmt_ts(duration)}")

    # Step 4: Extract frames (scene-first)
    frame_timestamps: list[float] = []
    scene_times: list[float] = []
    frame_ocr: dict = {}
    frame_desc: dict = {}
    if not args.no_frames:
        scene_times = extract_frames_scene(video_path, frames_dir, threshold=args.scene_threshold)
        interval_ts = extract_frames_interval(video_path, frames_dir, args.interval)
        frame_timestamps = merge_timestamps(scene_times, interval_ts, min_gap=args.interval * 0.8)
        print(f"[frames] Total unique frames: {len(frame_timestamps)} (scene={len(scene_times)}, interval fill={len(frame_timestamps)-len(scene_times)})")

        # OCR + Vision describe all frames
        if not args.no_ocr:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            n = len(frame_timestamps)
            print(f"[ocr] Running OCR on {n} frames (parallel) …")

            # Phase 1: OCR — fast local binary, safe to parallelize
            def do_ocr(ts):
                img_path = os.path.join(frames_dir, f"{fmt_ts_file(ts)}.jpg")
                if not os.path.exists(img_path):
                    return ts, ""
                return ts, ocr_frame(img_path)

            with ThreadPoolExecutor(max_workers=8) as executor:
                for ts, text in executor.map(do_ocr, frame_timestamps):
                    if text:
                        frame_ocr[ts] = text
            print(f"[ocr] OCR done: {len(frame_ocr)}/{n} frames with text")

            # Phase 2: Vision description — Ollama serial (one at a time)
            if not args.no_desc:
                print(f"[desc] Running vision descriptions on {n} frames (serial, ~{n*30//60}min) …")
                for i, ts in enumerate(frame_timestamps, 1):
                    img_path = os.path.join(frames_dir, f"{fmt_ts_file(ts)}.jpg")
                    if os.path.exists(img_path):
                        desc = describe_frame(img_path)
                        if desc:
                            frame_desc[ts] = desc
                    if i % 5 == 0 or i == n:
                        print(f"[desc] {i}/{n} …")
                print(f"[desc] Done: {len(frame_desc)}/{n} frames described")
            else:
                print("[desc] Skipped (--no-desc)")
            print(f"[ocr] Summary: OCR={len(frame_ocr)}, DESC={len(frame_desc)}")
    else:
        print("[frames] Skipped (--no-frames)")

    # Step 5: Language detection
    lang = args.lang
    if has_audio and lang == "auto":
        lang = detect_language(audio_path)
    print(f"[vid2md] Language: {lang}")

    # Step 6: Per-segment transcription (cut points = frame timestamps)
    cut_points = sorted(set([0.0] + list(frame_timestamps) + [duration]))
    frame_ts_set = set(frame_timestamps)
    segments = []

    if not has_audio:
        print("[transcribe] Skipped (no audio stream) — generating frame-only segments")
        for i in range(len(cut_points) - 1):
            seg_start = cut_points[i]
            seg_end = cut_points[i + 1]
            frame_ts = seg_start if seg_start in frame_ts_set else None
            segments.append({"start": seg_start, "end": seg_end, "text": "", "frame_ts": frame_ts})
    else:
        if lang == "zh" and not args.whisper_only:
            print("[transcribe] Loading FunASR model …")
            try:
                from funasr import AutoModel
                funasr_model = AutoModel(
                    model="iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
                    vad_model="iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
                    punc_model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
                    disable_update=True,
                )
            except Exception as e:
                print(f"[transcribe] FunASR load failed: {e}, falling back to faster-whisper (keeping lang={lang})")
                funasr_model = None
        else:
            funasr_model = None

        # Load audio once, slice per segment via wave module
        raw_audio, sr, n_ch, sw = _load_wav(audio_path)

        n_segs = len(cut_points) - 1
        print(f"[transcribe] Transcribing {n_segs} segments …")
        for i in range(n_segs):
            seg_start = cut_points[i]
            seg_end = cut_points[i + 1]
            frame_ts = seg_start if seg_start in frame_ts_set else None

            seg_path = os.path.join(tmp_dir, f"seg_{i:04d}.wav")
            _write_wav_segment(seg_path, raw_audio, sr, n_ch, sw, seg_start, seg_end)

            text = transcribe_segment(seg_path, lang, funasr_model)
            text = fix_terminology(text) if text else ""
            segments.append({"start": seg_start, "end": seg_end, "text": text, "frame_ts": frame_ts})

        print(f"[transcribe] Done. {sum(1 for s in segments if s['text'])} / {len(segments)} segments have text.")

    # Step 7: Build Markdown v2
    md_content = build_markdown_v2(title, source, duration, lang, segments, frames_dir, frame_ocr, frame_desc)
    md_path = os.path.join(out_dir, "tutorial.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)

    print(f"\n[vid2md] Done! Output: {md_path}")
    print(f"[vid2md] Frames: {frames_dir}")


if __name__ == "__main__":
    main()
