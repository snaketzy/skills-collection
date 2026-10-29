#!/usr/bin/env bash
#
# 本地 HLS 目录 -> Cloudflare R2 -> 在线 m3u8 地址
#
#   两趟上传 + 完整性核对 + 线上 content-type / CORS 验证
#
# 为什么必须分两趟：
#   rclone 的 --header-upload 是「整个传输」级别的，无法按扩展名分别设置。
#   而 .vtt 必须显式改写成 Content-Type: text/vtt，否则会落成
#   application/octet-stream；其余文件交给 rclone 自动识别即可。
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RCLONE="${RCLONE:-rclone}"
CURL="${CURL:-curl}"

HLS_DIR=""
BUCKET=""
PREFIX=""
PUBLIC_DOMAIN=""
RCONF=""
SKIP_UPLOAD=0
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: upload_hls_to_r2.sh --hls <本地hls目录> [选项]

必填:
  --hls DIR              本地 HLS 目录（含 master.m3u8）

目标:
  --bucket NAME          R2 桶名
  --prefix PATH          R2 前缀（子目录），留空表示桶根
  --public-domain URL    桶的公共访问域名，如 https://pub-xxxx.r2.dev
  --config PATH          rclone.conf 路径

选项:
  --skip-upload          跳过上传，只做核对与线上验证
  --dry-run              打印将要传输的文件，不真正上传
  -h, --help             显示本帮助

未显式给 --bucket / --prefix / --public-domain / --config 时，会从
$R2_HLS_PROJECT_DIR（默认当前目录）下的 cloudflare.env 与 rclone.conf 读取：
  CLOUDFLARE_R2_BUCKET / CLOUDFLARE_R2_PREFIX / CLOUDFLARE_R2_PUBLIC_DOMAIN
EOF
}

die() { echo "错误: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --hls) HLS_DIR="$2"; shift 2;;
    --bucket) BUCKET="$2"; shift 2;;
    --prefix) PREFIX="$2"; shift 2;;
    --public-domain) PUBLIC_DOMAIN="$2"; shift 2;;
    --config) RCONF="$2"; shift 2;;
    --skip-upload) SKIP_UPLOAD=1; shift;;
    --dry-run) DRY_RUN=1; shift;;
    -h|--help) usage; exit 0;;
    *) die "未知参数: $1";;
  esac
done

[ -n "$HLS_DIR" ] || { usage; die "缺少 --hls"; }
[ -d "$HLS_DIR" ] || die "目录不存在: $HLS_DIR"
[ -f "$HLS_DIR/master.m3u8" ] || die "$HLS_DIR 下没有 master.m3u8，确认这是 HLS 输出目录"
HLS_DIR="$(cd "$HLS_DIR" && pwd)"

# ---------- 配置回退 ----------
PROJ_DIR="${R2_HLS_PROJECT_DIR:-$PWD}"
[ -n "$RCONF" ] || { [ -f "$PROJ_DIR/rclone.conf" ] && RCONF="$PROJ_DIR/rclone.conf" || true; }
if [ -f "$PROJ_DIR/cloudflare.env" ]; then
  set -a; . "$PROJ_DIR/cloudflare.env"; set +a
  [ -n "$BUCKET" ]        || BUCKET="${CLOUDFLARE_R2_BUCKET:-}"
  [ -n "$PREFIX" ]        || PREFIX="${CLOUDFLARE_R2_PREFIX:-}"
  [ -n "$PUBLIC_DOMAIN" ] || PUBLIC_DOMAIN="${CLOUDFLARE_R2_PUBLIC_DOMAIN:-}"
fi

[ -n "$BUCKET" ] || die "缺少 --bucket（也未在 cloudflare.env 找到 CLOUDFLARE_R2_BUCKET）"
command -v "$RCLONE" >/dev/null 2>&1 || die "找不到 rclone，把 ~/.local/bin 加进 PATH"

RCFG=()
[ -n "$RCONF" ] && RCFG=(--config "$RCONF")
[ -n "$RCONF" ] && [ ! -f "$RCONF" ] && die "rclone 配置不存在: $RCONF"

# 归一化远端路径与 URL 前缀
PREFIX="${PREFIX#/}"; PREFIX="${PREFIX%/}"
REMOTE="r2:$BUCKET"
[ -n "$PREFIX" ] && REMOTE="$REMOTE/$PREFIX"
REMOTE="${REMOTE%/}/"
URLBASE=""
if [ -n "$PUBLIC_DOMAIN" ]; then
  URLBASE="${PUBLIC_DOMAIN%/}"
  [ -n "$PREFIX" ] && URLBASE="$URLBASE/$PREFIX"
fi

echo "本地 HLS   : $HLS_DIR"
echo "远端目标   : $REMOTE"
echo "公共域名   : ${PUBLIC_DOMAIN:-（未提供，跳过线上验证）}"
[ -n "$RCONF" ] && echo "rclone 配置: $RCONF"
echo

# ---------- 传输参数 ----------
XFER=(
  --transfers 32 --checkers 64
  --s3-upload-concurrency 32 --s3-chunk-size 8M
  --stats 15s --stats-one-line --no-traverse -v
)
[ "$DRY_RUN" = "1" ] && XFER+=(--dry-run)

if [ "$SKIP_UPLOAD" = "0" ]; then
  echo "=== 1/2 非 vtt（m3u8 / m4s / mp4 / html / js） ==="
  echo "    .m3u8 由 rclone 依系统 mime.types 自动识别为 application/x-mpegurl"
  "$RCLONE" "${RCFG[@]}" copy "$HLS_DIR" "$REMOTE" \
    --exclude ".DS_Store" --exclude "*.vtt" "${XFER[@]}"

  echo
  echo "=== 2/2 vtt（强制改写 Content-Type: text/vtt） ==="
  echo "    --ignore-times 不能省：否则 rclone 判定「已存在」直接跳过，类型改不掉"
  "$RCLONE" "${RCFG[@]}" copy "$HLS_DIR" "$REMOTE" \
    --include "*.vtt" --ignore-times \
    --header-upload "Content-Type: text/vtt" "${XFER[@]}"
  echo
else
  echo "（--skip-upload 已跳过上传）"
  echo
fi

# ---------- 完整性核对 ----------
echo "=== 本地 vs 远端 ==="
L_FILES="$(find "$HLS_DIR" -type f ! -name ".DS_Store" | wc -l | tr -d ' ')"
L_BYTES="$(find "$HLS_DIR" -type f ! -name ".DS_Store" -exec stat -f%z {} + | awk '{s+=$1} END{print s+0}')"
R_JSON="$("$RCLONE" "${RCFG[@]}" size "$REMOTE" --json 2>/dev/null || echo '{}')"
R_FILES="$(printf '%s' "$R_JSON" | sed -n 's/.*"count":\([0-9]*\).*/\1/p')"
R_BYTES="$(printf '%s' "$R_JSON" | sed -n 's/.*"bytes":\([0-9]*\).*/\1/p')"
printf "  本地: %s 个文件 / %s 字节\n" "$L_FILES" "$L_BYTES"
printf "  远端: %s 个文件 / %s 字节\n" "${R_FILES:-?}" "${R_BYTES:-?}"
if [ "$L_FILES" = "$R_FILES" ] && [ "$L_BYTES" = "$R_BYTES" ]; then
  echo "  ✅ 数量与字节数完全一致"
else
  echo "  ⚠️  不一致：先确认是否仍有文件在传；若远端偏少请重跑本脚本（rclone 会自动补差集）"
fi

# ---------- 线上验证 ----------
if [ -z "$PUBLIC_DOMAIN" ]; then
  echo
  echo "未提供 --public-domain，跳过线上验证。"
  echo "完成。"
  exit 0
fi

echo
echo "=== 线上状态码与 Content-Type ==="

# 挑一个视频档目录（优先 v?数字 命名的档位，退而求其次取首个含 m4s 的目录）
seg_dir=""; other_seg_dir=""; vtt_dir=""
for d in "$HLS_DIR"/*/; do
  [ -d "$d" ] || continue
  base="$(basename "$d")"
  if ls "$d"*.m4s >/dev/null 2>&1; then
    if [ -z "$seg_dir" ] && [[ "$base" =~ ^v?[0-9]+$ ]]; then seg_dir="$base"
    elif [ -z "$other_seg_dir" ]; then other_seg_dir="$base"; fi
  fi
  [ -z "$vtt_dir" ] && ls "$d"*.vtt >/dev/null 2>&1 && vtt_dir="$base"
done
[ -n "$seg_dir" ] || seg_dir="$other_seg_dir"

CHECK=("master.m3u8")
[ -n "$seg_dir" ] && CHECK+=("$seg_dir/playlist.m3u8" "$seg_dir/init.mp4")
[ -n "$vtt_dir" ] && CHECK+=("$vtt_dir/playlist.m3u8")
CHECK+=("player.html")

for rel in "${CHECK[@]}"; do
  hdr="$("$CURL" -sI "$URLBASE/$rel" || true)"
  code="$(printf '%s' "$hdr" | sed -n 's/^HTTP[^ ]* \([0-9]*\).*/\1/p' | tail -1)"
  ctype="$(printf '%s' "$hdr" | grep -i '^content-type' | tr -d '\r' | sed 's/^[Cc]ontent-[Tt]ype: *//' | tail -1)"
  printf "  %-34s %-4s %s\n" "$rel" "${code:-ERR}" "${ctype:-}"
done

if [ -n "$seg_dir" ]; then
  last="$(ls "$HLS_DIR/$seg_dir"/*.m4s | sed 's/.*\///' | sort -n | tail -1)"
  code="$("$CURL" -s -o /dev/null -w '%{http_code}' "$URLBASE/$seg_dir/$last")"
  echo "  $seg_dir/$last (末段分片) -> $code"
fi

echo
echo "=== CORS ==="
opts="$("$CURL" -s -o /dev/null -D - -X OPTIONS \
  -H "Origin: http://127.0.0.1:8080" -H "Access-Control-Request-Method: GET" \
  "$URLBASE/master.m3u8" | head -1 || true)"
acao="$("$CURL" -sI -H "Origin: http://127.0.0.1:8080" "$URLBASE/master.m3u8" | grep -ci 'access-control-allow-origin' || true)"
echo "  OPTIONS 预检: ${opts:-无响应}"
echo "  ACAO 头数量: ${acao:-0}"
if [ "${acao:-0}" = "0" ]; then
  echo "  ⚠️  该公共域不支持 CORS：桌面播放器与「页面同源」可正常播放；"
  echo "     本地页面跨源拉流会被浏览器拦截，需绑定自定义域名并配置 CORS 规则。"
fi

echo
echo "=== 在线地址 ==="
echo "  m3u8  : $URLBASE/master.m3u8"
echo "  player: $URLBASE/player.html"
echo
echo "完成。"
