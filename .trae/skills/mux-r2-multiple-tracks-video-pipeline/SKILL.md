---
name: mux-r2-multiple-tracks-video-pipeline
description: Publish videos, alternate audio tracks, and subtitles to Mux through Cloudflare R2. Use when asked to ingest one main video plus sidecar audio or subtitle files into one Mux asset with switchable tracks. Do not use for direct local upload to Mux.
---

# Mux R2 多音轨多字幕发布流水线

把「本地一组文件 -> R2 -> Mux -> HLS」串成一个可复用流程，适合这种命名方式：

- 主视频：`Movie.2024.mp4`
- 外挂音轨：`Movie.2024.AC-3 5.1.mp3`、`Movie.2024.TrueHD Atmos 7.1.m4a`
- 外挂字幕：`Movie.2024.English.srt`、`Movie.2024.Chinese (Traditional).default.srt`

脚本会把：

- 主视频建成一个 Mux asset
- 同 stem 的音频文件追加成可切换音轨
- 同 stem 的 `srt` / `vtt` 追加成可切换字幕
- 文件名里包含 `default` 或 `defalut` 的字幕，当作默认字幕，并输出带 `default_subtitles_lang=` 的 HLS URL

## 自带文件

| 路径 | 用途 |
|---|---|
| `scripts/mux_r2_multiple_tracks_nightly_pull.py` | 主流程：列 R2 对象、按 stem 组装视频包、创建 Mux 资产并追加音轨和字幕 |
| `assets/cloudflare.env.example` | R2 + Mux 配置模板 |
| `assets/rclone.conf.example` | rclone `[r2]` profile 模板 |
| `assets/com.mux-r2.multiple-tracks.nightly-pull.plist.template` | launchd 定时模板（每天 23:00） |

## 在新项目启用

设 `SKILL_DIR` 为本 skill 目录，`PROJ` 为目标项目根。

1. 复制配置模板并填入真实值：

```bash
cp "$SKILL_DIR/assets/cloudflare.env.example" "$PROJ/cloudflare.env"
cp "$SKILL_DIR/assets/rclone.conf.example" "$PROJ/rclone.conf"
```

2. 放文件：`mkdir -p "$PROJ/video"`，把主视频、外挂音轨、外挂字幕都放进去。

3. 先传到 R2：

```bash
export PATH="$HOME/.local/bin:$PATH"
cd "$PROJ"
rclone --config rclone.conf copy ./video/ r2:<桶名>/ \
  --ignore-existing \
  --transfers 7 --checkers 8 \
  --s3-upload-concurrency 32 --s3-chunk-size 8M \
  --stats 10s --stats-one-line -v
```

4. 运行 Mux 拉取与组装：

```bash
cd "$PROJ"
python3 -u "$SKILL_DIR/scripts/mux_r2_multiple_tracks_nightly_pull.py" --dry-run
python3 -u "$SKILL_DIR/scripts/mux_r2_multiple_tracks_nightly_pull.py"
```

依赖：`python3`、`requests`、`rclone`。

## 文件匹配规则

脚本按下面的规则组包：

1. 先找主视频文件：`.mp4` / `.mov` / `.mkv`
2. 再找与主视频同 stem 的外部轨道文件：
   - 音频：`<视频 stem>.<任意标签>.mp3|m4a|aac|wav|flac|ac3|eac3`
   - 字幕：`<视频 stem>.<任意标签>.srt|vtt`
3. `任意标签` 会变成轨道名称，例如 `English`、`TrueHD Atmos 7.1`

例子：

- `Inside.Out.2.mp4`
- `Inside.Out.2.TrueHD Atmos 7.1.mp3`
- `Inside.Out.2.English.srt`

会被组装成同一个 asset。

## 默认字幕规则

Mux 的默认字幕不是资产级字段，而是播放 URL 参数。因此脚本会：

- 识别文件名中含 `default` 或 `defalut` 的字幕文件
- 推断该字幕的语言码
- 在结果日志里输出：
  - `hls_url` 基础播放地址
  - `hls_url_with_default_subtitles` 带 `?default_subtitles_lang=...` 的地址

如果默认字幕文件推不出合法语言码，脚本会保留字幕轨，但不会拼默认字幕参数。

## 语言推断

- 字幕优先按文件名里的语言名推断，例如 `English` -> `en`、`Chinese (Traditional)` -> `zh-Hant`
- 音频轨如果文件名里没有语言信息，会回退到 `und`
- 轨道展示名优先使用文件名标签本身，例如 `AC-3 5.1`

## 去重规则

- asset 的 `passthrough` 使用主视频完整文件名
- 已存在同名主视频 asset 时，本次会跳过该视频包
- `--force` 会忽略该去重逻辑，重新创建一份新 asset

## 参数

- `--dry-run`：只打印将要处理的视频包，不真正调用 Mux
- `--force`：忽略已有 asset，重新处理全部主视频

## 配置查找规则

脚本按以下顺序定位配置根目录（存放 `cloudflare.env` / `rclone.conf` / `logs/`）：

1. 环境变量 `MUX_R2_PROJECT_DIR`
2. 当前工作目录（其中已存在 `cloudflare.env` 或 `rclone.conf`）
3. 脚本自身所在目录

## 凭证

统一放在 `cloudflare.env`：

- `MUX_TOKEN_ID` / `MUX_TOKEN_SECRET`
- `CLOUDFLARE_R2_PUBLIC_DOMAIN`
- `CLOUDFLARE_R2_BUCKET`
- `CLOUDFLARE_R2_PREFIX` 可选，R2 子目录前缀，例如 `movies/`

`cloudflare.env` / `rclone.conf` / `logs/` 必须加入 `.gitignore`。

## 产物消费

结果写入 `logs/mux_r2_multiple_tracks_pull_results.jsonl`。

消费时优先取：

- 有默认字幕时：`hls_url_with_default_subtitles`
- 无默认字幕时：`hls_url`
