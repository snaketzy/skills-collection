---
name: upload-HLS-to-R2-pipeline
description: Upload a locally generated multi-bitrate multi-track HLS directory to Cloudflare R2 and return the public m3u8 URL. Use when asked to publish local HLS output to R2, fix WebVTT content-type on R2, verify an online manifest, or hand a player an https m3u8. Do not use for Mux or Cloudflare Stream ingestion, nor for producing the HLS itself (use encode-video-to-hls-pipeline).
---

# 本地 HLS 目录 -> Cloudflare R2 -> 在线 m3u8

把 `encode-video-to-hls-pipeline` 产出的 `hls/` 目录整体传到 R2，拿到可直接喂给播放器的 https m3u8。

```text
<本地> output/movie/hls/            ->  <R2> r2:<bucket>/<prefix>/
├── master.m3u8                         master.m3u8
├── v240|240/…  v360|360/…  …           （原样保持目录结构）
├── a_English_5_1/…  s_English/…
├── player.html                         player.html   ← 同源网页播放入口
└── hls.min.js
```

主流程用 `scripts/upload_hls_to_r2.sh`，一条命令完成「上传 + 核对 + 线上验证」。

## 自带文件

| 路径 | 用途 |
|---|---|
| `scripts/upload_hls_to_r2.sh` | 主流程：两趟上传、字节数比对、线上 content-type / CORS 验证、输出在线地址 |
| `assets/rclone.conf.example` | rclone `[r2]` profile 模板 |
| `assets/cloudflare.env.example` | 桶名 / 前缀 / 公共域名 配置模板 |

## 用法

```bash
export PATH="$HOME/.local/bin:$PATH"
S=.trae/skills/upload-HLS-to-R2-pipeline

"$S/scripts/upload_hls_to_r2.sh" \
  --hls ./output/movie/hls \
  --bucket my-bucket \
  --prefix test/winnie/hls \
  --public-domain https://pub-xxxxxxxx.r2.dev \
  --config ./rclone.conf
```

未显式给的 `--bucket` / `--prefix` / `--public-domain` / `--config`，会从 `$R2_HLS_PROJECT_DIR`（默认当前目录）下的 `cloudflare.env` / `rclone.conf` 读取，所以配置齐全时只需：

```bash
cp "$S/assets/rclone.conf.example"    ./rclone.conf      # 填 R2 Access Key
cp "$S/assets/cloudflare.env.example" ./cloudflare.env   # 填桶名 / 前缀 / 公共域名
"$S/scripts/upload_hls_to_r2.sh" --hls ./output/movie/hls
```

其它参数：`--dry-run` 只列出待传文件；`--skip-upload` 跳过上传、只做核对与线上验证（排查线上问题时用）。

依赖：`rclone`（含 R2 的 S3 兼容配置）、`curl`。

## 为什么必须分两趟

`--header-upload` 是**整个传输**级别的，不能按扩展名分别设置。而 `.vtt` 必须显式改类型：

| 扩展名 | 线上 Content-Type | 来源 |
|---|---|---|
| `.m3u8` | `application/x-mpegurl` | rclone 依系统 `mime.types` 自动识别，正常 |
| `.m4s` | `application/octet-stream` | 无所谓，播放器按 fMP4 字节解复用 |
| `.mp4` | `video/mp4` | 自动 |
| `.vtt` | **需手工设为 `text/vtt`** | 自动识别会给成 `application/octet-stream` |
| `.html` / `.js` | `text/html` / `text/javascript` | 自动 |

所以第 1 趟用 `--exclude "*.vtt"` 走默认识别，第 2 趟用 `--include "*.vtt" --ignore-times --header-upload "Content-Type: text/vtt"` 单独重传。

## 必须遵守的规则

1. **第 2 趟漏掉 `--ignore-times` 等于没做**：rclone 默认按「大小 + 修改时间」判重，文件已在桶里就直接跳过，`--header-upload` 根本不生效，`.vtt` 仍是 `application/octet-stream`。必须用 `--ignore-times` 强制重传。

2. **不要用 `--metadata-set "content-type=text/vtt"`**：实测无效（仍落成 `application/octet-stream`）。只有 `--header-upload` 有效。

3. **可用 `--files-from` 单独重传指定文件，但里面必须是相对源目录的路径**（如 `s_English/1.vtt`）。传绝对路径会得到 `There was nothing to transfer` 且远端什么都没有。

4. **别把 rclone 输出接 `| tee` / `| tail` 看进度**：长时上传下管道缓冲会让终端看起来「假死」。让 rclone 直接输出，或另开终端用 `rclone size <remote> --json` 观察对象数与字节数增长。

5. **`r2.dev` 公共域不支持 CORS**：实测 `OPTIONS` 预检返回 403、响应里没有 `Access-Control-Allow-Origin`。因此
   - ✅ 桌面播放器（IINA / VLC / mpv / ffplay / PotPlayer）—— 无 CORS 概念，直接用 m3u8 地址
   - ✅ 浏览器直接打开 `https://<公共域>/<前缀>/player.html` —— 页面与分片同源，不需要 CORS
   - ❌ 本地 localhost 页面 + hls.js 跨源拉 R2 —— 会被浏览器拦截
   要支持第三种，需给桶绑定自定义域名并在 Cloudflare 侧配置 CORS 规则。

6. **`.DS_Store` 要排除**，否则会被当成内容差异，让 `rclone check` 报「1 files missing」。

## 校验

脚本结尾会自动打印三块结果：

```text
=== 本地 vs 远端 ===        本地/远端的文件数与字节数应完全一致
=== 线上状态码与 Content-Type ===  master.m3u8 / 首个分片 playlist / init.mp4 /
                                  字幕 playlist / player.html / 末段分片 应为 200
=== CORS ===               OPTIONS 与 ACAO，判断能否跨源
```

手工补测：

```bash
# 内容一致性（--size-only 足够，避免 ETag 分片差异误报）
rclone --config rclone.conf check ./output/movie/hls "r2:<bucket>/<prefix>/" --size-only --one-way

# 任意单文件类型
curl -sI "https://<公共域>/<前缀>/s_English/1.vtt" | grep -i content-type

# manifest 里的轨道与档位
curl -s "https://<公共域>/<前缀>/master.m3u8" | grep -E "EXT-X-MEDIA|EXT-X-STREAM-INF"
```

## 增量重传

HLS 重新编码后（例如加了档位），直接重跑同一个脚本即可：`copy` 是幂等的，只会补齐差异对象，全量重跑也不会重复占用空间。

## 产物消费

把 `https://<公共域>/<前缀>/master.m3u8` 填进播放器/App 的地址字段。多码率、多音轨、多字幕都在同一个 master 里，由播放器自适应与切换，无需为每档分别取地址。
