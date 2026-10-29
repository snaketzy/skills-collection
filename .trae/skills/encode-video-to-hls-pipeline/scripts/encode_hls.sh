#!/usr/bin/env bash
#
# 本地视频 -> 多码率 / 多音轨 / 多字幕 HLS
#
#   ffmpeg(h264_videotoolbox 硬编) 产出 mezz 中间件  ->  shaka-packager 组 HLS
#
# 关键点：
#   1. 所有视频档必须同 GOP、同关键帧边界，否则切档花屏
#      VideoToolbox 默认 GOP 只有约 12 帧，必须显式给 -g 和 -force_key_frames
#   2. 音频、字幕各自独立转成 fMP4 / VTT mezz，再由 packager 合成 EXT-X-MEDIA
#   3. packager 的输入描述符必须带 in= 前缀
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"

FFMPEG="${FFMPEG:-ffmpeg}"
FFPROBE="${FFPROBE:-ffprobe}"
PACKAGER="${PACKAGER:-packager}"

INPUT=""
OUT=""
LADDER="240:426x240:300k:350k:700k,360:640x360:700k:800k:1500k,480:854x480:1200k:1300k:2400k,720:1280x720:2800k:3000k:5600k,1080:1920x1080:5000k:5350k:10000k"
AUDIO=""
SUBS_PREFIX=""
DURATION=0
SEG=6
GOP=""
KEEP_MEZZ=0
COPY_PLAYER=1
DEFAULT_AUDIO_LANG="en"
DEFAULT_TEXT_LANG="en"

usage() {
  cat <<'EOF'
用法: encode_hls.sh --input <视频> --out <目录> [选项]

必填:
  --input PATH           主视频文件
  --out DIR              输出根目录，内部会建 mezz/ 与 hls/

选项:
  --ladder SPEC          视频阶梯，逗号分隔，每项 name:WxH:码率:maxrate:bufsize
                         默认 240/360/480/720/1080 五档
  --audio SPEC           音轨，逗号分隔，每项 name:lang:声道数:码率:源
                         源写 main 表示取主视频第 0 条音频，否则给外挂音频文件路径
                         默认 English:en:2:192k:main
  --subs-prefix PATH     字幕前缀；匹配 <前缀>.<标签>.vtt
                         <标签> 末尾的 .default 会被去掉，并作为默认字幕
  --duration SEC         只处理前 SEC 秒（0=全片，默认 0）
  --segment-duration SEC 分片时长，默认 6
  --gop N                手动指定 GOP 帧数，默认按 fps*分片时长 取整
  --default-audio-lang L 默认音轨语言，默认 en
  --default-text-lang L  默认字幕语言，默认 en
  --keep-mezz            打包后保留 mezz 中间文件（默认删除）
  --no-player            不复制附带播放器到 HLS 目录
  -h, --help             显示本帮助
EOF
}

die() { echo "错误: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --input) INPUT="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --ladder) LADDER="$2"; shift 2;;
    --audio) AUDIO="$2"; shift 2;;
    --subs-prefix) SUBS_PREFIX="$2"; shift 2;;
    --duration) DURATION="$2"; shift 2;;
    --segment-duration) SEG="$2"; shift 2;;
    --gop) GOP="$2"; shift 2;;
    --default-audio-lang) DEFAULT_AUDIO_LANG="$2"; shift 2;;
    --default-text-lang) DEFAULT_TEXT_LANG="$2"; shift 2;;
    --keep-mezz) KEEP_MEZZ=1; shift;;
    --no-player) COPY_PLAYER=0; shift;;
    -h|--help) usage; exit 0;;
    *) die "未知参数: $1";;
  esac
done

[ -n "$INPUT" ] || { usage; die "缺少 --input"; }
[ -n "$OUT" ] || { usage; die "缺少 --out"; }
[ -f "$INPUT" ] || die "输入文件不存在: $INPUT"

for bin in "$FFMPEG" "$FFPROBE" "$PACKAGER"; do
  command -v "$bin" >/dev/null 2>&1 || die "找不到可执行文件: $bin"
done

MEZZ="$OUT/mezz"
HLS="$OUT/hls"
mkdir -p "$MEZZ/sub" "$HLS"
MEZZ="$(cd "$MEZZ" && pwd)"
HLS="$(cd "$HLS" && pwd)"

# ---------- 探测源 ----------
FPS_RAW="$("$FFPROBE" -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$INPUT" | head -1)"
FPS="$(awk -F/ '{printf "%.6f", ($2 ? $1/$2 : $1)}' <<<"${FPS_RAW:-0}")"
SRC_DUR="$("$FFPROBE" -v error -show_entries format=duration -of csv=p=0 "$INPUT" | head -1)"

if [ "$DURATION" = "0" ]; then DUR="$SRC_DUR"; else DUR="$DURATION"; fi
[ -n "$GOP" ] || GOP="$(awk -v f="$FPS" -v s="$SEG" 'BEGIN{printf "%d", f*s+0.5}')"

echo "源         : $INPUT"
echo "帧率       : $FPS  (GOP=$GOP, 分片=${SEG}s)"
echo "处理时长   : ${DUR}s"
echo "mezz       : $MEZZ"
echo "hls        : $HLS"
echo

# ---------- 1. 视频档 ----------
echo "=== 1/4 视频编码 (硬编 $FFMPEG h264_videotoolbox) ==="
IFS=',' read -r -a LADDER_ITEMS <<<"$LADDER"
for item in "${LADDER_ITEMS[@]}"; do
  IFS=':' read -r vname vsize vbr vmax vbuf <<<"$item"
  [ -n "${vname:-}" ] && [ -n "${vsize:-}" ] || die "阶梯格式错误: $item"
  echo "  - $vname ($vsize, $vbr)"
  "$FFMPEG" -y -hide_banner -loglevel error -t "$DUR" -i "$INPUT" -an \
    -c:v h264_videotoolbox -profile:v high -pix_fmt yuv420p \
    -b:v "$vbr" -maxrate "$vmax" -bufsize "$vbuf" -g "$GOP" \
    -vf "scale=$vsize:flags=bicubic" \
    -force_key_frames "expr:gte(t,n_forced*$SEG)" -movflags +faststart \
    "$MEZZ/v_$vname.mp4"
done

# ---------- 2. 音轨 ----------
echo "=== 2/4 音频编码 ==="
[ -n "$AUDIO" ] || AUDIO="English:en:2:192k:main"
IFS=',' read -r -a AUDIO_ITEMS <<<"$AUDIO"
for item in "${AUDIO_ITEMS[@]}"; do
  IFS=':' read -r aname alang ach abr asrc <<<"$item"
  [ -n "${aname:-}" ] && [ -n "${asrc:-}" ] || die "音轨格式错误: $item"
  slug="$(printf '%s' "$aname" | tr -c 'A-Za-z0-9_-' '_')"
  ch_args=(-ac "$ach")
  [ "$ach" = "6" ] && ch_args=(-ac 6 -channel_layout 5.1)
  echo "  - $aname ($alang, ${ach}ch, $abr, 源=$asrc)"
  if [ "$asrc" = "main" ]; then
    "$FFMPEG" -y -hide_banner -loglevel error -t "$DUR" -i "$INPUT" -map 0:a:0 \
      -c:a aac -b:a "$abr" "${ch_args[@]}" "$MEZZ/a_$slug.mp4"
  else
    [ -f "$asrc" ] || die "外挂音轨不存在: $asrc"
    "$FFMPEG" -y -hide_banner -loglevel error -t "$DUR" -i "$asrc" \
      -c:a aac -b:a "$abr" "${ch_args[@]}" "$MEZZ/a_$slug.mp4"
  fi
done

# ---------- 3. 字幕 ----------
echo "=== 3/4 字幕准备 ==="
SUBS=()
SUBS_N=0
if [ -n "$SUBS_PREFIX" ]; then
  shopt -s nullglob
  pbase="$(basename "$SUBS_PREFIX")"
  for f in "$SUBS_PREFIX".*.vtt; do
    base="$(basename "$f" .vtt)"
    label="${base:$(( ${#pbase} + 1 ))}"
    label="${label%.default}"
    slug="$(sed 's/[()]//g; s/ /_/g' <<<"$label")"
    python3 "$SCRIPT_DIR/trim_vtt.py" "$f" "$MEZZ/sub/$slug.vtt" "$DUR"
    SUBS+=("$slug|$label")
    SUBS_N=$((SUBS_N + 1))
  done
  shopt -u nullglob
  [ "$SUBS_N" -gt 0 ] || die "没有匹配到字幕: $SUBS_PREFIX.*.vtt"
  echo "  共 $SUBS_N 条字幕"
else
  echo "  未指定 --subs-prefix，跳过字幕"
fi

# 字幕显示名 -> BCP-47 语言码
lang_of() {
  case "$1" in
    "Chinese (Simplified)") echo "zh-Hans";;
    Czech) echo "cs";;
    Danish) echo "da";;
    Dutch) echo "nl";;
    English) echo "en";;
    Finnish) echo "fi";;
    "French (Canada)-2") echo "fr-CA";;
    "French (Canada)") echo "fr-CA";;
    "French (France)") echo "fr-FR";;
    German) echo "de";;
    Greek) echo "el";;
    Hungarian) echo "hu";;
    Italian) echo "it";;
    Japanese) echo "ja";;
    Korean) echo "ko";;
    Norwegian) echo "nb";;
    Polish) echo "pl";;
    "Portuguese (Brazil)") echo "pt-BR";;
    Portuguese) echo "pt";;
    Romanian) echo "ro";;
    Slovak) echo "sk";;
    "Spanish (Latin America)") echo "es-419";;
    "Spanish (Spain)") echo "es-ES";;
    Swedish) echo "sv";;
    "Traditional Chinese(HK)") echo "zh-Hant-HK";;
    "Traditional Chinese") echo "zh-Hant";;
    Turkish) echo "tr";;
    *) echo "";;
  esac
}

# ---------- 4. 打包 ----------
echo "=== 4/4 shaka-packager 组 HLS ==="
cd "$HLS"
rm -rf master.m3u8 v_* a_* s_*

DESC=()
for item in "${LADDER_ITEMS[@]}"; do
  IFS=':' read -r vname _ _ _ _ <<<"$item"
  DESC+=("in=../mezz/v_$vname.mp4,stream=video,init_segment=$vname/init.mp4,segment_template=$vname/\$Number\$.m4s,playlist_name=$vname/playlist.m3u8")
done

for item in "${AUDIO_ITEMS[@]}"; do
  IFS=':' read -r aname alang _ _ _ <<<"$item"
  slug="$(printf '%s' "$aname" | tr -c 'A-Za-z0-9_-' '_')"
  DESC+=("in=../mezz/a_$slug.mp4,stream=audio,init_segment=a_$slug/init.mp4,segment_template=a_$slug/\$Number\$.m4s,playlist_name=a_$slug/playlist.m3u8,hls_group_id=audio,hls_name=$aname,lang=$alang")
done

if [ "$SUBS_N" -gt 0 ]; then
  for entry in "${SUBS[@]}"; do
    slug="${entry%%|*}"; label="${entry##*|}"
    lang="$(lang_of "$label")"
    desc="in=../mezz/sub/$slug.vtt,stream=text,segment_template=s_$slug/\$Number\$.vtt,playlist_name=s_$slug/playlist.m3u8,hls_group_id=subs,hls_name=$label"
    [ -n "$lang" ] && desc="$desc,lang=$lang"
    DESC+=("$desc")
  done
fi

echo "  输入流数量: ${#DESC[@]}"
"$PACKAGER" "${DESC[@]}" \
  --segment_duration "$SEG" \
  --default_language "$DEFAULT_AUDIO_LANG" \
  --default_text_language "$DEFAULT_TEXT_LANG" \
  --hls_master_playlist_output master.m3u8

# ---------- 附带播放器 ----------
if [ "$COPY_PLAYER" = "1" ] && [ -f "$SKILL_DIR/assets/player.html" ]; then
  cp "$SKILL_DIR/assets/player.html" "$HLS/player.html"
  if [ ! -f "$HLS/hls.min.js" ]; then
    echo
    echo "提示: 播放器需要同目录的 hls.min.js，执行下面命令补齐："
    echo "  curl -fsSL -o \"$HLS/hls.min.js\" https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js"
  fi
fi

# ---------- 校验 ----------
echo
echo "=== 分段核对 ==="
for item in "${LADDER_ITEMS[@]}"; do
  IFS=':' read -r vname _ _ _ _ <<<"$item"
  printf "  v_%-6s %s 段\n" "$vname" "$(ls "$vname"/*.m4s 2>/dev/null | wc -l | tr -d ' ')"
done
for item in "${AUDIO_ITEMS[@]}"; do
  IFS=':' read -r aname _ _ _ _ <<<"$item"
  slug="$(printf '%s' "$aname" | tr -c 'A-Za-z0-9_-' '_')"
  printf "  a_%-6s %s 段\n" "$slug" "$(ls "a_$slug"/*.m4s 2>/dev/null | wc -l | tr -d ' ')"
done
[ "$SUBS_N" -gt 0 ] && echo "  字幕轨 $SUBS_N 条"

echo
echo "=== 关键帧对齐（各档前 3 个）==="
for item in "${LADDER_ITEMS[@]}"; do
  IFS=':' read -r vname _ _ _ _ <<<"$item"
  printf "  v_%-6s " "$vname"
  "$FFPROBE" -v error -select_streams v:0 -skip_frame nokey \
    -show_entries frame=pts_time -of csv=p=0 -read_intervals "%+$((SEG * 3))" \
    "$MEZZ/v_$vname.mp4" 2>/dev/null | head -3 | tr '\n' ' '
  echo
done

if [ "$KEEP_MEZZ" = "0" ]; then
  rm -rf "$MEZZ"
  echo
  echo "已删除 mezz 中间文件（--keep-mezz 可保留）"
fi

echo
echo "完成。master 播放列表: $HLS/master.m3u8"
