#!/usr/bin/env python3
"""帧序列 -> mp4,以及抽帧做对比图。

用法:
    python3 encode.py --frames frames --out out/fly.mp4 --fps 30 --crf 20
    python3 encode.py --compare old.png new.png --out out/compare.jpg \
        --top "改前" --bottom "改后"
"""
import argparse
import os
import subprocess
import sys

# 中文字体候选(按优先级)。PIL 默认字体渲染中文会得到方块,必须显式挂字体。
FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]


def encode(frames, out, fps=30, crf=20, pattern="f%04d.png"):
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    inp = os.path.join(frames, pattern)
    cmd = ["ffmpeg", "-y", "-framerate", str(fps), "-i", inp,
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
           "-movflags", "+faststart", out]
    print("[ffmpeg]", " ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-3000:], file=sys.stderr)
        sys.exit(r.returncode)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"[ok] {out}  {size:.2f} MB")


def compare(top_path, bottom_path, out, top_label="before", bottom_label="after",
            width=900, ascii_only=False):
    """上下拼接两张图并加标签。ascii_only 时避免触碰系统字体路径"""
    from PIL import Image, ImageDraw, ImageFont  # noqa: PLC0415

    old = Image.open(top_path).convert("RGB")
    new = Image.open(bottom_path).convert("RGB")
    h = int(old.height * width / old.width)
    old = old.resize((width, h), Image.LANCZOS)
    new = new.resize((width, h), Image.LANCZOS)

    gap = 46
    canvas = Image.new("RGB", (width, h * 2 + gap), (18, 20, 26))
    canvas.paste(old, (0, 0))
    canvas.paste(new, (0, h + gap))

    d = ImageDraw.Draw(canvas)
    fp = None
    if not ascii_only:
        for p in FONT_CANDIDATES:
            try:
                fp = ImageFont.truetype(p, 26)
                break
            except Exception:  # noqa: BLE001
                continue
    if fp is None:
        fp = ImageFont.load_default()
        # 内置字体很可能渲染不了中文,退化成 ASCII 标签
        top_label = "before"
        bottom_label = "after"
    d.text((16, 12), top_label, font=fp, fill=(255, 235, 235))
    d.text((16, h + gap + 12), bottom_label, font=fp, fill=(190, 240, 255))

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    canvas.save(out, quality=90)
    print(f"[ok] {out}  {canvas.size}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", help="帧目录")
    ap.add_argument("--out", required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--pattern", default="f%04d.png")
    ap.add_argument("--compare", nargs=2, metavar=("TOP", "BOTTOM"))
    ap.add_argument("--top", default="改前")
    ap.add_argument("--bottom", default="改后")
    ap.add_argument("--ascii-only", action="store_true",
                    help="不加载系统字体(安全策略拦截 /System 路径时使用)")
    a = ap.parse_args()

    if a.compare:
        compare(a.compare[0], a.compare[1], a.out, a.top, a.bottom,
                ascii_only=a.ascii_only)
    elif a.frames:
        encode(a.frames, a.out, a.fps, a.crf, a.pattern)
    else:
        ap.error("需要 --frames 或 --compare")


if __name__ == "__main__":
    main()
