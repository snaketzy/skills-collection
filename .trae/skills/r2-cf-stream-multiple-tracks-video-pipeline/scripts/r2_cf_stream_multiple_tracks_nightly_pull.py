#!/usr/bin/env python3
"""
r2_cf_stream_multiple_tracks_nightly_pull.py
============================================

把 R2 中的一组文件发布成一个 Cloudflare Stream 视频：
  - 主视频：*.mp4 / *.mov / *.mkv
  - 外挂音轨：<video_stem>.<label>.mp3 / m4a / aac / wav / flac / ac3 / eac3
  - 外挂字幕：<video_stem>.<label>.srt / vtt

流程：
  1) 列出 R2 桶内对象
  2) 以主视频为中心，把同 stem 的音频/字幕归并成 bundle
  3) 调用 Cloudflare Stream copy 接口创建主视频
  4) 追加 additional audio tracks
  5) 上传 captions
  6) 把结果写入 logs/r2_cf_stream_multiple_tracks_pull_results.jsonl

Cloudflare Stream 差异：
  - 额外音轨支持 URL copy
  - 字幕不支持 URL copy，脚本会先把字幕下载到临时目录后上传
  - 字幕语言码兼容性比 Mux 严格；脚本会尝试更保守的 Stream 语言码，并在平台未保留某条字幕时给出警告
  - 默认字幕通过 defaultTextTrack（播放器初始化参数）表达，而不是 HLS URL 参数
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


SCRIPT_DIR = Path(__file__).resolve().parent


def _resolve_project_dir() -> Path:
    env = os.environ.get("CF_STREAM_PROJECT_DIR")
    if env:
        return Path(env).expanduser().resolve()
    cwd = Path.cwd()
    if (cwd / "cloudflare.env").exists() or (cwd / "rclone.conf").exists():
        return cwd
    return SCRIPT_DIR


PROJECT_DIR = _resolve_project_dir()
LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

RESULT_LOG = LOG_DIR / "r2_cf_stream_multiple_tracks_pull_results.jsonl"
RUN_LOG = LOG_DIR / f"r2_cf_stream_multiple_tracks_pull_{time.strftime('%Y%m%d')}.log"

CLOUDFLARE_ACCOUNT_ID: str | None = None
CLOUDFLARE_STREAM_API_TOKEN: str | None = None
R2_PUBLIC_DOMAIN: str | None = None
R2_BUCKET: str | None = None
R2_PREFIX = ""
STREAM_ALLOWED_ORIGINS: list[str] = []
STREAM_REQUIRE_SIGNED_URLS = False

_ENV_PATH = PROJECT_DIR / "cloudflare.env"
if _ENV_PATH.exists():
    for _line in _ENV_PATH.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        _k = _k.strip().upper()
        _v = _v.strip().strip('"').strip("'")
        if _k == "CLOUDFLARE_ACCOUNT_ID":
            CLOUDFLARE_ACCOUNT_ID = _v
        elif _k == "CLOUDFLARE_STREAM_API_TOKEN":
            CLOUDFLARE_STREAM_API_TOKEN = _v
        elif _k == "CLOUDFLARE_R2_PUBLIC_DOMAIN":
            R2_PUBLIC_DOMAIN = _v.rstrip("/")
        elif _k == "CLOUDFLARE_R2_BUCKET":
            R2_BUCKET = _v
        elif _k == "CLOUDFLARE_R2_PREFIX":
            R2_PREFIX = _v.strip("/")
        elif _k == "CLOUDFLARE_STREAM_ALLOWED_ORIGINS":
            STREAM_ALLOWED_ORIGINS = [item.strip() for item in _v.split(",") if item.strip()]
        elif _k == "CLOUDFLARE_STREAM_REQUIRE_SIGNED_URLS":
            STREAM_REQUIRE_SIGNED_URLS = _v.lower() in {"1", "true", "yes", "on"}

CLOUDFLARE_ACCOUNT_ID = os.environ.get("CLOUDFLARE_ACCOUNT_ID") or CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_STREAM_API_TOKEN = os.environ.get("CLOUDFLARE_STREAM_API_TOKEN") or CLOUDFLARE_STREAM_API_TOKEN

VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv")
AUDIO_SUFFIXES = (".m4a", ".aac", ".mp3", ".wav", ".flac", ".ac3", ".eac3")
TEXT_SUFFIXES = (".vtt", ".srt")

MIN_VIDEO_BYTES = 2 * 1024 * 1024
MIN_SIDECAR_BYTES = 16
POLL_INTERVAL_SEC = 12
VIDEO_READY_TIMEOUT_MIN = 240
AUDIO_READY_TIMEOUT_MIN = 60
CAPTION_READY_TIMEOUT_MIN = 30

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
    "chinese traditional": ("zh-TW", "Chinese (Traditional)"),
    "traditional chinese": ("zh-TW", "Chinese (Traditional)"),
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
    "portuguese": ("pt-PT", "Portuguese (Iberian)"),
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
DUPLICATE_SUFFIX_RE = re.compile(r"[\s._-]+\d+$")
CAPTION_LISTING_GRACE_SEC = 18


@dataclass
class R2Object:
    name: str
    size: int

    @property
    def suffix(self) -> str:
        return Path(self.name).suffix.lower()


@dataclass
class TrackSpec:
    kind: str
    file_name: str
    size: int
    label: str
    language_code: str
    name: str
    is_default: bool = False
    stream_language_code: str | None = None

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


def append_result(payload: dict[str, Any]) -> None:
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
    return re.sub(r"\s+", " ", label).strip().lower()


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
    normalized = normalize_label(label)
    if not normalized:
        return None
    if normalized in LANGUAGE_MAP:
        return LANGUAGE_MAP[normalized]

    base = DUPLICATE_SUFFIX_RE.sub("", normalized).strip()
    if base and base != normalized and base in LANGUAGE_MAP:
        return LANGUAGE_MAP[base]
    return None


def infer_language_code_and_name(label: str) -> tuple[str, str]:
    resolved = resolve_language(label)
    if resolved:
        return resolved

    stripped = label.strip()
    if BCP47_RE.match(stripped):
        return stripped, stripped

    return "und", stripped or label


def ensure_unique_track_names(tracks: list[TrackSpec]) -> None:
    seen: dict[str, int] = {}
    for track in tracks:
        base = track.name.strip() or track.label or Path(track.file_name).stem
        count = seen.get(base, 0)
        seen[base] = count + 1
        track.name = base if count == 0 else f"{base} ({count + 1})"


def caption_language_candidates(language_code: str) -> list[str]:
    candidates: list[str] = []

    def push(code: str) -> None:
        if code and code not in candidates:
            candidates.append(code)

    push(language_code)
    primary = language_code.split("-", 1)[0]
    if language_code == "und":
        return candidates
    if language_code.startswith("pt-"):
        push("pt-PT")
    if language_code.startswith("zh-") and language_code in {"zh-TW", "zh-Hant"}:
        push("zh-TW")
    if primary != language_code and primary not in {"pt", "zh"}:
        push(primary)
    return candidates


def assign_unique_caption_languages(tracks: list[TrackSpec]) -> None:
    used: set[str] = set()
    for track in tracks:
        chosen = None
        for candidate in caption_language_candidates(track.language_code):
            if candidate not in used:
                chosen = candidate
                break
        if chosen is None:
            track.stream_language_code = None
            log(
                f"   ⚠️  字幕 {track.file_name} 没有可用的唯一 Stream 语言码，"
                "本次将跳过上传"
            )
            continue

        track.stream_language_code = chosen
        used.add(chosen)
        if chosen == track.language_code:
            continue
        if chosen.split("-", 1)[0] == track.language_code.split("-", 1)[0]:
            log(
                f"   ⚠️  字幕 {track.file_name} 与其他字幕共享语言族或平台兼容性不足，"
                f"改用 {chosen}"
            )
        else:
            log(
                f"   ⚠️  字幕 {track.file_name} 与其他字幕共享语言码 {track.language_code}，"
                f"改用 {chosen}"
            )


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
        language_code, display_name = infer_language_code_and_name(clean_label)
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
        assign_unique_caption_languages(bundle.subtitle_tracks)
        _warn_duplicate_languages(bundle)

    return sorted(bundles.values(), key=lambda item: item.video.name), unmatched


def _warn_duplicate_languages(bundle: Bundle) -> None:
    video_name = Path(bundle.video.name).name
    for tracks, kind in ((bundle.audio_tracks, "音轨"), (bundle.subtitle_tracks, "字幕")):
        duplicates = {
            code: count
            for code, count in Counter(track.language_code for track in tracks).items()
            if count > 1 and code != "und"
        }
        if duplicates:
            detail = ", ".join(f"{code}×{count}" for code, count in sorted(duplicates.items()))
            log(f"   ⚠️  {video_name} 存在重复{kind}语言码: {detail}")


class CloudflareStreamClient:
    def __init__(self, account_id: str, api_token: str):
        self.account_id = account_id
        self.base = f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        self.headers = {"Authorization": f"Bearer {api_token}"}

    def _check(self, response: requests.Response) -> Any:
        try:
            payload = response.json()
        except Exception:
            response.raise_for_status()
            return {}
        if response.status_code >= 400 or payload.get("success") is False:
            raise RuntimeError(
                f"Cloudflare API {response.status_code} {response.request.method} {response.url}\n"
                f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
            )
        return payload.get("result")

    def list_videos_by_name(self, file_name: str) -> list[dict[str, Any]]:
        r = requests.get(
            f"{self.base}/stream",
            params={"name": file_name},
            headers=self.headers,
            timeout=30,
        )
        result = self._check(r) or []
        return result if isinstance(result, list) else list(result)

    def create_video_copy(self, video_url: str, file_name: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": video_url,
            "meta": {"name": file_name},
        }
        if STREAM_ALLOWED_ORIGINS:
            payload["allowedOrigins"] = STREAM_ALLOWED_ORIGINS
        if STREAM_REQUIRE_SIGNED_URLS:
            payload["requireSignedURLs"] = True
        r = requests.post(
            f"{self.base}/stream/copy",
            headers={**self.headers, "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=30,
        )
        return self._check(r) or {}

    def get_video(self, uid: str) -> dict[str, Any]:
        r = requests.get(f"{self.base}/stream/{uid}", headers=self.headers, timeout=20)
        return self._check(r) or {}

    def wait_video_ready(self, uid: str) -> dict[str, Any]:
        deadline = time.time() + VIDEO_READY_TIMEOUT_MIN * 60
        last_state = None
        while time.time() < deadline:
            video = self.get_video(uid)
            state = ((video.get("status") or {}).get("state")) or "unknown"
            ready = bool(video.get("readyToStream"))
            if state != last_state:
                log(f"   ⏳ stream video {uid[:12]}… state={state} ready={ready}")
                last_state = state
            if ready and (video.get("playback") or {}).get("hls"):
                return video
            if state == "error":
                status = video.get("status") or {}
                raise RuntimeError(
                    f"Cloudflare Stream 视频处理失败 uid={uid}: "
                    f"{status.get('errorReasonCode', '')} / {status.get('errorReasonText', '')}"
                )
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"Cloudflare Stream 视频 {uid} 超过 {VIDEO_READY_TIMEOUT_MIN}min 未 ready")

    def add_audio_copy(self, uid: str, track: TrackSpec) -> dict[str, Any]:
        payload = {"url": track.url, "label": track.name}
        r = requests.post(
            f"{self.base}/stream/{uid}/audio/copy",
            headers={**self.headers, "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=30,
        )
        return self._check(r) or {}

    def list_audio_tracks(self, uid: str) -> list[dict[str, Any]]:
        r = requests.get(f"{self.base}/stream/{uid}/audio", headers=self.headers, timeout=20)
        result = self._check(r) or {}
        if isinstance(result, dict):
            return result.get("audio") or []
        return result or []

    def wait_audio_ready(self, uid: str, audio_ids: list[str]) -> None:
        if not audio_ids:
            return
        deadline = time.time() + AUDIO_READY_TIMEOUT_MIN * 60
        last_summary = None
        while time.time() < deadline:
            tracks = {item.get("uid"): item for item in self.list_audio_tracks(uid)}
            states = []
            all_ready = True
            for audio_id in audio_ids:
                status = (tracks.get(audio_id) or {}).get("status") or "queued"
                states.append(f"{audio_id[:8]}={status}")
                if status == "error":
                    raise RuntimeError(f"Cloudflare Stream 音轨处理失败 uid={uid} audio={audio_id}")
                if status != "ready":
                    all_ready = False
            summary = ", ".join(states)
            if summary != last_summary:
                log(f"   🎚️  音轨状态: {summary}")
                last_summary = summary
            if all_ready:
                return
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"Cloudflare Stream 音轨超过 {AUDIO_READY_TIMEOUT_MIN}min 未 ready")

    def upload_caption_file(self, uid: str, language: str, file_path: Path) -> dict[str, Any]:
        with file_path.open("rb") as fh:
            files = {"file": (file_path.name, fh, "text/vtt")}
            r = requests.put(
                f"{self.base}/stream/{uid}/captions/{language}",
                headers=self.headers,
                files=files,
                timeout=60,
            )
        return self._check(r) or {}

    def list_captions(self, uid: str) -> list[dict[str, Any]]:
        r = requests.get(f"{self.base}/stream/{uid}/captions", headers=self.headers, timeout=20)
        result = self._check(r) or []
        return result if isinstance(result, list) else list(result)

    def wait_captions_listed(self, uid: str, languages: list[str]) -> tuple[list[str], list[str]]:
        if not languages:
            return [], []
        deadline = time.time() + CAPTION_LISTING_GRACE_SEC
        latest: set[str] = set()
        while time.time() < deadline:
            latest = {item.get("language") for item in self.list_captions(uid) if item.get("language")}
            missing = [language for language in languages if language not in latest]
            if not missing:
                return languages, []
            time.sleep(3)
        present = [language for language in languages if language in latest]
        missing = [language for language in languages if language not in latest]
        return present, missing

    def wait_captions_ready(self, uid: str, languages: list[str]) -> None:
        if not languages:
            return
        deadline = time.time() + CAPTION_READY_TIMEOUT_MIN * 60
        last_summary = None
        while time.time() < deadline:
            captions = {item.get("language"): item for item in self.list_captions(uid)}
            states = []
            all_ready = True
            for language in languages:
                status = (captions.get(language) or {}).get("status") or "inprogress"
                states.append(f"{language}={status}")
                if status == "error":
                    raise RuntimeError(f"Cloudflare Stream 字幕处理失败 uid={uid} language={language}")
                if status != "ready":
                    all_ready = False
            summary = ", ".join(states)
            if summary != last_summary:
                log(f"   📝 字幕状态: {summary}")
                last_summary = summary
            if all_ready:
                return
            time.sleep(POLL_INTERVAL_SEC)
        raise TimeoutError(f"Cloudflare Stream 字幕超过 {CAPTION_READY_TIMEOUT_MIN}min 未 ready")


def choose_default_subtitle(bundle: Bundle) -> TrackSpec | None:
    defaults = [track for track in bundle.subtitle_tracks if track.is_default]
    return defaults[0] if defaults else None


def describe_bundle(bundle: Bundle) -> None:
    log(f"   - 主视频: {bundle.video.name}  {bundle.video.size / 1024 / 1024:.2f} MB")
    for track in bundle.audio_tracks:
        log(f"       音轨: {track.file_name} -> name={track.name!r}, lang={track.language_code}")
    for track in bundle.subtitle_tracks:
        default_mark = " [default]" if track.is_default else ""
        log(
            f"       字幕: {track.file_name} -> name={track.name!r}, "
            f"lang={track.language_code}, stream_lang={track.stream_language_code}{default_mark}"
        )


def build_iframe_url(video: dict[str, Any]) -> str | None:
    preview = video.get("preview")
    if isinstance(preview, str) and preview.endswith("/watch"):
        return preview[:-len("/watch")] + "/iframe"
    hls = ((video.get("playback") or {}).get("hls")) or ""
    if "/manifest/video.m3u8" in hls:
        return hls.replace("/manifest/video.m3u8", "/iframe")
    return None


def maybe_convert_caption_to_vtt(src: Path, dst_dir: Path) -> Path:
    if src.suffix.lower() == ".vtt":
        return src

    content = src.read_text(encoding="utf-8-sig")
    lines = content.splitlines()
    out_lines = ["WEBVTT", ""]
    for line in lines:
        if "-->" in line:
            line = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", line)
        out_lines.append(line)
    dst = dst_dir / (src.stem + ".vtt")
    dst.write_text("\n".join(out_lines).strip() + "\n", encoding="utf-8")
    return dst


def fetch_caption_sidecar(track: TrackSpec, dst_dir: Path) -> Path:
    response = requests.get(track.url, timeout=60)
    response.raise_for_status()
    temp_src = dst_dir / Path(track.file_name).name
    temp_src.write_bytes(response.content)
    return maybe_convert_caption_to_vtt(temp_src, dst_dir)


def run(force: bool = False, dry_run: bool = False) -> int:
    log("=" * 70)
    log("🎬 Nightly: Cloudflare Stream ← R2 多音轨多字幕 Pull 任务开始")
    log(f"   R2 公共域名 : {R2_PUBLIC_DOMAIN}")
    log(f"   R2 桶       : {R2_BUCKET}  前缀: {R2_PREFIX!r}")
    log(f"   结果日志    : {RESULT_LOG}")
    log(f"   --force     : {force}   --dry-run : {dry_run}")
    log("=" * 70)

    if not CLOUDFLARE_ACCOUNT_ID:
        log("❌ cloudflare.env 缺少 CLOUDFLARE_ACCOUNT_ID")
        return 2
    if not CLOUDFLARE_STREAM_API_TOKEN:
        log("❌ cloudflare.env 缺少 CLOUDFLARE_STREAM_API_TOKEN")
        return 2
    if not R2_PUBLIC_DOMAIN:
        log("❌ cloudflare.env 缺少 CLOUDFLARE_R2_PUBLIC_DOMAIN")
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

    client = CloudflareStreamClient(CLOUDFLARE_ACCOUNT_ID, CLOUDFLARE_STREAM_API_TOKEN)

    todo: list[Bundle] = []
    for bundle in bundles:
        exists = [] if force else client.list_videos_by_name(bundle.video.name)
        if exists:
            log(f"   ⏭  跳过已有 Stream 视频: {bundle.video.name!r}")
            continue
        todo.append(bundle)

    if not todo:
        log("✅ 没有新的主视频 bundle 需要处理")
        return 0

    log(f"🎯 本次待处理 bundle: {len(todo)} 个")
    for bundle in todo:
        describe_bundle(bundle)

    if dry_run:
        log("🧪 --dry-run 结束，未提交任何 Cloudflare Stream API")
        return 0

    ok = 0
    fail = 0
    for idx, bundle in enumerate(todo, 1):
        log(f"\n[{idx}/{len(todo)}] 🚚 处理 {bundle.video.name}")
        t0 = time.time()
        video_url = build_public_url(bundle.video.name)
        default_subtitle = choose_default_subtitle(bundle)
        tmp_dir = Path(tempfile.mkdtemp(prefix="cf-stream-sidecars-"))
        try:
            created = client.create_video_copy(video_url, bundle.video.name)
            uid = created.get("uid")
            if not uid:
                raise RuntimeError(f"Cloudflare Stream 创建结果缺少 uid: {created}")
            log(f"        已创建 stream uid={uid}")

            video = client.wait_video_ready(uid)

            audio_ids: list[str] = []
            for track in bundle.audio_tracks:
                audio = client.add_audio_copy(uid, track)
                audio_uid = audio.get("uid")
                if not audio_uid:
                    raise RuntimeError(f"追加音轨返回缺少 uid: {audio}")
                audio_ids.append(audio_uid)
                log(f"        已追加音轨: {track.name} ({track.language_code}) uid={audio_uid[:12]}…")
            client.wait_audio_ready(uid, audio_ids)

            caption_languages: list[str] = []
            for track in bundle.subtitle_tracks:
                stream_lang = track.stream_language_code
                if not stream_lang:
                    log(f"        ⚠️  跳过字幕: {track.file_name}（无可用 Stream 语言码）")
                    continue
                upload_path = fetch_caption_sidecar(track, tmp_dir)
                client.upload_caption_file(uid, stream_lang, upload_path)
                caption_languages.append(stream_lang)
                log(f"        已上传字幕: {track.name} ({stream_lang}) from {track.file_name}")
            listed_languages, missing_languages = client.wait_captions_listed(uid, caption_languages)
            if missing_languages:
                log(
                    "        ⚠️  以下字幕未出现在 Cloudflare Stream 资产中，"
                    f"将不再等待其 ready: {', '.join(missing_languages)}"
                )
            client.wait_captions_ready(uid, listed_languages)

            video = client.get_video(uid)
            playback = video.get("playback") or {}
            hls_url = playback.get("hls")
            dash_url = playback.get("dash")
            iframe_url = build_iframe_url(video)
            preview_url = video.get("preview")
            default_text_track = None
            if (
                default_subtitle
                and default_subtitle.stream_language_code
                and default_subtitle.stream_language_code in listed_languages
            ):
                default_text_track = default_subtitle.stream_language_code
            elif default_subtitle and default_subtitle.stream_language_code:
                log(
                    "        ⚠️  默认字幕未被 Cloudflare Stream 保留，"
                    f"本次不输出 default_text_track={default_subtitle.stream_language_code}"
                )

            elapsed = time.time() - t0
            ok += 1
            log(f"        ✅ 完成! 耗时 {elapsed:.0f}s  uid={uid}")

            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "original_file": bundle.video.name,
                "size_bytes": bundle.video.size,
                "source_url": video_url,
                "uid": uid,
                "hls_url": hls_url,
                "dash_url": dash_url,
                "preview_url": preview_url,
                "iframe_url": iframe_url,
                "default_text_track": default_text_track,
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
                        "stream_language_code": track.stream_language_code,
                        "default": track.is_default,
                    }
                    for track in bundle.subtitle_tracks
                ],
                "elapsed_sec": round(elapsed, 1),
            }
            append_result(record)

            print(f"\n        // {bundle.video.name}  (uid={uid})")
            print(f"        'hlsUrl': '{hls_url}',")
            print(f"        'iframeUrl': '{iframe_url}',")
            print(f"        'defaultTextTrack': '{default_text_track}',\n", flush=True)
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
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    log("=" * 70)
    log(f"📋 夜跑结束：成功 {ok}，失败 {fail}，共 {len(todo)}")
    log(f"   结果明细: {RESULT_LOG}")
    log("=" * 70)
    return 0 if fail == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly: Cloudflare Stream pulls grouped videos, additional audio tracks, and subtitles from R2"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 Cloudflare Stream 中已有同名 meta.name，对全部主视频重新创建视频",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出要处理的 bundle，不真正调用 Cloudflare Stream API",
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
