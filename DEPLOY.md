---
name: deploy-vid2md
description: Interactive deployment guide for vid2md. Clones the repository, installs Python dependencies, configures transcription models (FunASR/Whisper), sets up OCR, optionally pulls a vision model for AI frame descriptions, and runs a smoke test.
---

# Deploy: vid2md

> **Interactive protocol.** Each step checks current state before acting. Pauses for confirmation where needed.

---

## Before Starting

```bash
# Check prerequisites
which git && git --version
which python3 && python3 --version
which ffmpeg && ffmpeg -version 2>&1 | head -1
which pip3 && pip3 --version
```

If ffmpeg is missing:
```bash
brew install ffmpeg        # macOS
# sudo apt install ffmpeg  # Debian/Ubuntu
```

---

## Phase 1 — Clone Repository

Ask: "Where should vid2md be installed? (default: `~/Projects/vid2md`)"

Use `<VID2MD_DIR>` as the placeholder.

```bash
# Check if already exists
ls <VID2MD_DIR>/vid2md.py 2>/dev/null && echo "already installed"
```

If not installed:
```bash
mkdir -p "$(dirname <VID2MD_DIR>)"
git clone https://github.com/OttoPrua/vid2md.git <VID2MD_DIR>
```

---

## Phase 2 — Python Dependencies

```bash
cd <VID2MD_DIR>
pip3 install -r requirements.txt
```

Detect platform and install platform-specific packages:

```bash
# Apple Silicon Mac
if [[ "$(uname -m)" == "arm64" && "$(uname)" == "Darwin" ]]; then
  pip3 install mlx-whisper
  echo "✅ mlx-whisper installed (Apple Silicon)"
fi
```

---

## Phase 3 — Transcription Models

Ask: "Will you primarily process Chinese or English videos? (zh / en / both)"

**Chinese (`zh` or `both`)** — download FunASR model on first run:
```bash
MODELSCOPE_CACHE=/tmp/ms_models python3 - << 'EOF'
from funasr import AutoModel
AutoModel(model='paraformer-zh', model_revision='v2.0.4')
print("✅ FunASR model ready")
EOF
```

If FunASR download fails (network issues):
```bash
# Fallback: faster-whisper works for all languages
pip3 install faster-whisper
python3 -c "from faster_whisper import WhisperModel; print('✅ faster-whisper ready')"
```

**English (`en` or `both`) on Apple Silicon:**
```bash
python3 -c "import mlx_whisper; print('✅ mlx-whisper ready')" 2>/dev/null \
  || echo "mlx-whisper not available — faster-whisper will be used as fallback"
```

---

## Phase 4 — OCR Setup

Ask: "Which OCR backend do you want to use? Recommended by platform:
- **Mac (Apple Silicon/Intel)**: macOS Vision (built-in, no setup needed)
- **Mac with wechat-ocr**: best accuracy for Chinese — needs path
- **Linux/Windows**: EasyOCR (GPU) or Tesseract (CPU)"

**Option A — wechat-ocr (macOS, best CJK accuracy):**

Ask for path, then:
```bash
export WECHAT_OCR_BIN=/path/to/wechat-ocr
echo 'export WECHAT_OCR_BIN=/path/to/wechat-ocr' >> ~/.zshrc
```

**Option B — macOS Vision (built-in, no setup):**
```bash
pip3 install pyobjc-framework-Vision pyobjc-framework-Quartz
python3 -c "import Vision; print('✅ macOS Vision ready')"
```

**Option C — EasyOCR (Linux/Windows/macOS):**
```bash
pip3 install easyocr
python3 -c "import easyocr; print('✅ EasyOCR ready')"
```

**Option D — Tesseract (CPU, all platforms):**
```bash
brew install tesseract        # macOS
# sudo apt install tesseract-ocr  # Debian/Ubuntu
pip3 install pytesseract
python3 -c "import pytesseract; print('✅ Tesseract ready')"
```

---

## Phase 5 — AI Frame Descriptions (optional)

Ask: "Install AI frame descriptions? Requires Ollama + ~5 GB disk. (y/n)"

If yes:
```bash
# Check Ollama
which ollama || { echo "Install Ollama from https://ollama.com first"; exit 1; }

# Pull vision model
ollama pull qwen2.5vl:7b
ollama list | grep qwen2.5vl && echo "✅ vision model ready"
```

If no:
> "You can add `--no-desc` to any vid2md command to skip descriptions. You can install Ollama and pull the model later."

---

## Phase 6 — Smoke Test

```bash
cd <VID2MD_DIR>

# Verify CLI works
python3 vid2md.py --help | head -5

# Quick test (short public video, English, no OCR/desc)
python3 vid2md.py \
  "https://www.youtube.com/watch?v=dQw4w9WgXcQ" \
  --lang en --no-ocr --no-desc --interval 60

# Check output
ls output/*/tutorial.md 2>/dev/null && echo "✅ vid2md working" || echo "❌ check errors above"
```

---

## Environment Variables Reference

Set these in `~/.zshrc` or `~/.bashrc` for persistent configuration:

```bash
export MODELSCOPE_CACHE=/path/to/modelscope-cache   # FunASR model cache
export WECHAT_OCR_BIN=/path/to/wechat-ocr           # OCR binary (optional)
export OLLAMA_DESC_HOST=http://localhost:11434        # Ollama endpoint
export OLLAMA_DESC_MODEL=qwen2.5vl:7b               # Vision model
export VID2MD_OCR_BACKEND=vision                     # Force OCR backend
```

---

## Final Check

```bash
python3 <VID2MD_DIR>/vid2md.py --help 2>/dev/null | head -3 && echo "✅ vid2md ready"
ls <VID2MD_DIR>/output/*/tutorial.md 2>/dev/null && echo "✅ smoke test passed"
```

→ Full docs: https://github.com/OttoPrua/vid2md
