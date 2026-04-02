#!/bin/bash
# 长视频切分处理脚本
# 每段20分钟，分段跑 vid2md，最后合并 tutorial.md

INPUT="$1"
OUTPUT_DIR="$2"
SEGMENT_MINS=${3:-20}  # 默认每段20分钟

if [ -z "$INPUT" ] || [ -z "$OUTPUT_DIR" ]; then
    echo "用法: $0 <输入视频> <输出目录> [每段分钟数]"
    exit 1
fi

LOG="${TMPDIR:-/tmp}/vid2md_phase1.log"
VID2MD="${VID2MD_PATH:-$(cd "$(dirname "$0")/.." && pwd)/vid2md.py}"
TMPDIR=$(mktemp -d /tmp/vid_segments_XXXXXX)
SEGMENT_SECS=$((SEGMENT_MINS * 60))

log() { echo "[$(date +%H:%M:%S)] $1" | tee -a "$LOG"; }

# 获取视频总时长
DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$INPUT" 2>/dev/null | cut -d. -f1)
log "[切分] $INPUT 总时长=${DURATION}s，每段${SEGMENT_MINS}分钟"

# 清理临时目录
rm -rf "$TMPDIR" && mkdir -p "$TMPDIR"
mkdir -p "$OUTPUT_DIR"

# 切分视频
PART=0
START=0
PARTS=()
while [ $START -lt $DURATION ]; do
    PART=$((PART + 1))
    SEG_FILE="$TMPDIR/part$(printf '%03d' $PART).mp4"
    ffmpeg -y -ss $START -i "$INPUT" -t $SEGMENT_SECS -c copy "$SEG_FILE" 2>/dev/null
    PARTS+=("$SEG_FILE")
    log "[切分] part$PART: ${START}s ~ $((START + SEGMENT_SECS))s → $SEG_FILE"
    START=$((START + SEGMENT_SECS))
done

log "[切分] 共 ${#PARTS[@]} 段"

# 逐段跑 vid2md
for SEG in "${PARTS[@]}"; do
    PART_NAME=$(basename "$SEG" .mp4)
    PART_OUT="$TMPDIR/${PART_NAME}_out"
    log "[处理] $PART_NAME"
    export MODELSCOPE_CACHE="/tmp/ms_models"
    python3 "$VID2MD" "$SEG" -o "$PART_OUT" --lang zh --interval 30 --scene-threshold 10 --no-desc 2>&1 | tee -a "$LOG"
done

# 合并 tutorial.md
FINAL_MD="$OUTPUT_DIR/tutorial.md"
log "[合并] 生成 $FINAL_MD"
echo "# $(basename "$INPUT" .mp4)" > "$FINAL_MD"
echo "" >> "$FINAL_MD"

for SEG in "${PARTS[@]}"; do
    PART_NAME=$(basename "$SEG" .mp4)
    PART_MD="$TMPDIR/${PART_NAME}_out/tutorial.md"
    if [ -f "$PART_MD" ]; then
        echo "" >> "$FINAL_MD"
        echo "---" >> "$FINAL_MD"
        echo "## $PART_NAME" >> "$FINAL_MD"
        cat "$PART_MD" >> "$FINAL_MD"
    fi
done

# 合并 frames
mkdir -p "$OUTPUT_DIR/frames"
for SEG in "${PARTS[@]}"; do
    PART_NAME=$(basename "$SEG" .mp4)
    FRAMES_DIR="$TMPDIR/${PART_NAME}_out/frames"
    [ -d "$FRAMES_DIR" ] && cp -n "$FRAMES_DIR"/* "$OUTPUT_DIR/frames/" 2>/dev/null
done

log "[完成] ✅ $OUTPUT_DIR/tutorial.md"
wc -l "$FINAL_MD"

# 清理临时文件
rm -rf "$TMPDIR"
