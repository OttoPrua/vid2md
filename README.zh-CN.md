# vid2md

<p align="center">
  <strong>将视频教程转换为结构化 Markdown 文档</strong><br>
  时间对齐字幕 · 场景截图 · OCR · AI 画面描述
</p>

<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/python-3.9%2B-blue?style=for-the-badge" alt="Python">
  <img src="https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey?style=for-the-badge" alt="Platform">
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="License">
</p>

vid2md 从 YouTube、B站或本地文件下载视频，提取字幕、截取关键帧、对画面内容 OCR，并可选用本地视觉模型描述每一帧 — 最终输出一份可导航的结构化 Markdown 文档。

---

## 目录

- [输出结果](#输出结果)
- [快速开始](#快速开始)
- [处理模式](#处理模式)
- [架构](#架构)
- [长视频处理](#长视频处理)
- [Phase 2 — 知识提炼](#phase-2--知识提炼)
- [配置](#配置)
- [安装](#安装)
- [常见问题](#常见问题)

---

## 输出结果

```
output/<视频标题>/
├── tutorial.md      ← 主文档
└── frames/
    ├── frame_0000.jpg
    ├── frame_0036.jpg
    └── ...
```

`tutorial.md` 的每个章节包含：
- 时间戳标题
- 该时间段的字幕文本
- 嵌入截图
- 从截图中 OCR 提取的文字
- （可选）AI 对该帧的描述

---

## 快速开始

```bash
# 中文视频（B站）
MODELSCOPE_CACHE=/tmp/ms_models python3 vid2md.py "https://b23.tv/xxx" --lang zh

# 英文视频（YouTube）
python3 vid2md.py "https://youtube.com/watch?v=xxx" --lang en

# 本地视频文件
MODELSCOPE_CACHE=/tmp/ms_models python3 vid2md.py /path/to/video.mp4 --lang zh

# 快速模式（仅字幕 + 截图，跳过 OCR 和 AI 描述）
python3 vid2md.py "URL" --lang zh --no-ocr --no-desc
```

---

## 处理模式

| 模式 | 参数 | 耗时（10分钟视频） | 输出内容 |
|------|------|-----------------|---------|
| 快速 | `--no-ocr` | ~1分钟 | 字幕 + 截图 |
| 标准 | `--no-desc` | ~5分钟 | 字幕 + 截图 + OCR |
| 完整 | 默认 | ~40分钟 | 字幕 + 截图 + OCR + AI描述 |

---

## 架构

```
输入（URL 或本地文件）
       │
       ▼
  yt-dlp 下载
       │
       ├──► 音频提取（ffmpeg）
       │         │
       │         ▼
       │    字幕转录
       │    ├── 中文：FunASR Paraformer（ModelScope）
       │    │        → fallback：faster-whisper
       │    └── 英文：mlx-whisper（Apple Silicon 原生）
       │               → fallback：faster-whisper
       │
       ├──► 帧提取（ffmpeg）
       │    ├── 场景切换检测
       │    └── 固定间隔补帧（--interval，默认30秒）
       │         │
       │         ├──► OCR（wechat-ocr 或 macOS Vision）
       │         └──► AI 描述（本地 Ollama VLM，可选）
       │
       └──► Markdown 组装
                 └── output/<标题>/tutorial.md
```

### 转录后端

| 语言 | 主要 | 备用 |
|------|------|------|
| 中文 | FunASR Paraformer（ModelScope） | faster-whisper |
| 英文 | mlx-whisper（Apple Silicon 原生） | faster-whisper |
| 其他 | faster-whisper | — |

### OCR 后端

vid2md 按优先级依次尝试各 OCR 后端，自动回退：

| 优先级 | 后端 | 平台 | 语言 | 说明 |
|--------|------|------|------|------|
| 1 | **wechat-ocr** | macOS | 中文 + 英文 | 本地二进制，最快，CJK 准确率最高。通过 `WECHAT_OCR_BIN` 配置路径。|
| 2 | **macOS Vision** (`pyobjc-framework-Vision`) | macOS 13+ | 中、英、日、韩 | 系统内置，无需额外安装，准确率良好。|
| 3 | **EasyOCR** | macOS / Linux / Windows | 80+ 种语言 | 支持 CUDA/MPS GPU 加速。安装：`pip install easyocr`。|
| 4 | **Tesseract** | macOS / Linux / Windows | 100+ 种语言 | 纯 CPU，语言覆盖广。安装：`brew install tesseract`。|
| 5 | _(跳过)_ | — | — | 所有后端均不可用时，输出中省略 OCR 内容。|

**按设备推荐：**

| 设备 | 推荐 OCR | 原因 |
|------|---------|------|
| **Mac（Apple Silicon）** | wechat-ocr → macOS Vision | 本地运行，无需 GPU，CJK 优化 |
| **Mac（Intel）** | macOS Vision → EasyOCR（CPU） | Vision 系统内置；EasyOCR 作备选 |
| **Linux / CUDA GPU** | EasyOCR（CUDA） | GPU 加速，语言覆盖广 |
| **Linux（纯 CPU）** | Tesseract | 轻量，无需下载模型 |
| **Windows（WSL2）** | EasyOCR（CUDA）或 Tesseract | wechat-ocr 和 Vision 仅限 macOS |

通过环境变量指定 OCR 后端：

```bash
# 指定后端（wechat-ocr / vision / easyocr / tesseract）
export VID2MD_OCR_BACKEND=easyocr

# 自定义 wechat-ocr 路径
export WECHAT_OCR_BIN=/path/to/wechat-ocr
```

### AI 帧描述

使用本地 [Ollama](https://ollama.com) 实例和视觉模型（默认：`qwen2.5vl:7b`）。通过环境变量配置：

```bash
OLLAMA_DESC_HOST=http://localhost:11434   # Ollama 端点
OLLAMA_DESC_MODEL=qwen2.5vl:7b           # 视觉模型
```

如果没有可用的 Ollama 实例，加 `--no-desc` 跳过。

---

## 长视频处理

超过 30 分钟的视频，FunASR 可能内存溢出崩溃。推荐方案是切分处理后合并。

```bash
# 每段20分钟切分处理后合并
scripts/process_long_video.sh /path/to/video.mp4 /path/to/output 20
```

### 推荐切分策略

| 视频时长 | 建议每段 |
|---------|---------|
| 30-60 分钟 | 20 分钟 |
| 60-120 分钟 | 30 分钟 |
| 120 分钟+ | 40 分钟 |

---

## Phase 2 — 知识提炼

生成 `tutorial.md` 后，可用本地大语言模型做第二轮处理，产出结构化的 `knowledge.md`——从原始转录文本中提炼核心概念、步骤和参数。

需要：
- 本地 LLM 服务（如 Ollama 搭配合适的语言模型）
- `scripts/phase2_batch.py`——处理长文档的分块提炼脚本

```bash
python3 scripts/phase2_batch.py \
  --input output/<标题>/tutorial.md \
  --output output/<标题>/knowledge.md \
  --host http://localhost:11434 \
  --model <你的模型名>
```

脚本按 10,000 字符分块处理以避免上下文限制，最后合并结果。

---

## 配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MODELSCOPE_CACHE` | `~/.cache/modelscope` _(示例)_ | FunASR 模型缓存目录 |
| `WECHAT_OCR_BIN` | `/path/to/wechat-ocr` | wechat-ocr 二进制路径 |
| `OLLAMA_DESC_HOST` | `http://localhost:11434` | 帧描述 Ollama 端点 |
| `OLLAMA_DESC_MODEL` | `qwen2.5vl:7b` | 帧描述视觉模型 |

### CLI 参数

```
用法: vid2md.py [-h] [--lang {zh,en,auto}] [--output OUTPUT]
               [--scene-threshold N] [--interval N]
               [--no-ocr] [--no-desc] [--verbose]
               input

位置参数:
  input                 URL 或本地视频文件路径

可选参数:
  --lang {zh,en,auto}   转录语言（默认：zh）
  --output OUTPUT       输出目录（默认：./output/<标题>）
  --scene-threshold N   场景切换灵敏度；越大帧越少（默认：8）
  --interval N          固定间隔补帧秒数（默认：30）
  --no-ocr              跳过 OCR 步骤
  --no-desc             跳过 AI 帧描述步骤
  --verbose             启用调试日志
```

### 场景灵敏度参考

| 视频类型 | `--scene-threshold` |
|---------|-------------------|
| 屏幕录制、UI 演示 | 8（默认） |
| 纯讲解、对话 | 12~15 |
| 高动态视频 | 20+ |

---

## 安装

```bash
git clone https://github.com/OttoPrua/vid2md.git
cd vid2md
pip install -r requirements.txt
brew install ffmpeg           # macOS
ollama pull qwen2.5vl:7b     # 可选：AI 帧描述
```

### 可选：wechat-ocr

用于中英文 OCR 的本地二进制工具。不可用时，vid2md 回退到 macOS Vision 框架（仅 macOS）或跳过 OCR。

通过环境变量配置路径：
```bash
export WECHAT_OCR_BIN=/path/to/wechat-ocr
```

---

## 常见问题

**中文视频转录输出全空 / 被识别为英文**
FunASR 加载失败。检查 `MODELSCOPE_CACHE` 是否设置，以及模型文件是否存在。首次运行会自动下载约 300 MB。

**英文模型下载慢**
确保能访问 huggingface.co，部分地区需要代理。

**输出中没有 AI 描述**
Ollama 未运行或视觉模型未加载。运行 `ollama list` 确认，或加 `--no-desc` 跳过。

**截图太多 / 太少**
调整 `--scene-threshold`（越大帧越少）或 `--interval`（越大补帧越少）。

---

## 相关项目

- [yt-dlp](https://github.com/yt-dlp/yt-dlp) — 视频下载
- [FunASR](https://github.com/modelscope/FunASR) — 中文 ASR
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — 备用 ASR
- [Ollama](https://ollama.com) — 本地 LLM/VLM 运行时
- [OpenClaw](https://github.com/openclaw/openclaw) — 本 skill 所在的 agent 框架

## 许可证

MIT
