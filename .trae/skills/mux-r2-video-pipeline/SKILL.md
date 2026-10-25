---
name: mux-r2-video-pipeline
description: Publish videos to Mux through Cloudflare R2. Use when asked to batch upload local videos to R2, run or schedule the R2-to-Mux pull pipeline, or transcode videos into Mux HLS. Do not use for direct local upload to Mux.
---

# Mux R2 视频发布流水线

把「本地视频 → R2 → Mux → HLS」串成一个可复用流程。本 skill 自带脚本与配置模板，可复制到任意项目使用，无需手抄脚本。

## 自带文件

| 路径 | 用途 |
|---|---|
| `scripts/mux_r2_nightly_pull.py` | 主流程：列 R2 差集 → Mux Pull → 轮询转码到 ready |
| `scripts/mux_upload.py` | 备用交互上传：Pull / Direct 双模式，逐文件观察进度 |
| `assets/cloudflare.env.example` | R2 + Mux 令牌配置模板 |
| `assets/rclone.conf.example` | rclone `[r2]` profile 模板 |
| `assets/com.mux-r2.nightly-pull.plist.template` | launchd 定时模板（每天 23:00） |

## 在新项目启用（自举步骤）

设 `SKILL_DIR` 为本 skill 目录，`PROJ` 为目标项目根。

1. 准备配置（复制模板并填入真实值）：

```bash
cp "$SKILL_DIR/assets/cloudflare.env.example" "$PROJ/cloudflare.env"
cp "$SKILL_DIR/assets/rclone.conf.example"    "$PROJ/rclone.conf"
```

2. 放视频：`mkdir -p "$PROJ/video"`，把待发布视频放进去（也支持非本地目录，直接放 R2 桶即可）。

3. 运行（**CWD 必须是项目根**，脚本据此读取该项目的配置）：

```bash
cd "$PROJ"
python3 -u "$SKILL_DIR/scripts/mux_r2_nightly_pull.py" --dry-run   # 先看待抓列表
python3 -u "$SKILL_DIR/scripts/mux_r2_nightly_pull.py"             # 正式执行
```

依赖：`python3` 与 `requests`；rclone 二进制位于 `~/.local/bin/rclone` 或在 PATH 中。

## 步骤 1 本地批量传 R2

```bash
export PATH="$HOME/.local/bin:$PATH"
cd "$PROJ"
rclone --config rclone.conf copy ./video/ r2:<桶名>/ \
  --ignore-existing \
  --transfers 7 --checkers 8 \
  --s3-upload-concurrency 32 --s3-chunk-size 8M \
  --stats 10s --stats-one-line -v
```

验证：`rclone --config rclone.conf lsjson r2:<桶名>/`

## 步骤 2 + 3 Mux 拉取并转码

`mux_r2_nightly_pull.py` 的行为：

- 列 R2 桶对象 → 列 Mux 已有 `passthrough`（同时索引「文件名」与「stem」两形态）→ 差集即新文件
- 对每个新文件 `POST /assets`：`input=[{url}]`、`video_quality=<脚本里的 VIDEO_QUALITY>`、`max_resolution_tier=2160p`、`passthrough=<完整文件名>`（`video_quality` / `max_resolution_tier` 定义在脚本顶部常量，默认 `basic` + `2160p`）
- 轮询 `GET /assets/{id}` 直到 `status=ready`，取 `playback_ids[0]`
- 结果追加写 `<PROJ>/logs/mux_r2_pull_results.jsonl`，并打印 `'videoUrl': 'https://stream.mux.com/<pid>.m3u8'`

参数：默认只抓新文件；`--dry-run` 只列清单；`--force` 忽略去重全部重抓。

## 定时（每天 23:00）

用 `assets/com.mux-r2.nightly-pull.plist.template`，替换 `__PYTHON__` / `__SKILL_DIR__` / `__PROJECT_DIR__` / `__HOME__` 后：

```bash
cp com.mux-r2.nightly-pull.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.mux-r2.nightly-pull.plist 2>/dev/null
launchctl load   ~/Library/LaunchAgents/com.mux-r2.nightly-pull.plist
launchctl start  com.mux-r2.nightly-pull   # 立刻验证，不必等到 23:00
```

## 配置查找规则

脚本按以下顺序定位配置根目录（存放 `cloudflare.env` / `rclone.conf` / `logs/`）：

1. 环境变量 `MUX_R2_PROJECT_DIR`
2. 当前工作目录（其中已存在 `cloudflare.env` 或 `rclone.conf`）
3. 脚本自身所在目录（旧行为）

所以既可「从 skill 目录运行、CWD 指向目标项目」，也可「把脚本复制到项目根就地运行」。用 `MUX_R2_PROJECT_DIR=/path/to/proj` 可在任意 CWD 显式指定。

## 凭证

Mux 令牌与 R2 凭证统一放在 `<PROJ>/cloudflare.env`：

- `MUX_TOKEN_ID` / `MUX_TOKEN_SECRET` —— 也可用同名环境变量覆盖（环境变量优先）
- `CLOUDFLARE_R2_PUBLIC_DOMAIN` —— 拼 Pull URL 用
- `CLOUDFLARE_R2_BUCKET` —— rclone 列桶用

`cloudflare.env` / `rclone.conf` / `logs/` 必须加入目标项目 `.gitignore`，切勿提交。

## 关键坑

- **4K 只出 1080p**：Mux 默认 `encoding_tier=baseline` 封顶 1080p。要出 2160p / 1440p 必须 `video_quality=premium`（旧字段名 `encoding_tier`）+ `resolution_tier=2160p`。且源码率要足够（约 ≥16 Mbps），否则 Mux 不会生成高码率档。
- **r2.dev HEAD 403**：S3 兼容的 HEAD 签名严格会返回 403，但 GET（含 Range）正常返回 200/206；Mux Pull 用 GET，不受影响。
- **看不到刚上传的文件**：multipart 上传在 `CompleteMultipartUpload` 返回前，`lsjson` 看不到该对象。看到「缺失」不代表失败，等几分钟再查，并用对象 `Size` 与本地字节数对齐（相差 <1KB 视为一致）。
- **进度显示假死**：不要用 `--progress | tee`（tty 进度条与管道缓冲冲突）。让 rclone 直接输出到终端，或另开终端用 `lsjson` 查状态。

## 产物消费

把 `<PROJ>/logs/mux_r2_pull_results.jsonl` 中的 `hls_url` 填入应用代码的视频地址字段。各画质档位指向同一个 `.m3u8`，由 Mux 内部做自适应码率切换。
