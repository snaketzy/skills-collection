#!/usr/bin/env python3
"""按时间戳裁剪 WebVTT，并规范化输出。

ffmpeg 的 -t / -ss 对 webvtt 流不生效（输出仍会保留整条字幕时间轴），
所以裁剪必须自己按 cue 时间戳过滤。

用法:
    trim_vtt.py <src.vtt> <dst.vtt> [limit_seconds]

limit_seconds <= 0 或省略时不做裁剪，仅做「去 BOM + 统一换行 + 丢空块」的规范化。
"""

import sys


def to_seconds(stamp):
    parts = stamp.split(":")
    if len(parts) == 3:
        h, m, rest = parts
    elif len(parts) == 2:
        h, (m, rest) = "0", parts
    else:
        raise ValueError("无法解析时间戳: %r" % stamp)
    return int(h) * 3600 + int(m) * 60 + float(rest)


def main():
    if len(sys.argv) < 3:
        sys.stderr.write(__doc__)
        return 2

    src, dst = sys.argv[1], sys.argv[2]
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 0.0

    text = open(src, encoding="utf-8-sig").read().replace("\r\n", "\n")

    blocks, cur = [], []
    for line in text.split("\n"):
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    if cur:
        blocks.append(cur)

    out = ["WEBVTT", ""]
    for block in blocks:
        idx = next((i for i, ln in enumerate(block) if "-->" in ln), None)
        if idx is None:
            continue  # 头部元信息块或空块
        if limit > 0 and to_seconds(block[idx].split("-->")[0].strip()) >= limit:
            continue
        out.extend(block)
        out.append("")

    with open(dst, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
