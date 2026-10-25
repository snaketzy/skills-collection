#!/usr/bin/env python3
"""
mux_r2_nightly_pull.py
======================

每晚 23:00 由 launchd / cron 触发：
  1) 列出 Cloudflare R2 指定桶内的视频文件（mp4/mov/mkv）
  2) 列出 Mux 中已存在的资产 passthrough（默认存完整文件名）
  3) 对「R2 有，但 Mux 中 passthrough 未出现过」的文件发起 URL Pull
  4) 轮询等待 ready，把 (original_file, asset_id, playback_id, hls_url)
     追加写入 JSONL 结果日志，一行一条，后续可被任何流程消费

使用：
  python3 mux_r2_nightly_pull.py              # 正常跑（只抓新文件）
  python3 mux_r2_nightly_pull.py --force      # 忽略去重，对 R2 全部文件再抓一次
  python3 mux_r2_nightly_pull.py --dry-run    # 只打印要抓哪些，不真正提交

关键依赖：
  - 项目根目录 cloudflare.env
      MUX_TOKEN_ID / MUX_TOKEN_SECRET             （也可用同名环境变量覆盖）
      CLOUDFLARE_R2_PUBLIC_DOMAIN / CLOUDFLARE_R2_BUCKET
  - ~/.local/bin/rclone + 项目根 rclone.conf 的 [r2] profile（s3 方式访问 R2）
  - python3 requests
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

# ============================================================
# 0. 定位路径（脚本无论被谁 launchd 触发，都以脚本目录为根）
# ============================================================
SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_project_dir() -> Path:
    """配置根目录（存放 cloudflare.env / rclone.conf / logs/ 的地方）。

    解析顺序：
      1) 环境变量 MUX_R2_PROJECT_DIR
      2) 当前工作目录（其中已存在 cloudflare.env 或 rclone.conf）
      3) 回退到脚本自身所在目录（旧行为）

    这样脚本既可在项目根就地运行，也可放到 skill 的 scripts/ 里，
    在任意项目根（CWD）加载该项目自己的配置。
    """
    env = os.environ.get("MUX_R2_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "cloudflare.env").exists() or (cwd / "rclone.conf").exists():
        return cwd
    return SCRIPT_DIR


PROJECT_DIR = _resolve_project_dir()
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RESULT_LOG = LOG_DIR / "mux_r2_pull_results.jsonl"   # 追加写，每晚一行一条
RUN_LOG = LOG_DIR / f"mux_r2_pull_{time.strftime('%Y%m%d')}.log"

# ============================================================
# 1. 凭证 / 配置
# ============================================================
# 1a) Mux 令牌：来自 cloudflare.env 的 MUX_TOKEN_ID / MUX_TOKEN_SECRET，
#     也可用同名环境变量覆盖（环境变量优先）
MUX_TOKEN_ID: str | None = None
MUX_TOKEN_SECRET: str | None = None

# 1b) 从 cloudflare.env 读 R2 + Mux 配置
_ENV_PATH = PROJECT_DIR / "cloudflare.env"
R2_PUBLIC_DOMAIN: str | None = None
R2_BUCKET: str | None = None
R2_PREFIX: str = ""            # 桶里的子目录前缀，例如 "videos/"，末尾要带 "/"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip().upper()
        _v = _v.strip().strip('"').strip("'")
        if _k == "CLOUDFLARE_R2_PUBLIC_DOMAIN":
            R2_PUBLIC_DOMAIN = _v.rstrip("/")
        elif _k == "CLOUDFLARE_R2_BUCKET":
            R2_BUCKET = _v
        elif _k == "MUX_TOKEN_ID":
            MUX_TOKEN_ID = _v
        elif _k == "MUX_TOKEN_SECRET":
            MUX_TOKEN_SECRET = _v

MUX_TOKEN_ID = os.environ.get("MUX_TOKEN_ID") or MUX_TOKEN_ID
MUX_TOKEN_SECRET = os.environ.get("MUX_TOKEN_SECRET") or MUX_TOKEN_SECRET

# 1c) 其它可调项
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv")
MIN_FILE_BYTES = 2 * 1024 * 1024   # 小于 2MB 视为碎片忽略
POLL_INTERVAL_SEC = 12
READY_TIMEOUT_MIN = 240             # 单条资产最多等 4 小时（10GB 级够了）
MAX_CONCURRENT = 3                  # 同一时刻最多并发提交的 Pull（Mux 免费额度别打满）

MAX_RESOLUTION_TIER = "2160p"
VIDEO_QUALITY = "basic"

# 1d) rclone 定位
RCLONE_BIN = shutil.which("rclone") or str(Path.home() / ".local" / "bin" / "rclone")
RCLONE_CONF = str(PROJECT_DIR / "rclone.conf")
RCLONE_PROFILE = "r2"


# ============================================================
# 2. 通用工具
# ============================================================
def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_result(payload: dict) -> None:
    with RESULT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


# ============================================================
# 3. 列 R2 桶（rclone lsjson → 直接拿到 Name / Size / ETag 等）
# ============================================================
def list_r2_objects() -> dict[str, int]:
    """返回 {文件名(不含前缀): 字节数}，只包含非空且为 VIDEO_SUFFIXES 的对象。"""
    if not R2_BUCKET:
        raise RuntimeError("cloudflare.env 中缺少 CLOUDFLARE_R2_BUCKET")

    prefix_arg = f"r2:{R2_BUCKET}/{R2_PREFIX}" if R2_PREFIX else f"r2:{R2_BUCKET}/"
    cmd = [RCLONE_BIN, "--config", RCLONE_CONF, "lsjson", prefix_arg]
    log(f"📂 读取 R2 桶: {' '.join(cmd)}")

    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(
            f"rclone lsjson 失败 (exit {p.returncode})\n"
            f"STDOUT: {p.stdout[:500]}\nSTDERR: {p.stderr[:500]}"
        )

    data = json.loads(p.stdout or "[]")
    result: dict[str, int] = {}
    skipped = 0
    for obj in data:
        name = obj.get("Name") or obj.get("Path")
        size = int(obj.get("Size", 0) or 0)
        if not name:
            continue
        # R2_PREFIX 场景下，rclone 返回的 Name 已经不带前缀，直接用
        lower = name.lower()
        if not lower.endswith(VIDEO_SUFFIXES):
            skipped += 1
            continue
        if size < MIN_FILE_BYTES:
            skipped += 1
            continue
        result[name] = size
    log(f"   桶中有效视频: {len(result)} 个（忽略 {skipped} 个非视频/过小文件）")
    return result


# ============================================================
# 4. 列 Mux 已有资产（按 passthrough 去重；passthrough 存完整文件名）
# ============================================================
class MuxClient:
    def __init__(self, token_id: str, token_secret: str):
        self.auth = base64.b64encode(f"{token_id}:{token_secret}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
        }
        self.base = "https://api.mux.com/video/v1"

    def _check(self, resp: requests.Response) -> None:
        if resp.status_code >= 400:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RuntimeError(
                f"Mux {resp.status_code} {resp.request.method} {resp.url}\n"
                f"{json.dumps(detail, indent=2, ensure_ascii=False)}"
            )

    def list_passthroughs(self) -> set[str]:
        """把 Mux 里所有 passthrough 收集起来。
        注意历史资产的 passthrough 有两种写法：
          - 旧版 (mux_upload.py create_asset_by_url): file_path.stem  (例 "test1")
          - 新版 (用户本次要求):        完整文件名     (例 "test1.mp4")
        所以除了 passthrough 本身，也把 stem 形式加进 seen，两种命中任一种就算已抓过。
        """
        seen: set[str] = set()
        page = 1
        while True:
            r = requests.get(
                f"{self.base}/assets",
                params={"limit": 100, "page": page},
                headers=self.headers,
                timeout=30,
            )
            self._check(r)
            batch = r.json().get("data") or []
            if not batch:
                break
            for a in batch:
                pt = a.get("passthrough")
                if not pt:
                    continue
                seen.add(pt)
                # 再加一份 stem 形态，避免 "test1" vs "test1.mp4" 两边对不上
                stem = Path(pt).stem if "." in pt else f"{pt}.mp4"  # 双向补
                seen.add(stem)
                # 直接构造两种形式
                for alt in (f"{pt}.mp4", Path(pt).stem):
                    if alt and alt != pt:
                        seen.add(alt)
            if len(batch) < 100:
                break
            page += 1
        log(f"🧭 Mux 已有资产数（含 stem/fullname 双形态去重索引）: {len(seen)} keys")
        return seen

    def create_asset_by_url(self, r2_public_url: str, filename: str) -> str:
        """
        passthrough 刻意使用完整原始文件名（含扩展名），与 list_passthroughs 去重匹配。
        """
        payload = {
            "input": [{"url": r2_public_url}],
            "playback_policy": ["public"],
            "passthrough": filename,
            "video_quality": VIDEO_QUALITY,
            "max_resolution_tier": MAX_RESOLUTION_TIER,
        }
        r = requests.post(
            f"{self.base}/assets", json=payload, headers=self.headers, timeout=30
        )
        self._check(r)
        return r.json()["data"]["id"]

    def wait_ready(self, asset_id: str) -> str:
        deadline = time.time() + READY_TIMEOUT_MIN * 60
        last_status = None
        while time.time() < deadline:
            r = requests.get(
                f"{self.base}/assets/{asset_id}", headers=self.headers, timeout=20
            )
            self._check(r)
            d = r.json()["data"]
            status = d["status"]
            pids = d.get("playback_ids") or []
            pid = pids[0]["id"] if pids else None

            if status != last_status:
                log(f"   ⏳ {asset_id[:12]}… status={status}" + (f"  playback={pid[:20]}…" if pid else ""))
                last_status = status

            if status == "ready" and pid:
                return pid
            if status == "errored":
                err = d.get("errors") or {}
                raise RuntimeError(
                    f"转码失败 asset={asset_id}: "
                    f"{err.get('type', '')} / {err.get('message', '')}"
                )
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"asset {asset_id} 超过 {READY_TIMEOUT_MIN}min 未 ready")


# ============================================================
# 5. 主流程
# ============================================================
def run(force: bool = False, dry_run: bool = False) -> int:
    log("=" * 70)
    log("🌙 Nightly: Mux ← Cloudflare R2 Pull 任务开始")
    log(f"   R2 公共域名 : {R2_PUBLIC_DOMAIN}")
    log(f"   R2 桶       : {R2_BUCKET}  前缀: {R2_PREFIX!r}")
    log(f"   结果日志    : {RESULT_LOG}")
    log(f"   --force     : {force}   --dry-run : {dry_run}")
    log("=" * 70)

    if not R2_PUBLIC_DOMAIN:
        log("❌ cloudflare.env 缺少 CLOUDFLARE_R2_PUBLIC_DOMAIN，无法拼接 Pull URL")
        return 2
    if not MUX_TOKEN_ID or not MUX_TOKEN_SECRET:
        log("❌ 缺少 Mux 令牌：请在 cloudflare.env 填 MUX_TOKEN_ID / MUX_TOKEN_SECRET，或设置同名环境变量")
        return 2

    # 5a) 拉两侧列表
    r2_objects = list_r2_objects()
    client = MuxClient(MUX_TOKEN_ID, MUX_TOKEN_SECRET)
    if force:
        existing_pts: set[str] = set()
    else:
        existing_pts = client.list_passthroughs()

    # 5b) 找差集 → 本次要 Pull 的名单
    todo: list[tuple[str, int]] = []
    for name, size in sorted(r2_objects.items()):
        stem = Path(name).stem
        if name in existing_pts or stem in existing_pts:
            log(f"   ⏭  跳过 (Mux 已存在 passthrough={name!r} 或 {stem!r})")
            continue
        todo.append((name, size))

    if not todo:
        log("✅ 没有新文件需要 Pull，直接结束")
        return 0

    log(f"🎯 本次待 Pull: {len(todo)} 个")
    for n, s in todo:
        log(f"   - {n:<40s} {s/1024/1024:>8.2f} MB")

    if dry_run:
        log("🧪 --dry-run 结束，未提交任何 Pull")
        return 0

    # 5c) 逐个 Pull（这里故意串行，省得 Mux 并发限流打你；要并发改 MAX_CONCURRENT 用线程池）
    ok = 0
    fail = 0
    for idx, (name, size) in enumerate(todo, 1):
        pull_url = f"{R2_PUBLIC_DOMAIN}/{R2_PREFIX}{name}"
        log(f"\n[{idx}/{len(todo)}] 🚚 Pull {name}  ({size/1024/1024:.1f} MB)")
        log(f"        URL: {pull_url}")
        t0 = time.time()
        try:
            asset_id = client.create_asset_by_url(pull_url, name)
            log(f"        已创建 asset_id={asset_id}")
            playback_id = client.wait_ready(asset_id)
            hls = f"https://stream.mux.com/{playback_id}.m3u8"
            elapsed = time.time() - t0
            ok += 1
            log(f"        ✅ ready! 耗时 {elapsed:.0f}s  playback_id={playback_id}")
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "original_file": name,
                "size_bytes": size,
                "source_url": pull_url,
                "asset_id": asset_id,
                "playback_id": playback_id,
                "hls_url": hls,
                "elapsed_sec": round(elapsed, 1),
            }
            append_result(record)
            # 也立刻打印成 mock_data.dart 友好的片段
            print(f"\n        // {name}  (asset_id={asset_id})")
            print(f"        'videoUrl': '{hls}',\n", flush=True)
        except Exception as e:
            fail += 1
            log(f"        ❌ 失败: {e}")
            append_result({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "original_file": name,
                "size_bytes": size,
                "source_url": pull_url,
                "error": str(e),
            })

    log("=" * 70)
    log(f"📋 夜跑结束：成功 {ok}，失败 {fail}，共 {len(todo)}")
    log(f"   结果明细: {RESULT_LOG}")
    log("=" * 70)
    return 0 if fail == 0 else 1


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nightly: Mux pulls new videos from Cloudflare R2 bucket")
    p.add_argument("--force", action="store_true",
                   help="忽略 Mux 中已有 passthrough，对全部 R2 视频都再抓一遍（生成新 asset）")
    p.add_argument("--dry-run", action="store_true",
                   help="只列出要 Pull 的列表，不真正提交 Mux API")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        sys.exit(run(force=args.force, dry_run=args.dry_run))
    except KeyboardInterrupt:
        log("⏹  用户中止")
        sys.exit(130)
    except Exception as e:
        log(f"💥 未捕获异常: {e!r}")
        raise
