#!/usr/bin/env python3
"""渲染帧数值判色工具。

**这是本工作流最关键的判据来源。** 不要靠肉眼/视觉模型判断"是不是发灰、是不是发青",
直接量通道均值、饱和度、亮度,以及按画面分带的分布——见 references/pitfalls.md。

健康区间(1280x720 山地飞行帧):
    天空区(顶部 35%)  饱和度 0.30~0.45, 亮度 0.50~0.65, 蓝>红
    地形区(底部 35%)  亮部 RGB 三通道应接近中性(差值 < 0.05)
    天空云像素占比     10%~30%

用法:
    python3 measure.py frames/f0001.png
    python3 measure.py frames/f0001.png --grid      # 额外打印粗网格
    python3 measure.py frames/*.png --brief         # 批量只看是否越界
"""
import argparse
import glob
import sys

import numpy as np
from PIL import Image


def load(p):
    return np.asarray(Image.open(p).convert("RGB")).astype(np.float32) / 255.0


def stats(a, y0, y1):
    r = a[int(a.shape[0] * y0):int(a.shape[0] * y1)]
    R, G, B = r[..., 0].mean(), r[..., 1].mean(), r[..., 2].mean()
    lum = 0.299 * R + 0.587 * G + 0.114 * B
    sat = max(R, G, B) - min(R, G, B)
    lumM = 0.299 * r[..., 0] + 0.587 * r[..., 1] + 0.114 * r[..., 2]
    satM = r.max(2) - r.min(2)
    cloud = float(((lumM > 0.70) & (satM < 0.18)).mean() * 100)
    return dict(R=R, G=G, B=B, lum=lum, sat=sat, cloud=cloud)


def grid(a, gy=8, gx=4):
    H, W, _ = a.shape
    print("\n粗网格 mean RGB (行=画面上->下):")
    for i in range(gy):
        row = []
        for j in range(gx):
            b = a[int(H * i / gy):int(H * (i + 1) / gy),
                  int(W * j / gx):int(W * (j + 1) / gx)]
            row.append(f"{b[...,0].mean():.2f},{b[...,1].mean():.2f},{b[...,2].mean():.2f}")
        print(" | ".join(row))


def report(path, want_grid=False, brief=False):
    a = load(path)
    sky = stats(a, 0.00, 0.35)
    terr = stats(a, 0.65, 1.00)

    warn = []
    if sky["sat"] < 0.25:
        warn.append(f"天空发灰(饱和度 {sky['sat']:.3f} < 0.25) —— 检查是否有巨型云挡镜头")
    if sky["lum"] > 0.75:
        warn.append(f"天空过曝(亮度 {sky['lum']:.3f} > 0.75) —— 检查加性辉光是否过大")
    if sky["cloud"] < 3:
        warn.append(f"天空几乎无云({sky['cloud']:.1f}%) —— 检查 sky.png 覆盖率或 band 限制")

    if brief:
        flag = "OK " if not warn else "WARN"
        print(f"[{flag}] {path:28s} sky sat{sky['sat']:.3f} lum{sky['lum']:.3f} "
              f"cloud{sky['cloud']:5.1f}%  | terr R{terr['R']:.3f} G{terr['G']:.3f} B{terr['B']:.3f}")
    else:
        print(f"\n=== {path} ===")
        print(f"天空(上35%)  R{sky['R']:.3f} G{sky['G']:.3f} B{sky['B']:.3f}  "
              f"lum{sky['lum']:.3f}  sat{sky['sat']:.3f}  云像素{sky['cloud']:.1f}%")
        print(f"地形(下35%)  R{terr['R']:.3f} G{terr['G']:.3f} B{terr['B']:.3f}  "
              f"lum{terr['lum']:.3f}  sat{terr['sat']:.3f}")
        if want_grid:
            grid(a)
    for w in warn:
        print(f"  !! {w}")
    return not warn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--brief", action="store_true")
    a = ap.parse_args()

    files = []
    for f in a.files:
        files.extend(sorted(glob.glob(f)) or [f])
    ok = all(report(f, a.grid, a.brief) for f in files)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
