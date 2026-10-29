---
name: r2-cf-stream-multiple-tracks-video-pipeline
description: Publish one main video plus sidecar audio and subtitle files from Cloudflare R2 to Cloudflare Stream. Use when the user asks to ingest switchable audio tracks and captions into Cloudflare Stream HLS or iframe playback. Do not use for Mux workflows or direct local-only playback.
---

# R2 -> Cloudflare Stream 多音轨多字幕发布流水线

把「本地一组文件 -> R2 -> Cloudflare Stream -> HLS / iframe」串成一个可复用流程，适合这种命名：

- 主视频：`Movie.2024.mp4`
- 外挂音轨：`Movie.2024.Cantonese.mp3`、`Movie.2024.Mandarin.m4a`
- 外挂字幕：`Movie.2024.English.default.vtt`、`Movie.2024.Chinese (Traditional).srt`

脚本会把：

- 主视频通过 Cloudflare Stream `copy` 接口从 R2 公网 URL 拉取
- 同 stem 的音频文件作为 Cloudflare Stream additional audio tracks 追加
- 同 stem 的字幕文件作为 Cloudflare Stream captions 追加
- 文件名里包含 `default` 或 `defalut` 的字幕识别为默认字幕候选

## 自带文件

| 路径 | 用途 |
|---|---|
| `scripts/r2_cf_stream_multiple_tracks_nightly_pull.py` | 主流程：列 R2 对象、组装视频包、创建 Cloudflare Stream 视频、追加音轨和字幕 |
| `assets/cloudflare.env.example` | R2 + Cloudflare Stream 配置模板 |
| `assets/rclone.conf.example` | rclone `[r2]` profile 模板 |
| `assets/com.r2-cf-stream.multiple-tracks.nightly-pull.plist.template` | launchd 定时模板（每天 23:00） |

## 在新项目启用

设 `SKILL_DIR` 为本 skill 目录，`PROJ` 为目标项目根。

1. 复制配置模板：

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

4. 运行 Cloudflare Stream 拉取与组装：

```bash
cd "$PROJ"
python3 -u "$SKILL_DIR/scripts/r2_cf_stream_multiple_tracks_nightly_pull.py" --dry-run
python3 -u "$SKILL_DIR/scripts/r2_cf_stream_multiple_tracks_nightly_pull.py"
```

依赖：`python3`、`requests`、`rclone`。

## 文件匹配规则

脚本按下面规则组包：

1. 先找主视频文件：`.mp4` / `.mov` / `.mkv`
2. 再找与主视频同 stem 的外部轨道文件：
   - 音频：`<视频 stem>.<任意标签>.mp3|m4a|aac|wav|flac|ac3|eac3`
   - 字幕：`<视频 stem>.<任意标签>.vtt|srt`
3. `任意标签` 会变成轨道名或语言推断输入，例如 `English`、`French (Canada)`、`TrueHD Atmos 7.1`

## Cloudflare Stream 平台差异

这版和 Mux 版有三个重要差异：

1. **字幕上传不是 URL copy**
   - Cloudflare Stream 音频支持 `audio/copy`
   - 字幕不支持从 URL 直接 copy，脚本会优先找本地文件；找不到时会从 R2 公网 URL 下载到临时目录后再上传

2. **字幕每个语言码只允许一条**
   - Cloudflare Stream captions 接口对同一个 BCP-47 语言码只允许一个字幕文件
   - Cloudflare Stream 对字幕语言码的兼容性比 Mux 更严格，重复或过于宽泛的语言标签不一定会被平台真正保留
   - 脚本会优先尝试更兼容的语言码，例如把普通 `Portuguese` 归一为 `pt-PT`
   - 遇到重复语言时，脚本会尽量分配一个更保守的备用语言码；如果 Cloudflare Stream 最终没有保留该字幕，脚本会发出警告并继续，而不是一直卡在等待状态

3. **默认字幕不是 HLS URL 参数**
   - Cloudflare Stream 原生播放器通过初始化参数 `defaultTextTrack` 指定默认字幕
   - HLS manifest URL 本身不携带“默认字幕”选择
   - 所以脚本结果会输出：
     - `hls_url`
     - `dash_url`
     - `iframe_url`
     - `default_text_track`
   - 你在接入 Cloudflare Stream Player 时，把 `default_text_track` 传给 `defaultTextTrack` 即可

## 字幕格式

- Cloudflare Stream captions 上传接口要求 `WebVTT`
- `.vtt` 会直接上传
- `.srt` 会在临时目录中自动转成 `.vtt` 后上传

## 默认字幕规则

- 文件名里包含 `default` 或 `defalut` 的字幕视为默认字幕候选
- 默认字幕优先使用其最终上传到 Cloudflare Stream 的 `default_text_track`
- 如果默认字幕没有被 Cloudflare Stream 最终保留，结果里不会输出 `default_text_track`
- 如果默认字幕语言无法推断，会保留字幕轨，但不会输出默认字幕配置

## 去重规则

- Cloudflare Stream 视频 `meta.name` 使用主视频完整文件名
- 已存在同名 `meta.name` 的视频时，本次会跳过该 bundle
- `--force` 会忽略该去重逻辑，重新创建一份新视频

## 参数

- `--dry-run`：只打印将要处理的视频包，不真正调用 Cloudflare API
- `--force`：忽略已有同名视频，重新处理全部主视频

## 配置查找规则

脚本按以下顺序定位配置根目录（存放 `cloudflare.env` / `rclone.conf` / `logs/`）：

1. 环境变量 `CF_STREAM_PROJECT_DIR`
2. 当前工作目录（其中已存在 `cloudflare.env` 或 `rclone.conf`）
3. 脚本自身所在目录

## 凭证

统一放在 `cloudflare.env`：

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_STREAM_API_TOKEN`
- `CLOUDFLARE_R2_PUBLIC_DOMAIN`
- `CLOUDFLARE_R2_BUCKET`
- `CLOUDFLARE_R2_PREFIX` 可选
- `CLOUDFLARE_STREAM_ALLOWED_ORIGINS` 可选，逗号分隔
- `CLOUDFLARE_STREAM_REQUIRE_SIGNED_URLS` 可选，`true` / `false`

`cloudflare.env` / `rclone.conf` / `logs/` 必须加入 `.gitignore`。

## 产物消费

结果写入 `logs/r2_cf_stream_multiple_tracks_pull_results.jsonl`。

消费时优先取：

- `hls_url`
- `iframe_url`
- `default_text_track`

Cloudflare Stream Player 示例：

```html
<iframe
  id="stream-player"
  src="https://customer-<CODE>.cloudflarestream.com/<VIDEO_UID>/iframe"
  allow="accelerometer; gyroscope; autoplay; encrypted-media; picture-in-picture;"
  allowfullscreen
></iframe>
<script src="https://embed.cloudflarestream.com/embed/sdk.latest.js"></script>
<script>
  const player = Stream(document.getElementById("stream-player"));
  player.defaultTextTrack = "en";
</script>
```
