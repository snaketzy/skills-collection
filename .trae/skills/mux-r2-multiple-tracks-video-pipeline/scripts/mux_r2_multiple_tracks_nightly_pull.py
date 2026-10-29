#!/usr/bin/env python3
"""
mux_r2_multiple_tracks_nightly_pull.py
=====================================

把 R2 中的一组文件发布成一个 Mux asset：
  - 主视频：*.mp4 / *.mov / *.mkv
  - 外挂音轨：<video_stem>.<label>.mp3 / m4a / aac / wav / flac / ac3 / eac3
  - 外挂字幕：<video_stem>.<label>.srt / vtt

流程：
  1) 列出 R2 桶内对象
  2) 以主视频为中心，把同 stem 的音频/字幕归并成 bundle
  3) 对每个 bundle 创建一个 Mux asset
  4) 等 asset ready 后，追加可切换音轨与字幕轨
  5) 把结果写入 logs/mux_r2_multiple_tracks_pull_results.jsonl

默认字幕说明：
  - 文件名中包含 "default" 或 "defalut" 的字幕会被识别为默认字幕候选
  - Mux 默认字幕通过播放 URL 的 default_subtitles_lang 参数控制
  - 所以脚本会额外输出 hls_url_with_default_subtitles
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import requests


SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_project_dir() -> Path:
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

RESULT_LOG = LOG_DIR / "mux_r2_multiple_tracks_pull_results.jsonl"
RUN_LOG = LOG_DIR / f"mux_r2_multiple_tracks_pull_{time.strftime('%Y%m%d')}.log"

MUX_TOKEN_ID: str | None = None
MUX_TOKEN_SECRET: str | None = None
R2_PUBLIC_DOMAIN: str | None = None
R2_BUCKET: str | None = None
R2_PREFIX = ""

_ENV_PATH = PROJECT_DIR / "cloudflare.env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip().upper()
        _v = _v.strip().strip('"').strip("'")
        if _k == "MUX_TOKEN_ID":
            MUX_TOKEN_ID = _v
        elif _k == "MUX_TOKEN_SECRET":
            MUX_TOKEN_SECRET = _v
        elif _k == "CLOUDFLARE_R2_PUBLIC_DOMAIN":
            R2_PUBLIC_DOMAIN = _v.rstrip("/")
        elif _k == "CLOUDFLARE_R2_BUCKET":
            R2_BUCKET = _v
        elif _k == "CLOUDFLARE_R2_PREFIX":
            R2_PREFIX = _v.strip("/")

MUX_TOKEN_ID = os.environ.get("MUX_TOKEN_ID") or MUX_TOKEN_ID
MUX_TOKEN_SECRET = os.environ.get("MUX_TOKEN_SECRET") or MUX_TOKEN_SECRET

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv")
AUDIO_SUFFIXES = (".m4a", ".aac", ".mp3", ".wav", ".flac", ".ac3", ".eac3")
TEXT_SUFFIXES = (".srt", ".vtt")

MIN_VIDEO_BYTES = 2 * 1024 * 1024
MIN_SIDECAR_BYTES = 16
POLL_INTERVAL_SEC = 12
READY_TIMEOUT_MIN = 240
TRACK_READY_TIMEOUT_MIN = 60
MAX_RESOLUTION_TIER = "2160p"
VIDEO_QUALITY = "basic"

# 主视频内嵌音轨的语言标签（Mux 推荐显式设置，否则播放器里会显示为无语言的 Default）
PRIMARY_AUDIO_LANGUAGE = "en"
PRIMARY_AUDIO_NAME = "English"

DEFAULT_MARKERS = ("default", "defalut")
RCLONE_BIN = shutil.which("rclone") or str(Path.home() / ".local" / "bin" / "rclone")
RCLONE_CONF = str(PROJECT_DIR / "rclone.conf")
RCLONE_PROFILE = "r2"

LANGUAGE_MAP: dict[str, tuple[str, str]] = {
    "arabic": ("ar", "Arabic"),
    "bulgarian": ("bg", "Bulgarian"),
    "catalan": ("ca", "Catalan"),
    "chinese": ("zh", "Chinese"),
    "chinese cantonese": ("yue", "Cantonese"),
    "cantonese": ("yue", "Cantonese"),
    "chinese mandarin": ("cmn", "Mandarin"),
    "mandarin": ("cmn", "Mandarin"),
    "chinese simplified": ("zh-Hans", "Chinese (Simplified)"),
    "simplified chinese": ("zh-Hans", "Chinese (Simplified)"),
    "chinese traditional": ("zh-Hant", "Chinese (Traditional)"),
    "traditional chinese": ("zh-Hant", "Chinese (Traditional)"),
    "chinese traditional hk": ("zh-HK", "Chinese (Traditional, Hong Kong)"),
    "traditional chinese hk": ("zh-HK", "Chinese (Traditional, Hong Kong)"),
    "chinese hong kong": ("zh-HK", "Chinese (Traditional, Hong Kong)"),
    "croatian": ("hr", "Croatian"),
    "czech": ("cs", "Czech"),
    "danish": ("da", "Danish"),
    "dutch": ("nl", "Dutch"),
    "english": ("en", "English"),
    "estonian": ("et", "Estonian"),
    "finnish": ("fi", "Finnish"),
    "french": ("fr", "French"),
    "french parisian": ("fr-FR", "French (France)"),
    "french france": ("fr-FR", "French (France)"),
    "parisian french": ("fr-FR", "French (France)"),
    "french canada": ("fr-CA", "French (Canada)"),
    "french canadian": ("fr-CA", "French (Canada)"),
    "canadian french": ("fr-CA", "French (Canada)"),
    "german": ("de", "German"),
    "greek": ("el", "Greek"),
    "hebrew": ("he", "Hebrew"),
    "hungarian": ("hu", "Hungarian"),
    "icelandic": ("is", "Icelandic"),
    "indonesian": ("id", "Indonesian"),
    "italian": ("it", "Italian"),
    "japanese": ("ja", "Japanese"),
    "korean": ("ko", "Korean"),
    "latvian": ("lv", "Latvian"),
    "lithuanian": ("lt", "Lithuanian"),
    "malay": ("ms", "Malay"),
    "norwegian": ("no", "Norwegian"),
    "persian": ("fa", "Persian"),
    "polish": ("pl", "Polish"),
    "portuguese": ("pt", "Portuguese"),
    "portuguese brazil": ("pt-BR", "Portuguese (Brazilian)"),
    "portuguese brazilian": ("pt-BR", "Portuguese (Brazilian)"),
    "brazilian portuguese": ("pt-BR", "Portuguese (Brazilian)"),
    "portuguese iberian": ("pt-PT", "Portuguese (Iberian)"),
    "portuguese portugal": ("pt-PT", "Portuguese (Iberian)"),
    "european portuguese": ("pt-PT", "Portuguese (Iberian)"),
    "romanian": ("ro", "Romanian"),
    "russian": ("ru", "Russian"),
    "slovak": ("sk", "Slovak"),
    "slovenian": ("sl", "Slovenian"),
    "spanish": ("es", "Spanish"),
    "spanish castilian": ("es-ES", "Spanish (Castilian)"),
    "castilian": ("es-ES", "Spanish (Castilian)"),
    "spanish spain": ("es-ES", "Spanish (Castilian)"),
    "spanish latin america": ("es-419", "Spanish (Latin American)"),
    "spanish latin american": ("es-419", "Spanish (Latin American)"),
    "latin american spanish": ("es-419", "Spanish (Latin American)"),
    "swedish": ("sv", "Swedish"),
    "thai": ("th", "Thai"),
    "turkish": ("tr", "Turkish"),
    "ukrainian": ("uk", "Ukrainian"),
    "vietnamese": ("vi", "Vietnamese"),
}

BCP47_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
# 结尾的重复序号，例如 "French (Canada)-2" / "English 2"
DUPLICATE_SUFFIX_RE = re.compile(r"[\s._-]+\d+$")


@dataclass
class R2Object:
    name: str
    size: int

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()

    @property
    def stem_path(self) -> str:
        return str(Path(self.name).with_suffix(""))


@dataclass
class TrackSpec:
    kind: str
    file_name: str
    size: int
    label: str
    language_code: str
    name: str
    is_default: bool = False

    @property
    def url(self) -> str:
        return build_public_url(self.file_name)


@dataclass
class Bundle:
    video: R2Object
    audio_tracks: list[TrackSpec] = field(default_factory=list)
    subtitle_tracks: list[TrackSpec] = field(default_factory=list)


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with RUN_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def append_result(payload: dict) -> None:
    with RESULT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_public_url(name: str) -> str:
    if not R2_PUBLIC_DOMAIN:
        raise RuntimeError("cloudflare.env 中缺少 CLOUDFLARE_R2_PUBLIC_DOMAIN")
    clean_name = name.lstrip("/")
    path = f"{R2_PREFIX}/{clean_name}" if R2_PREFIX else clean_name
    encoded = quote(path, safe="/")
    return f"{R2_PUBLIC_DOMAIN}/{encoded}"


def normalize_label(label: str) -> str:
    label = label.replace("_", " ").replace(".", " ")
    label = re.sub(r"[\[\]{}()]+", " ", label)
    label = re.sub(r"\s+", " ", label).strip().lower()
    return label


def strip_default_markers(label: str) -> tuple[str, bool]:
    lowered = label.lower()
    is_default = any(marker in lowered for marker in DEFAULT_MARKERS)
    if not is_default:
        return label.strip(" ._-"), False

    cleaned = label
    for marker in DEFAULT_MARKERS:
        cleaned = re.sub(marker, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    return cleaned, True


def resolve_language(label: str) -> tuple[str, str] | None:
    """把文件名标签解析成 (语言码, 展示名)，解析不出返回 None。"""
    normalized = normalize_label(label)
    if not normalized:
        return None
    if normalized in LANGUAGE_MAP:
        return LANGUAGE_MAP[normalized]

    # 去掉结尾的重复序号再试一次，例如 "French (Canada)-2" -> "french canada"
    base = DUPLICATE_SUFFIX_RE.sub("", normalized).strip()
    if base and base != normalized and base in LANGUAGE_MAP:
        return LANGUAGE_MAP[base]
    return None


def infer_language_code_and_name(label: str, kind: str) -> tuple[str, str]:
    resolved = resolve_language(label)
    if resolved:
        return resolved

    # 标签本身就是 BCP-47 语言码时直接采用，例如 "en" / "zh-Hant"
    stripped = label.strip()
    if BCP47_RE.match(stripped):
        return stripped, stripped

    # 兜底：无法识别语言，保留原始标签作为展示名
    return "und", stripped or label


def ensure_unique_track_names(tracks: list[TrackSpec]) -> None:
    seen: dict[str, int] = {}
    for track in tracks:
        base = track.name.strip() or track.label or Path(track.file_name).stem
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            track.name = f"{base} ({count + 1})"
        else:
            track.name = base


def list_r2_objects() -> list[R2Object]:
    if not R2_BUCKET:
        raise RuntimeError("cloudflare.env 中缺少 CLOUDFLARE_R2_BUCKET")

    prefix_arg = f"{RCLONE_PROFILE}:{R2_BUCKET}/{R2_PREFIX}" if R2_PREFIX else f"{RCLONE_PROFILE}:{R2_BUCKET}/"
    cmd = [RCLONE_BIN, "--config", RCLONE_CONF, "lsjson", prefix_arg]
    log(f"📂 读取 R2 桶: {' '.join(cmd)}")

    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(
            f"rclone lsjson 失败 (exit {p.returncode})\n"
            f"STDOUT: {p.stdout[:500]}\nSTDERR: {p.stderr[:500]}"
        )

    data = json.loads(p.stdout or "[]")
    objects: list[R2Object] = []
    skipped = 0
    for item in data:
        name = item.get("Name") or item.get("Path")
        size = int(item.get("Size", 0) or 0)
        if not name:
            continue
        suffix = Path(name).suffix.lower()
        if suffix in VIDEO_SUFFIXES and size >= MIN_VIDEO_BYTES:
            objects.append(R2Object(name=name, size=size))
            continue
        if suffix in AUDIO_SUFFIXES + TEXT_SUFFIXES and size >= MIN_SIDECAR_BYTES:
            objects.append(R2Object(name=name, size=size))
            continue
        skipped += 1
    log(f"   桶中可处理对象: {len(objects)} 个（忽略 {skipped} 个无关/过小文件）")
    return objects


def track_label_from_sidecar(video_name: str, sidecar_name: str) -> str:
    video_prefix = f"{Path(video_name).with_suffix('')}."
    suffix = Path(sidecar_name).suffix
    if not sidecar_name.startswith(video_prefix):
        return Path(sidecar_name).stem
    return sidecar_name[len(video_prefix): -len(suffix)]


def build_bundles(objects: list[R2Object]) -> tuple[list[Bundle], list[str]]:
    videos = [obj for obj in objects if obj.suffix in VIDEO_SUFFIXES]
    sidecars = [obj for obj in objects if obj.suffix in AUDIO_SUFFIXES + TEXT_SUFFIXES]

    bundles = {video.name: Bundle(video=video) for video in videos}
    video_prefixes = sorted(
        ((f"{Path(video.name).with_suffix('')}.", video.name) for video in videos),
        key=lambda item: len(item[0]),
        reverse=True,
    )

    unmatched: list[str] = []
    for sidecar in sidecars:
        matched_video_name = None
        for prefix, video_name in video_prefixes:
            if sidecar.name.startswith(prefix):
                matched_video_name = video_name
                break

        if not matched_video_name:
            unmatched.append(sidecar.name)
            continue

        raw_label = track_label_from_sidecar(matched_video_name, sidecar.name)
        clean_label, is_default = strip_default_markers(raw_label)
        clean_label = clean_label or Path(sidecar.name).stem
        language_code, display_name = infer_language_code_and_name(clean_label, "audio" if sidecar.suffix in AUDIO_SUFFIXES else "text")
        spec = TrackSpec(
            kind="audio" if sidecar.suffix in AUDIO_SUFFIXES else "text",
            file_name=sidecar.name,
            size=sidecar.size,
            label=clean_label,
            language_code=language_code,
            name=display_name or clean_label,
            is_default=is_default if sidecar.suffix in TEXT_SUFFIXES else False,
        )
        bundle = bundles[matched_video_name]
        if spec.kind == "audio":
            bundle.audio_tracks.append(spec)
        else:
            bundle.subtitle_tracks.append(spec)

    for bundle in bundles.values():
        ensure_unique_track_names(bundle.audio_tracks)
        ensure_unique_track_names(bundle.subtitle_tracks)
        _warn_duplicate_languages(bundle)

    return sorted(bundles.values(), key=lambda item: item.video.name), unmatched


def _warn_duplicate_languages(bundle: Bundle) -> None:
    """同一 bundle 内多条轨道映射到同一语言码时给出提示：轨道名已区分，但默认字幕选择会变得不明确。"""
    video_name = Path(bundle.video.name).name
    for tracks, kind in ((bundle.audio_tracks, "音轨"), (bundle.subtitle_tracks, "字幕")):
        duplicates = {
            code: count
            for code, count in Counter(track.language_code for track in tracks).items()
            if count > 1 and code != "und"
        }
        if duplicates:
            detail = ", ".join(f"{code}×{count}" for code, count in sorted(duplicates.items()))
            log(f"   ⚠️  {video_name} 存在重复{kind}语言码: {detail}（轨道名已自动区分）")


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
            for asset in batch:
                passthrough = asset.get("passthrough")
                if passthrough:
                    seen.add(passthrough)
                    seen.add(Path(passthrough).stem)
                    seen.add(f"{passthrough}.mp4")
            if len(batch) < 100:
                break
            page += 1
        log(f"🧭 Mux 已有资产索引 keys: {len(seen)}")
        return seen

    def create_asset_from_video(self, video_url: str, filename: str) -> str:
        video_input: dict[str, str] = {"url": video_url}
        # 显式标注主视频内嵌音轨的语言，便于播放器正确展示音轨列表
        if PRIMARY_AUDIO_LANGUAGE:
            video_input["language_code"] = PRIMARY_AUDIO_LANGUAGE
        if PRIMARY_AUDIO_NAME:
            video_input["name"] = PRIMARY_AUDIO_NAME

        payload = {
            "inputs": [video_input],
            "playback_policies": ["public"],
            "passthrough": filename,
            "video_quality": VIDEO_QUALITY,
            "max_resolution_tier": MAX_RESOLUTION_TIER,
        }
        r = requests.post(f"{self.base}/assets", json=payload, headers=self.headers, timeout=30)
        self._check(r)
        return r.json()["data"]["id"]

    def get_asset(self, asset_id: str) -> dict:
        r = requests.get(f"{self.base}/assets/{asset_id}", headers=self.headers, timeout=20)
        self._check(r)
        return r.json()["data"]

    def create_track(self, asset_id: str, track: TrackSpec) -> str:
        payload = {
            "url": track.url,
            "type": track.kind,
            "language_code": track.language_code,
            "name": track.name,
            "passthrough": track.file_name,
        }
        if track.kind == "text":
            payload["text_type"] = "subtitles"
            payload["closed_captions"] = False
        r = requests.post(f"{self.base}/assets/{asset_id}/tracks", json=payload, headers=self.headers, timeout=30)
        self._check(r)
        return r.json()["data"]["id"]

    def wait_asset_ready(self, asset_id: str) -> str:
        deadline = time.time() + READY_TIMEOUT_MIN * 60
        last_status = None
        while time.time() < deadline:
            data = self.get_asset(asset_id)
            status = data["status"]
            playback_ids = data.get("playback_ids") or []
            playback_id = playback_ids[0]["id"] if playback_ids else None
            if status != last_status:
                log(f"   ⏳ asset {asset_id[:12]}… status={status}" + (f" playback={playback_id[:20]}…" if playback_id else ""))
                last_status = status
            if status == "ready" and playback_id:
                return playback_id
            if status == "errored":
                err = data.get("errors") or {}
                raise RuntimeError(f"主视频转码失败 asset={asset_id}: {err.get('type', '')} / {err.get('message', '')}")
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"asset {asset_id} 超过 {READY_TIMEOUT_MIN}min 未 ready")

    def wait_tracks_ready(self, asset_id: str, track_ids: list[str]) -> None:
        if not track_ids:
            return
        deadline = time.time() + TRACK_READY_TIMEOUT_MIN * 60
        last_summary = None
        while time.time() < deadline:
            data = self.get_asset(asset_id)
            track_map = {track["id"]: track for track in data.get("tracks") or []}
            states = []
            all_ready = True
            for track_id in track_ids:
                track = track_map.get(track_id)
                status = (track or {}).get("status") or "preparing"
                states.append(f"{track_id[:8]}={status}")
                if status == "errored":
                    raise RuntimeError(f"轨道处理失败 asset={asset_id} track={track_id}")
                if status != "ready":
                    all_ready = False
            summary = ", ".join(states)
            if summary != last_summary:
                log(f"   🎚️  轨道状态: {summary}")
                last_summary = summary
            if all_ready:
                return
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"asset {asset_id} 的外挂轨道超过 {TRACK_READY_TIMEOUT_MIN}min 未 ready")


def choose_default_subtitle(bundle: Bundle) -> TrackSpec | None:
    defaults = [track for track in bundle.subtitle_tracks if track.is_default]
    return defaults[0] if defaults else None


def describe_bundle(bundle: Bundle) -> None:
    log(f"   - 主视频: {bundle.video.name}  {bundle.video.size / 1024 / 1024:.2f} MB")
    if bundle.audio_tracks:
        for track in bundle.audio_tracks:
            log(f"       音轨: {track.file_name} -> name={track.name!r}, lang={track.language_code}")
    if bundle.subtitle_tracks:
        for track in bundle.subtitle_tracks:
            default_mark = " [default]" if track.is_default else ""
            log(f"       字幕: {track.file_name} -> name={track.name!r}, lang={track.language_code}{default_mark}")


def run(force: bool = False, dry_run: bool = False) -> int:
    log("=" * 70)
    log("🎬 Nightly: Mux ← Cloudflare R2 多音轨多字幕 Pull 任务开始")
    log(f"   R2 公共域名 : {R2_PUBLIC_DOMAIN}")
    log(f"   R2 桶       : {R2_BUCKET}  前缀: {R2_PREFIX!r}")
    log(f"   结果日志    : {RESULT_LOG}")
    log(f"   --force     : {force}   --dry-run : {dry_run}")
    log("=" * 70)

    if not R2_PUBLIC_DOMAIN:
        log("❌ cloudflare.env 缺少 CLOUDFLARE_R2_PUBLIC_DOMAIN")
        return 2
    if not MUX_TOKEN_ID or not MUX_TOKEN_SECRET:
        log("❌ 缺少 Mux 令牌：请在 cloudflare.env 填 MUX_TOKEN_ID / MUX_TOKEN_SECRET，或设置同名环境变量")
        return 2

    objects = list_r2_objects()
    bundles, unmatched = build_bundles(objects)

    if unmatched:
        log(f"⚠️  有 {len(unmatched)} 个 sidecar 未匹配到主视频：")
        for name in unmatched:
            log(f"   - {name}")

    if not bundles:
        log("✅ R2 中没有找到可处理的主视频")
        return 0

    client = MuxClient(MUX_TOKEN_ID, MUX_TOKEN_SECRET)
    existing_pts = set() if force else client.list_passthroughs()

    todo: list[Bundle] = []
    for bundle in bundles:
        stem = Path(bundle.video.name).stem
        if bundle.video.name in existing_pts or stem in existing_pts:
            log(f"   ⏭  跳过已有 asset: {bundle.video.name!r}")
            continue
        todo.append(bundle)

    if not todo:
        log("✅ 没有新的主视频 bundle 需要处理")
        return 0

    log(f"🎯 本次待处理 bundle: {len(todo)} 个")
    for bundle in todo:
        describe_bundle(bundle)

    if dry_run:
        log("🧪 --dry-run 结束，未提交任何 Mux API")
        return 0

    ok = 0
    fail = 0
    for idx, bundle in enumerate(todo, 1):
        log(f"\n[{idx}/{len(todo)}] 🚚 处理 {bundle.video.name}")
        video_url = build_public_url(bundle.video.name)
        default_subtitle = choose_default_subtitle(bundle)
        t0 = time.time()

        try:
            asset_id = client.create_asset_from_video(video_url, bundle.video.name)
            log(f"        已创建 asset_id={asset_id}")
            playback_id = client.wait_asset_ready(asset_id)

            created_track_ids: list[str] = []
            for track in bundle.audio_tracks + bundle.subtitle_tracks:
                track_id = client.create_track(asset_id, track)
                created_track_ids.append(track_id)
                log(f"        已追加 {track.kind} 轨: {track.name} ({track.language_code}) id={track_id[:12]}…")

            client.wait_tracks_ready(asset_id, created_track_ids)

            base_hls = f"https://stream.mux.com/{playback_id}.m3u8"
            hls_with_default = None
            default_lang = None
            if default_subtitle and default_subtitle.language_code != "und":
                default_lang = default_subtitle.language_code
                hls_with_default = f"{base_hls}?default_subtitles_lang={quote(default_lang, safe='')}"
            elif default_subtitle:
                log(f"        ⚠️  默认字幕 {default_subtitle.file_name} 无法推断语言码，跳过 default_subtitles_lang")

            elapsed = time.time() - t0
            ok += 1
            log(f"        ✅ 完成! 耗时 {elapsed:.0f}s  playback_id={playback_id}")

            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "original_file": bundle.video.name,
                "size_bytes": bundle.video.size,
                "source_url": video_url,
                "asset_id": asset_id,
                "playback_id": playback_id,
                "hls_url": base_hls,
                "hls_url_with_default_subtitles": hls_with_default,
                "default_subtitles_lang": default_lang,
                "audio_tracks": [
                    {
                        "file": track.file_name,
                        "name": track.name,
                        "language_code": track.language_code,
                    }
                    for track in bundle.audio_tracks
                ],
                "subtitle_tracks": [
                    {
                        "file": track.file_name,
                        "name": track.name,
                        "language_code": track.language_code,
                        "default": track.is_default,
                    }
                    for track in bundle.subtitle_tracks
                ],
                "elapsed_sec": round(elapsed, 1),
            }
            append_result(record)

            print(f"\n        // {bundle.video.name}  (asset_id={asset_id})")
            print(f"        'videoUrl': '{hls_with_default or base_hls}',\n", flush=True)
        except Exception as e:
            fail += 1
            log(f"        ❌ 失败: {e}")
            append_result(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "original_file": bundle.video.name,
                    "size_bytes": bundle.video.size,
                    "source_url": video_url,
                    "error": str(e),
                }
            )

    log("=" * 70)
    log(f"📋 夜跑结束：成功 {ok}，失败 {fail}，共 {len(todo)}")
    log(f"   结果明细: {RESULT_LOG}")
    log("=" * 70)
    return 0 if fail == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly: Mux pulls grouped videos, alternate audio tracks, and subtitles from Cloudflare R2"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 Mux 中已有 passthrough，对全部主视频重新创建 asset",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出要处理的 bundle，不真正调用 Mux API",
    )
    return parser.parse_args()


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
