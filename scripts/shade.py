#!/usr/bin/env python3
"""山体投射阴影(cast shadow)预计算。

在着色器里实时算投射阴影几乎不可能(需要全场景 ray march),
但沿太阳方向对高程做**投影步进**可以离线预烘成一张贴图——
山体层次约 80% 来自「山挡山」,这一步是性价比最高的真实感来源。

用法:
    python3 shade.py --elev elev.npy --km 22 --az 318 --alt 33 --out shadow.png
"""
import argparse

import numpy as np


def sun_shadow(g, cell, az_deg=318.0, alt_deg=24.0, soft=70.0, steps=None):
    """
    g       : 高程网格 (正北朝上,行号向南递增)
    cell    : 每像素米数
    az_deg  : 太阳方位角(正北起,顺时针)
    alt_deg : 太阳高度角
    soft    : 半影过渡的米数,越大越柔
    返回 0-1 受照系数(1=全照, 0=全影)
    """
    az = np.radians(az_deg)
    alt = np.radians(alt_deg)
    dx, dn = np.sin(az), np.cos(az)          # 指向太阳的水平方向(东, 北)
    dcol, drow = dx, -dn                      # 行号向南递增,故北为负
    rise_per_px = cell * np.tan(alt)

    h, w = g.shape
    span = int(np.ceil(np.hypot(h, w)))
    if steps is None:
        steps = min(240, max(60, int((g.max() - g.min()) / max(rise_per_px, 1e-6))))

    pad = steps + 2
    gp = np.pad(g, pad, mode="edge")
    blocked = np.zeros((h, w), np.float32)

    for k in range(1, steps + 1):
        oc, orr = int(round(k * dcol)), int(round(k * drow))
        shifted = gp[pad + orr:pad + orr + h, pad + oc:pad + oc + w]
        limit = g + k * rise_per_px
        np.maximum(blocked, shifted - limit, out=blocked)

    # blocked>0 表示被挡住;用 soft 米做半影过渡
    return np.clip(1.0 - blocked / soft, 0.0, 1.0)


def ambient_occlusion(g, cell, radius_px=18):
    """简化曲率/遮挡:低于邻域均值 = 谷底,压暗"""
    from dem import box_blur  # noqa: PLC0415
    ref = box_blur(g, radius_px, passes=2)
    d = g - ref
    lo, hi = np.percentile(d, 2), np.percentile(d, 98)
    t = np.clip((d - lo) / max(hi - lo, 1e-6), 0, 1)
    return 0.55 + 0.45 * t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--elev", required=True, help=".npy 高程(m)")
    ap.add_argument("--km", type=float, required=True, help="区域宽度(公里),用于算每像素米数")
    ap.add_argument("--az", type=float, default=318.0)
    ap.add_argument("--alt", type=float, default=33.0)
    ap.add_argument("--soft", type=float, default=60.0)
    ap.add_argument("--out", default="shadow.png")
    a = ap.parse_args()

    g = np.load(a.elev).astype(np.float32)
    cell = a.km * 1000.0 / g.shape[0]
    sh = sun_shadow(g, cell, az_deg=a.az, alt_deg=a.alt, soft=a.soft)
    from PIL import Image  # noqa: PLC0415
    Image.fromarray((sh * 255).astype(np.uint8)).save(a.out)
    print(f"[ok] {a.out}  平均受照 {sh.mean():.2f}  全影占比 {(sh < 0.15).mean()*100:.1f}%")


if __name__ == "__main__":
    main()
