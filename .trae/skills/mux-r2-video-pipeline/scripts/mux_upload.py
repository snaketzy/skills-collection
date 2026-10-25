import os
import re
import sys
import time
import base64
import json
import shutil
import signal
import subprocess
import requests
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# Mux 令牌：优先从项目根 cloudflare.env 读，其次用同名环境变量覆盖（见下方解析）
MUX_TOKEN_ID = None
MUX_TOKEN_SECRET = None

VIDEO_DIR = "./video"
MAX_WORKERS = 4
MP4_SUFFIXES = (".mp4", ".mov", ".mkv")

MAX_RESOLUTION_TIER = "2160p"
VIDEO_QUALITY = "basic"

# ============================================================
# 🌐 提速开关（最重要的一行）
# 有科学上网/代理的填写，没有就留 None，也能跑
# 常见代理端口：Clash/V2Ray 默认 7890；Surge 默认 6152；Charles 8888
# 例：HTTP_PROXY = "http://127.0.0.1:7890"
HTTP_PROXY = None  # "http://127.0.0.1:7890"

# ============================================================
# ☁️ Cloudflare R2 自动配置（Pull 模式专用）
# ============================================================
# 从项目根目录 cloudflare.env 读取，R2 上传后自动拼接公开 URL
# 不用手动填 PULL_URLS 了，脚本会按 {R2_PUBLIC_DOMAIN}/{文件名} 自动生成
def _resolve_project_dir() -> Path:
    """配置根目录：环境变量 MUX_R2_PROJECT_DIR > 当前工作目录（含 cloudflare.env）> 脚本目录。

    便于把脚本放到 skill 的 scripts/ 里，在任意项目根加载该项目自己的配置。
    """
    env = os.environ.get("MUX_R2_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "cloudflare.env").exists():
        return cwd
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project_dir()
_ENV_PATH = PROJECT_DIR / "cloudflare.env"
R2_PUBLIC_DOMAIN = None
R2_PREFIX = ""          # 如果 R2 里存到子目录如 videos/，这里填 "videos/"，末尾要 /
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
        elif _k == "MUX_TOKEN_ID":
            MUX_TOKEN_ID = _v
        elif _k == "MUX_TOKEN_SECRET":
            MUX_TOKEN_SECRET = _v

MUX_TOKEN_ID = os.environ.get("MUX_TOKEN_ID") or MUX_TOKEN_ID
MUX_TOKEN_SECRET = os.environ.get("MUX_TOKEN_SECRET") or MUX_TOKEN_SECRET


# ============================================================
# 🚀 PULL 模式（强烈推荐，速度是直传的 10~100 倍）
# ============================================================
# 用法 1 (自动)：配置好 R2_PUBLIC_DOMAIN 后，VIDEO_DIR 下所有 mp4 会自动拼接 URL
# 用法 2 (手动)：下面字典里写 文件名 -> 公开 URL，优先级最高
PULL_URLS: dict[str, str] = {
    # "test1.mp4": "https://pub-xxx.r2.dev/test1.mp4",
}

if R2_PUBLIC_DOMAIN:
    # 自动补充 PULL_URLS：只填那些没手动覆盖的
    _auto_videos = sorted(
        p for p in Path(VIDEO_DIR).iterdir()
        if p.is_file() and p.suffix.lower() in MP4_SUFFIXES
    ) if Path(VIDEO_DIR).exists() else []
    for _p in _auto_videos:
        if _p.name not in PULL_URLS or not PULL_URLS[_p.name]:
            PULL_URLS[_p.name] = f"{R2_PUBLIC_DOMAIN}/{R2_PREFIX}{_p.name}"


class MuxClient:
    def __init__(self, token_id, token_secret):
        self.auth = base64.b64encode(f"{token_id}:{token_secret}".encode()).decode()
        self.headers = {
            "Authorization": f"Basic {self.auth}",
            "Content-Type": "application/json",
        }
        self.base_url = "https://api.mux.com/video/v1"

    def _check_error(self, resp):
        if resp.status_code >= 400:
            try:
                err_detail = resp.json()
            except Exception:
                err_detail = resp.text
            raise RuntimeError(
                f"API {resp.status_code} {resp.request.method} {resp.url}\n"
                f"响应: {json.dumps(err_detail, indent=2, ensure_ascii=False)}"
            )

    # -------------------- Direct Upload 模式（本地推） --------------------
    def create_direct_upload(self, file_path: Path) -> dict:
        payload = {
            "new_asset_settings": {
                "playback_policy": ["public"],
                "passthrough": file_path.stem,
                "video_quality": VIDEO_QUALITY,
                "max_resolution_tier": MAX_RESOLUTION_TIER,
            },
            "cors_origin": "*",
        }
        resp = requests.post(
            f"{self.base_url}/uploads", json=payload, headers=self.headers
        )
        self._check_error(resp)
        data = resp.json()["data"]
        return {
            "upload_id": data["id"],
            "upload_url": data["url"],
            "timeout": data.get("timeout", 3600),
        }

    def upload_file_curl(self, upload_url: str, file_path: Path) -> None:
        """用 curl 执行 PUT：HTTP/2 + 大 buffer + 可选代理 + 速度远胜 requests"""
        if not shutil.which("curl"):
            raise RuntimeError("未找到 curl，请安装后重试")

        size_mb = file_path.stat().st_size / (1024 * 1024)
        print(
            f"     🌐 上传中 (curl HTTP/2"
            + (f", 代理: {HTTP_PROXY}" if HTTP_PROXY else ", 直连")
            + f") ...",
            flush=True,
        )

        t0 = time.time()
        cmd = [
            "curl",
            "-sS",
            "--http2",                       # 多路复用，跨境表现更好
            "--tcp-nodelay",                 # 禁用 Nagle 算法
            "--no-keepalive",
            "--connect-timeout", "30",
            "--max-time", str(int(max(3600, size_mb * 20))),  # 超大超时
            "-X", "PUT",
            "-T", str(file_path),
            "-H", "Content-Type: application/octet-stream",
            "-H", "Expect:",                 # 关闭 Expect: 100-continue 省一次 RTT
            "-w", "\n%{http_code}\t%{size_upload}\t%{time_total}\t%{speed_upload}\n",
            upload_url,
        ]

        env = os.environ.copy()
        if HTTP_PROXY:
            # curl 本身不走 requests 代理，要显式传环境变量
            env["HTTPS_PROXY"] = HTTP_PROXY
            env["HTTP_PROXY"] = HTTP_PROXY
            env["ALL_PROXY"] = HTTP_PROXY
            cmd[1:1] = ["-x", HTTP_PROXY]     # 也直接传 -x 更稳

        # 用 timeout 防止 curl 卡死
        try:
            completed = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=int(max(3600, size_mb * 25)),
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError("curl 上传超时")

        # curl -w 格式：最后一行是 http_code\tsize_upload\ttime_total\tspeed_upload
        lines = [l for l in completed.stdout.strip().splitlines() if l.strip()]
        if not lines:
            raise RuntimeError(f"curl 无输出: {completed.stderr[:400]}")
        stats = lines[-1].split("\t")
        if len(stats) < 4:
            raise RuntimeError(f"curl 异常输出: {completed.stdout[-500:]}\nSTDERR: {completed.stderr[:500]}")
        http_code, size_uploaded, time_total, speed_upload = stats
        dt = time.time() - t0
        mbps = (float(speed_upload) * 8) / (1024 * 1024) if float(speed_upload) else 0

        if not http_code.startswith("2"):
            raise RuntimeError(
                f"curl 上传失败 HTTP {http_code}: {completed.stderr[:500]}"
            )
        print(
            f"     ✔️  上传完成: {float(size_uploaded)/1048576:.1f} MB / "
            f"{float(time_total):.1f} s, 平均 {mbps:.2f} Mbps",
            flush=True,
        )

    def wait_for_asset_created(self, upload_id: str, timeout_min: int = 30) -> str:
        deadline = time.time() + timeout_min * 60
        while time.time() < deadline:
            resp = requests.get(
                f"{self.base_url}/uploads/{upload_id}", headers=self.headers
            )
            self._check_error(resp)
            data = resp.json()["data"]
            status = data["status"]
            asset_id = data.get("asset_id")
            if asset_id:
                return asset_id
            if status == "errored":
                raise RuntimeError(f"上传会话 {upload_id} 出错")
            time.sleep(4)
        raise TimeoutError(f"上传会话 {upload_id} 关联资产超时")

    # -------------------- URL Pull 模式（Mux 拉取，推荐） --------------------
    def create_asset_by_url(self, video_url: str, file_path: Path) -> str:
        """直接给 Mux 一个 URL，让它的服务器从该 URL 拉取，速度极快"""
        payload = {
            "input": [{"url": video_url}],
            "playback_policy": ["public"],
            "passthrough": file_path.stem,
            "video_quality": VIDEO_QUALITY,
            "max_resolution_tier": MAX_RESOLUTION_TIER,
        }
        resp = requests.post(
            f"{self.base_url}/assets", json=payload, headers=self.headers
        )
        self._check_error(resp)
        return resp.json()["data"]["id"]

    # -------------------- 共用：等资产转码完成 --------------------
    def wait_for_asset_ready(self, asset_id: str, timeout_min: int = 180) -> str:
        deadline = time.time() + timeout_min * 60
        last_status = None
        while time.time() < deadline:
            resp = requests.get(
                f"{self.base_url}/assets/{asset_id}", headers=self.headers
            )
            self._check_error(resp)
            data = resp.json()["data"]
            status = data["status"]
            playback_ids = data.get("playback_ids", [])

            if status == "ready" and playback_ids:
                return playback_ids[0]["id"]
            if status == "errored":
                errors = data.get("errors", {})
                raise RuntimeError(
                    f"资产 {asset_id} 转码失败: "
                    f"{errors.get('type', '')} - {errors.get('message', '')}"
                )
            if status != last_status:
                print(f"  ⏳ {asset_id[:8]}... 状态: {status}", flush=True)
                last_status = status
            time.sleep(15)
        raise TimeoutError(f"资产 {asset_id} 等待超时")


def process_single(client: MuxClient, file_path: Path) -> dict:
    """处理单个视频：自动选择 Pull 模式 or Direct Upload 模式"""
    size_mb = file_path.stat().st_size / (1024 * 1024)

    if file_path.name in PULL_URLS and PULL_URLS[file_path.name]:
        pull_url = PULL_URLS[file_path.name]
        print(
            f"🎬 Pull 模式: {file_path.name} ({size_mb:.1f} MB)\n"
            f"     源地址: {pull_url[:80]}...",
            flush=True,
        )
        asset_id = client.create_asset_by_url(pull_url, file_path)
        print(f"   ✅ Mux 已开始拉取，asset_id={asset_id[:12]}...")
    else:
        print(
            f"📤 Push 模式: {file_path.name} ({size_mb:.1f} MB)",
            flush=True,
        )
        upload_info = client.create_direct_upload(file_path)
        print(f"   ✅ 创建上传会话: {upload_info['upload_id'][:12]}...")
        client.upload_file_curl(upload_info["upload_url"], file_path)
        print(f"   ✅ 文件推送完成，等待 Mux 接收...")
        asset_id = client.wait_for_asset_created(upload_info["upload_id"])
        print(f"   ✅ 资产已创建: {asset_id[:12]}...")

    print(f"   🎞️  转码处理中... (耐心等待，2GB 视频约 10~30 min)")
    playback_id = client.wait_for_asset_ready(asset_id)
    stream_url = f"https://stream.mux.com/{playback_id}.m3u8"
    print(f"   🎉 播放就绪: {stream_url}\n", flush=True)

    return {
        "original_file": file_path.name,
        "asset_id": asset_id,
        "playback_id": playback_id,
        "hls_url": stream_url,
    }


def main():
    if not MUX_TOKEN_ID or not MUX_TOKEN_SECRET:
        print("❌ 缺少 Mux 令牌：请在 cloudflare.env 填 MUX_TOKEN_ID / MUX_TOKEN_SECRET，或设置同名环境变量")
        sys.exit(1)

    if not os.path.exists(VIDEO_DIR):
        print(f"❌ 视频目录不存在: {VIDEO_DIR}")
        sys.exit(1)

    client = MuxClient(MUX_TOKEN_ID, MUX_TOKEN_SECRET)

    video_files = sorted(
        p for p in Path(VIDEO_DIR).iterdir()
        if p.is_file() and p.suffix.lower() in MP4_SUFFIXES
    )

    if not video_files:
        print("❌ 目录中没有找到视频文件")
        sys.exit(1)

    pull_count = sum(1 for f in video_files if f.name in PULL_URLS and PULL_URLS[f.name])
    push_count = len(video_files) - pull_count

    print("=" * 70)
    print("⚡ iSEE -> Mux 批量上传加速版")
    print("=" * 70)
    print(f"📂 视频目录   : {VIDEO_DIR}  (共 {len(video_files)} 个文件)")
    print(f"🔀 并发数     : {MAX_WORKERS}")
    print(f"🌐 代理       : {HTTP_PROXY or '未使用 (直连跨境)'}")
    print(f"🚚 Pull 模式  : {pull_count} 个 (Mux 拉取, 推荐)")
    print(f"📦 Push 模式  : {push_count} 个 (本地上传, 较慢)")
    print("=" * 70 + "\n")

    results = []
    failed_files = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_map = {executor.submit(process_single, client, f): f for f in video_files}
        for future in as_completed(future_map):
            file = future_map[future]
            try:
                results.append(future.result())
            except Exception as e:
                failed_files.append(file.name)
                print(f"   ❌ 失败 {file.name}: {str(e)}\n", flush=True)

    print("\n" + "=" * 70)
    print(f"📋 完成! 成功 {len(results)} 个 / 失败 {len(failed_files)} 个")
    if failed_files:
        print(f"⚠️  失败列表: {', '.join(failed_files)}")
    print("=" * 70)

    if results:
        print("\n👇 以下 HLS URL 可直接复制进 mock_data.dart:\n")
        for r in results:
            print(f"// {r['original_file']}")
            print(f"// asset_id   : {r['asset_id']}")
            print(f"// playback_id: {r['playback_id']}")
            print(f"'videoUrl': '{r['hls_url']}',\n")


if __name__ == "__main__":
    # 优雅退出，线程池不会卡住
    signal.signal(signal.SIGINT, lambda s, f: (print("\n⏹️  用户中止"), sys.exit(1)))
    main()
