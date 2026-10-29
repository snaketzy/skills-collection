---
name: encode-video-to-hls-pipeline
description: Encode local video into multi-bitrate multi-audio multi-subtitle HLS with ffmpeg hard encoding plus shaka-packager. Use when asked to build an HLS ABR ladder or package sidecar audio and WebVTT subtitles.
---

# 本地视频 -> 多码率 / 多音轨 / 多字幕 HLS

把「一个主视频 + 若干外挂音轨 + 若干 WebVTT 字幕」压成一套标准 HLS：

```text
<out>/
├── mezz/                     # 中间件，打包后可删
│   ├── v_240.mp4 ... v_1080.mp4
│   ├── a_English_5_1.mp4 ...
│   └── sub/English.vtt ...
└── hls/
    ├── master.m3u8           # 多码率 + EXT-X-MEDIA 音轨/字幕组
    ├── 240/init.mp4 + 1..N.m4s + playlist.m3u8   (360/ 480/ 720/ 1080/ 同)
    ├── a_English_5_1/...
    ├── s_English/...
    └── player.html           # 校验用播放器（需同目录 hls.min.js）
```

主流程用 `scripts/encode_hls.sh`；字幕裁剪用 `scripts/trim_vtt.py`。

## 依赖

必须是 **arm64 原生** 的 ffmpeg / ffprobe（Rosetta 下 `h264_videotoolbox` 会受限甚至不可用），以及 shaka-packager：

```bash
# 校验是否原生
file "$(command -v ffmpeg)"        # 期望 arm64
ffmpeg -hide_banner -encoders | grep videotoolbox

# 缺失时（官方 arm64 构建，放到 /usr/local/bin 无需 sudo）
curl -fsSL --retry 3 -o /tmp/ffmpeg9arm.zip  https://www.osxexperts.net/ffmpeg9arm.zip
curl -fsSL --retry 3 -o /tmp/ffprobe9arm.zip https://www.osxexperts.net/ffprobe9arm.zip
unzip -o /tmp/ffmpeg9arm.zip -d /tmp/ff && unzip -o /tmp/ffprobe9arm.zip -d /tmp/ff
cp /tmp/ff/ffmpeg /tmp/ff/ffprobe /usr/local/bin/ && chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe

# shaka-packager：从 GitHub releases 取 macOS arm64 包后放入 /usr/local/bin
```

## 默认阶梯

| 档位 | 分辨率 | 码率 | maxrate | bufsize |
|---|---|---|---|---|
| 240P | 426×240 | 300k | 350k | 700k |
| 360P | 640×360 | 700k | 800k | 1500k |
| 480P | 854×480 | 1200k | 1300k | 2400k |
| 720P | 1280×720 | 2800k | 3000k | 5600k |
| 1080P | 1920×1080 | 5000k | 5350k | 10000k |

## 用法

```bash
S=.trae/skills/encode-video-to-hls-pipeline
D=./warehouse/Some.Movie.2011.1080P
V="$D/Some.Movie.2011.mp4"; P="$D/Some.Movie.2011"

"$S/scripts/encode_hls.sh" \
  --input "$V" \
  --out ./output/movie \
  --audio "English_5.1:en:6:384k:main,English:en:2:192k:main,Cantonese:yue:2:192k:$P.Cantonese.mp3,Mandarin:zh:2:192k:$P.Mandarin.mp3" \
  --subs-prefix "$P"
```

- `--audio` 的源写 `main` 表示取主视频第 0 条音频；否则给外挂音频文件。
- `--subs-prefix` 会匹配 `<前缀>.<标签>.vtt`，标签末尾的 `.default` 去掉后即默认字幕。
- 只要前 10 分钟做预演：加 `--duration 600`。全片留空。
- 想调整档位：`--ladder "240:426x240:300k:350k:700k,720:1280x720:2800k:3000k:5600k"`。

## 必须遵守的规则

1. **所有视频档必须同 GOP、同关键帧边界**，否则切档花屏。
   `h264_videotoolbox` 默认 GOP 只有约 12 帧（0.5s），必须同时给：
   - `-g <fps × 分片秒数>`（23.976fps + 6s 分片 → 144）
   - `-force_key_frames "expr:gte(t,n_forced*6)"`

   脚本按 `fps × --segment-duration` 自动算 GOP。换用 `libx264` 时还要加 `-sc_threshold 0 -keyint_min <GOP>` 关掉场景切换插帧。

2. **ffmpeg 的 `-t` / `-ss` 对 WebVTT 流不生效**：截取 10 分钟后字幕仍会保留到全片结尾。必须用 `trim_vtt.py` 按 cue 时间戳过滤。

3. **packager 的输入描述符必须带 `in=` 前缀**，否则报 `Unknown field in stream descriptor` 并静默不产出任何分段。

4. **默认轨由语言码 + 顺序决定**：`--default_language` / `--default_text_language` 给匹配语言码的**第一条**流打 `DEFAULT=YES`。所以要把想默认的音轨放在 `--audio` 第一位。

5. **每条音轨/字幕都要独立编成 mezz 再交给 packager**（`hls_group_id` 分组成 `EXT-X-MEDIA`），不要用 ffmpeg 的 `var_stream_map` 一次出多轨，那样很难控制默认轨和字幕组。

6. 字幕显示名会做 `slug` 转换（去括号、空格转下划线），语言码查脚本里的 `lang_of()`；查不到就不带 `lang=`，track 仍会保留。

## 校验

```bash
cd ./output/movie/hls
# 各档段数应完全一致
for d in 240 360 480 720 1080; do echo "$d $(ls $d/*.m4s | wc -l)"; done
# 时长
awk -F: '/#EXTINF/{s+=$2} END{printf "%.2fs\n", s}' 1080/playlist.m3u8
# 关键帧对齐（各档输出应完全相同）
for d in 240 360 480 720 1080; do
  printf "%-7s " "$d"
  ffprobe -v error -select_streams v:0 -skip_frame nokey -show_entries frame=pts_time \
    -of csv=p=0 -read_intervals "%+18" "../mezz/v_$d.mp4" | head -3 | tr '\n' ' '
  echo
done
# 默认轨
grep -E "DEFAULT=YES" master.m3u8
```

浏览器实测：同目录起静态服务后打开 `player.html`（`Cmd+Shift+R` 强刷避免 304 缓存）。

## 下一步：发布到 R2 拿在线 m3u8

拿到 `hls/` 目录后，用 `upload-HLS-to-R2-pipeline` skill 上传并获取在线地址：

```bash
export PATH="$HOME/.local/bin:$PATH"
../upload-HLS-to-R2-pipeline/scripts/upload_hls_to_r2.sh --hls ./output/movie/hls
```

该 skill 负责两趟上传（含 `.vtt` 的 `Content-Type` 修正）、本地/远端字节数核对、线上状态码与 CORS 验证，并直接打印 `master.m3u8` 与 `player.html` 的在线地址。详见 `../upload-HLS-to-R2-pipeline/SKILL.md`。
