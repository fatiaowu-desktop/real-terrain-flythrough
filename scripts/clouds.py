#!/usr/bin/env python3
"""生成云层相关贴图。

- build()        : 无缝 fbm 云密度(供低位云海面 + 山体云影共用)
- build_cumulus(): 朝向相机的公告板积云,带「受光顶 / 背阴底」体积渐变
- build_sky()    : 等距天空穹顶贴图(竖直渐变 + 高空柔云)

所有柔化都用 PIL 的 C 实现 GaussianBlur(快且不会卡),并抬高基础频率,
从根上消除值噪声最低频八度的方块格子。

用法:
    python3 clouds.py --outdir web      # 生成 cloud.png / cumulus.png / sky.png
"""
import argparse
import os

import numpy as np
from PIL import Image, ImageFilter


def noise_seamless(n, grid, rng):
    """在 torus 上取值的值噪声,保证四边无缝"""
    g = rng.random((grid, grid)).astype(np.float32)
    t = np.arange(n) / n * grid
    i0 = np.floor(t).astype(int) % grid
    f = t - np.floor(t)
    i1 = (i0 + 1) % grid
    s = (f * f * (3 - 2 * f)).astype(np.float32)

    g00 = g[np.ix_(i0, i0)]
    g10 = g[np.ix_(i1, i0)]
    g01 = g[np.ix_(i0, i1)]
    g11 = g[np.ix_(i1, i1)]
    sx, sy = s[None, :], s[:, None]
    top = g00 * (1 - sx) + g10 * sx
    bot = g01 * (1 - sx) + g11 * sx
    return top * (1 - sy) + bot * sy


def fbm_seamless(n, base=8, octaves=6, persistence=0.55, seed=5):
    """base 是最低频八度的格子数。**太小会产生大方块**,见 pitfalls.md"""
    rng = np.random.default_rng(seed)
    total = np.zeros((n, n), np.float32)
    amp, norm = 1.0, 0.0
    for o in range(octaves):
        total += amp * noise_seamless(n, base * (2 ** o), rng)
        norm += amp
        amp *= persistence
    return total / norm


def _blur(arr, radius):
    """高斯模糊(numpy 0..1 数组 -> 同形数组)。必须用 PIL 的 C 实现,别用 numpy box_blur"""
    im = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    im = im.filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(im).astype(np.float32) / 255.0


def build(n=2048, outdir="web"):
    """无缝云密度贴图(周期 fbm),供云海面与山体云影共用"""
    base = fbm_seamless(n, base=6, octaves=7, persistence=0.62, seed=11)
    detail = fbm_seamless(n, base=16, octaves=6, persistence=0.55, seed=29)
    cov = fbm_seamless(n, base=3, octaves=3, persistence=0.65, seed=41)

    v = base * 0.66 + detail * 0.34
    v = v * 0.80 + cov * 0.20

    # 域扭曲:打断值噪声的格点方块感
    dx = (fbm_seamless(n, base=4, octaves=3, persistence=0.6, seed=77) - 0.5) * n * 0.14
    dy = (fbm_seamless(n, base=4, octaves=3, persistence=0.6, seed=91) - 0.5) * n * 0.14
    yy = (np.arange(n)[:, None] + dy).astype(np.int32) % n
    xx = (np.arange(n)[None, :] + dx).astype(np.int32) % n
    v = v[yy, xx]
    v = _blur(v, 5)

    lo, hi = np.percentile(v, 30), np.percentile(v, 90)
    a = np.clip((v - lo) / max(hi - lo, 1e-6), 0, 1)
    a = a * a * (3 - 2 * a)
    a = np.clip(a * 1.18 - 0.06, 0, 1)
    a = a ** 0.72
    a = np.clip((a - 0.28) / 0.72, 0, 1)
    a = a * a * (3 - 2 * a)

    lum = 0.74 + 0.26 * a
    rgb = np.stack([lum * 1.00, lum * 0.995, lum * 0.985], -1)
    rgba = np.concatenate([rgb, a[..., None]], -1)
    img = Image.fromarray((np.clip(rgba, 0, 1) * 255).astype(np.uint8), "RGBA")
    p = os.path.join(outdir, "cloud.png")
    img.save(p)
    print(f"[cloud] {p}  覆盖率 {(a > 0.25).mean()*100:.1f}%  峰值 {a.max():.2f}")
    return p


def build_cumulus(n=1024, outdir="web"):
    """朝向相机的体积感积云:多瓣 cauliflower 轮廓 + 强对比暖白顶 / 深冷蓝底"""
    warp_x = (fbm_seamless(n, base=8, octaves=3, persistence=0.6, seed=77) - 0.5) * n * 0.10
    warp_y = (fbm_seamless(n, base=8, octaves=3, persistence=0.6, seed=91) - 0.5) * n * 0.10
    yyw = (np.arange(n)[:, None] + warp_y).astype(int) % n
    xxw = (np.arange(n)[None, :] + warp_x).astype(int) % n

    base = fbm_seamless(n, base=9, octaves=6, persistence=0.55, seed=5)[yyw, xxw]
    detail = fbm_seamless(n, base=24, octaves=5, persistence=0.5, seed=8)[yyw, xxw]

    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    cy = cx = n / 2
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / (n / 2)
    radial = np.clip(1.0 - d, 0, 1)

    vpos = yy / n                                   # 0 顶, 1 底 (PIL 行序)
    vbias = 1.0 - vpos
    lump = base * 0.46 + detail * 0.36              # 低频定大形 + 高频切小叶
    shape = radial * 0.52 + lump * 0.88 - 0.02
    shape *= (0.80 + 0.40 * vbias)
    a = np.clip((shape - 0.28) / 0.46, 0, 1)
    a = a * a * (3 - 2 * a)
    a = _blur(a, 6)
    a = np.clip(a * (radial ** 0.22), 0, 1)
    a = a * a * (3 - 2 * a)
    a = np.clip(a * 1.25 - 0.05, 0, 1)

    # 强对比体积光照:顶部暖白受光,底部深冷蓝背阴
    litC = np.array([1.00, 0.98, 0.92])
    shadC = np.array([0.30, 0.38, 0.60])
    sh = np.clip(vbias * 1.10 - 0.08, 0, 1)[:, :, None]
    rgb = shadC[None, None, :] + (litC - shadC)[None, None, :] * sh
    rgb = rgb * (0.90 + 0.10 * a[:, :, None])

    rgba = np.concatenate([rgb, a[..., None]], -1)
    img = Image.fromarray((np.clip(rgba, 0, 1) * 255).astype(np.uint8), "RGBA")
    p = os.path.join(outdir, "cumulus.png")
    img.save(p)
    print(f"[cumulus] {p}  均值 {a.mean():.2f}  峰值 {a.max():.2f}")
    return p


def build_sky(w=2048, h=1024, outdir="web"):
    """等距天空穹顶贴图:竖直渐变 + 高空柔云。太阳辉光由场景里的加性精灵承担"""
    yy = np.linspace(0, 1, h)[:, None]              # 0 顶, 1 底
    deep = np.array([0.18, 0.38, 0.72])             # 天顶深蓝
    mid = np.array([0.40, 0.60, 0.85])              # 近地平线适度加深,别糊成灰白
    hor = np.array([0.75, 0.80, 0.86])              # 地平线苍白带
    t1 = np.clip(yy / 0.55, 0, 1)[:, :, None]
    t2 = np.clip((yy - 0.55) / 0.45, 0, 1)[:, :, None]
    lo = deep[None, None, :] * (1 - t1) + mid[None, None, :] * t1
    hi = mid[None, None, :] * (1 - t2) + hor[None, None, :] * t2
    col = np.where(yy[:, :, None] < 0.55, lo, hi)
    col = np.repeat(col, w, axis=1).astype(np.float32)

    # 高空柔云:抬高基础频率 + 大半径高斯模糊,彻底去掉方块团
    cn = 1024
    wx = (fbm_seamless(cn, base=6, octaves=3, persistence=0.6, seed=63) - 0.5) * cn * 0.10
    wy = (fbm_seamless(cn, base=6, octaves=3, persistence=0.6, seed=71) - 0.5) * cn * 0.10
    yw = (np.arange(cn)[:, None] + wy).astype(int) % cn
    xw = (np.arange(cn)[None, :] + wx).astype(int) % cn
    cfb = fbm_seamless(cn, base=11, octaves=6, persistence=0.58, seed=13)[yw, xw]
    cfb = (cfb - cfb.min()) / (cfb.max() - cfb.min())
    cimg = Image.fromarray((cfb * 255).astype(np.uint8))
    cimg = cimg.filter(ImageFilter.GaussianBlur(16))
    cimg = cimg.resize((w, h), Image.LANCZOS)
    cf = np.asarray(cimg).astype(np.float32) / 255.0
    cov = np.clip((cf - 0.46) / 0.26, 0, 1)
    cov = cov * cov * (3 - 2 * cov)
    cov = _blur(cov, 10)
    # 刻意不做 band 限制:等距贴图上「可见天空」只占很窄一条,任何 band 都可能误伤它
    cov = np.clip(cov * 1.15, 0, 1)
    cloud = np.stack([np.full((h, w), 0.95), np.full((h, w), 0.96), np.full((h, w), 0.98)], -1)
    col = col * (1 - cov)[..., None] + cloud * cov[..., None]

    img = Image.fromarray((np.clip(col, 0, 1) * 255).astype(np.uint8), "RGB")
    p = os.path.join(outdir, "sky.png")
    img.save(p)
    print(f"[sky] {p}  云覆盖 {(cov > 0.2).mean()*100:.1f}%")
    return p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="web")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)
    build(outdir=a.outdir)
    build_cumulus(outdir=a.outdir)
    build_sky(outdir=a.outdir)


if __name__ == "__main__":
    main()
