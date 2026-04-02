# vid2md

<p align="center">
  <strong>Convert video tutorials to structured Markdown documents</strong><br>
  Timestamped subtitles · scene screenshots · OCR · AI frame descriptions
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge" alt="Python">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey?style=for-the-badge" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="License">
</p>

vid2md downloads a video from YouTube, Bilibili, or a local file, extracts a transcript, captures key frames, runs OCR on screen content, and optionally describes each frame with a local vision model — producing a single, navigable Markdown document.

---

## Contents

- [Output](#output)
- [Quick Start](#quick-start)
- [Processing Modes](#processing-modes)
- [Architecture](#architecture)
- [Long Video Processing](#long-video-processing)
- [Phase 2 — Knowledge Extraction](#phase-2--knowledge-extraction)
- [Configuration](#configuration)
- [Installation](#installation)
- [Troubleshooting](#troubleshooting)

---

## Output

```
output/<video-title>/
├── tutorial.md      ← main document
└── frames/
    ├── frame_0000.jpg
    ├── frame_0036.jpg
    └── ...
```

Each section of `tutorial.md` contains:
- Timestamp header
- Subtitle text for that segment
- Embedded screenshot
- OCR text extracted from the frame
- (optional) AI description of the frame

---

## Quick Start

```bash
# Chinese video (Bilibili)
MODELSCOPE_CACHE=/tmp/ms_models python3 vid2md.py "https://b23.tv/xxx" --lang zh

# English video (YouTube)
python3 vid2md.py "https://youtube.com/watch?v=xxx" --lang en

# Local video file
MODELSCOPE_CACHE=/tmp/ms_models python3 vid2md.py /path/to/video.mp4 --lang zh

# Fast mode (subtitles + screenshots only, no OCR or AI descriptions)
python3 vid2md.py "URL" --lang en --no-ocr --no-desc
```

---

## Processing Modes

| Mode | Flags | Time (10-min video) | Output |
|------|-------|-------------------|--------|
| Fast | `--no-ocr` | ~1 min | subtitles + screenshots |
| Standard | `--no-desc` | ~5 min | subtitles + screenshots + OCR |
| Full | _(default)_ | ~40 min | subtitles + screenshots + OCR + AI descriptions |

---

## Architecture

```
Input (URL or file)
       │
       ▼
  yt-dlp download
       │
       ├──► audio extraction (ffmpeg)
       │         │
       │         ▼
       │    Transcription
       │    ├── Chinese: FunASR Paraformer (ModelScope)
       │    │           → fallback: faster-whisper
       │    └── English: mlx-whisper (Apple Silicon)
       │                → fallback: faster-whisper
       │
       ├──► frame extraction (ffmpeg)
       │    ├── scene-change detection (PySceneDetect / ffmpeg)
       │    └── fixed-interval fill (--interval, default 30s)
       │         │
       │         ├──► OCR (wechat-ocr or macOS Vision)
       │         └──► AI description (local Ollama VLM, optional)
       │
       └──► Markdown assembly
                 └── output/<title>/tutorial.md
```

### Transcription backends

| Language | Primary | Fallback |
|----------|---------|---------|
| Chinese | FunASR Paraformer (ModelScope) | faster-whisper |
| English | mlx-whisper (Apple Silicon native) | faster-whisper |
| Other | faster-whisper | — |

### OCR backend

By default uses `wechat-ocr` (local macOS binary, free, Chinese + English). Configure the path via `WECHAT_OCR_BIN` environment variable. Falls back to macOS Vision framework if unavailable.

### AI frame description

Uses a local [Ollama](https://ollama.com) instance with a vision model (default: `qwen2.5vl:7b`). Configure via environment variables:

```bash
OLLAMA_DESC_HOST=http://localhost:11434   # Ollama endpoint
OLLAMA_DESC_MODEL=qwen2.5vl:7b           # vision model
```

Skip with `--no-desc` if no Ollama instance is available.

---

## Long Video Processing

For videos over 30 minutes, FunASR may run out of memory. The recommended approach is to split, process each segment, then merge.

```bash
# Split into 20-minute segments, process each, merge output
scripts/process_long_video.sh /path/to/video.mp4 /path/to/output 20
```

### Recommended segment sizes

| Video duration | Segment size |
|---------------|-------------|
| 30–60 min | 20 min |
| 60–120 min | 30 min |
| 120 min+ | 40 min |

---

## Phase 2 — Knowledge Extraction

After generating `tutorial.md`, a second pass with a local LLM can produce a structured `knowledge.md` — distilling key concepts, steps, and parameters from the raw transcript output.

Requirements:
- A local LLM server (e.g. Ollama with a large language model)
- `scripts/phase2_batch.py` — handles chunked processing for long documents

```bash
python3 scripts/phase2_batch.py \
  --input output/<title>/tutorial.md \
  --output output/<title>/knowledge.md \
  --host http://localhost:11434 \
  --model <your-model>
```

The script processes in 10,000-character chunks to avoid context limits, then merges the results.

---

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MODELSCOPE_CACHE` | `~/.cache/modelscope` | Cache directory for FunASR models |
| `WECHAT_OCR_BIN` | `~/bin/wechat-ocr` | Path to wechat-ocr binary |
| `OLLAMA_DESC_HOST` | `http://localhost:11434` | Ollama endpoint for frame descriptions |
| `OLLAMA_DESC_MODEL` | `qwen2.5vl:7b` | Vision model for frame descriptions |

### CLI options

```
usage: vid2md.py [-h] [--lang {zh,en,auto}] [--output OUTPUT]
                 [--scene-threshold SCENE_THRESHOLD] [--interval INTERVAL]
                 [--no-ocr] [--no-desc] [--verbose]
                 input

positional arguments:
  input                 URL or local video file path

options:
  --lang {zh,en,auto}   transcript language (default: zh)
  --output OUTPUT       output directory (default: ./output/<title>)
  --scene-threshold N   scene change sensitivity; higher = fewer frames (default: 8)
  --interval N          fixed-interval fallback in seconds (default: 30)
  --no-ocr              skip OCR step
  --no-desc             skip AI frame description step
  --verbose             enable debug logging
```

### Scene threshold guide

| Video type | `--scene-threshold` |
|-----------|-------------------|
| Screen recordings, UI demos | 8 (default) |
| Talking-head / lecture | 12–15 |
| High-motion video | 20+ |

---

## Installation

```bash
git clone https://github.com/OttoPrua/vid2md.git
cd vid2md
pip install -r requirements.txt
brew install ffmpeg           # macOS
ollama pull qwen2.5vl:7b     # optional: for AI descriptions
```

### Optional: wechat-ocr

A local OCR binary for Chinese + English text. If unavailable, vid2md falls back to macOS Vision framework (macOS only) or skips OCR.

Set the path via environment variable:
```bash
export WECHAT_OCR_BIN=/path/to/wechat-ocr
```

---

## Troubleshooting

**Chinese transcript empty / detected as English**
FunASR failed to load. Check that `MODELSCOPE_CACHE` is set and the model files exist. The first run downloads ~300 MB.

**English model download slow**
Set `HF_HUB_OFFLINE=0` and ensure network access to huggingface.co. Using a VPN may help in some regions.

**No AI descriptions in output**
Ollama is not running or the vision model is not loaded. Run `ollama list` to verify, or add `--no-desc` to skip.

**Too many / too few screenshots**
Adjust `--scene-threshold` (higher = fewer frames) or `--interval` (higher = fewer fill frames).

---

## Related

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — video download
- [FunASR](https://github.com/modelscope/FunASR) — Chinese ASR
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — fallback ASR
- [Ollama](https://ollama.com) — local LLM/VLM runtime
- [OpenClaw](https://github.com/openclaw/openclaw) — the agent framework this skill runs in

## License

MIT
