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


def _blur_wrap(arr, radius):
    """水平方向环绕的高斯模糊。

    PIL 的 GaussianBlur **不处理环绕**,会拿图像外当作无关像素,
    于是贴图左右边缘对不上 —— 贴到天空球上就是一道竖直硬缝
    (实测左右边缘平均差 0.072、最大 0.41)。这里横向平铺三份再裁中间,
    保证 u 方向首尾连续。垂直方向不需要(极点不收拢)。
    """
    h, w = arr.shape
    t = np.tile(arr, (1, 3))
    b = _blur(t, radius)
    return b[:, w:2 * w]


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
    """积云精灵:多个高斯"泡泡"叠加成花椰菜轮廓 + 云底压平 + 宽羽化 + 厚度受光。

    旧版是「径向衰减 × fbm + 6px 模糊」,结果是**近似正圆的扁平团块、边缘硬锯齿**,
    贴到天上就是一眼假的贴纸。积云的辨识特征是"多个圆顶堆在一条平云底上",
    所以这里改成泡泡场,而不是单个径向形状。
    """
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float32)
    vpos = yy / n                                    # 0 顶, 1 底 (PIL 行序)

    # --- 泡泡场:小而多,叠出来才有花椰菜轮廓(大而少会并成一个光滑团) ---
    field = np.zeros((n, n), np.float32)
    for _ in range(30):
        ang = rng.random() * 2 * np.pi
        rr = (rng.random() ** 0.72) * n * 0.27
        cx = n * 0.50 + np.cos(ang) * rr
        cy = n * 0.50 + np.sin(ang) * rr * 0.68 - n * 0.05   # 整体偏上
        rad = n * (0.065 + rng.random() * 0.075)
        # 用 max 而非 sum:sum 会把相邻泡泡平滑成一个光滑外壳,
        # max 保留每个圆顶自己的边界,并集才读得出花椰菜
        field = np.maximum(field, np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (rad * rad))))
    # --- 云底压平:真实积云底部是一条相对平坦的云底 ---
    field *= np.clip((0.70 - vpos) / 0.14, 0, 1)
    field /= max(float(field.max()), 1e-6)

    # --- alpha:轻羽化,保住泡泡的波浪轮廓(blur 过大就退回成光滑土豆) ---
    a = np.clip((field - 0.19) / 0.26, 0, 1)
    a = a * a * (3 - 2 * a)
    a = _blur(a, 9)
    a = np.clip(a * 1.22, 0, 1)

    # --- 受光:厚度(圆顶)占 40%,垂直位置占 60%,把对比拉开 ---
    thick = np.clip(field / 0.70, 0, 1)
    vert = np.power(1.0 - vpos, 0.62)
    lit = np.clip(0.40 * thick + 0.60 * vert, 0, 1)
    tex = fbm_seamless(n, base=26, octaves=4, persistence=0.5, seed=8)
    lit = np.clip(lit * (0.82 + 0.36 * tex), 0, 1)   # 内部翻滚,别太光滑

    litC = np.array([1.00, 0.99, 0.96])
    shadC = np.array([0.40, 0.48, 0.66])
    rgb = shadC[None, None, :] + (litC - shadC)[None, None, :] * lit[:, :, None]

    rgba = np.concatenate([rgb, a[..., None]], -1)
    img = Image.fromarray((np.clip(rgba, 0, 1) * 255).astype(np.uint8), "RGBA")
    p = os.path.join(outdir, "cumulus.png")
    img.save(p)
    edge = float(a[:2].mean() + a[-2:].mean() + a[:, :2].mean() + a[:, -2:].mean())
    print(f"[cumulus] {p}  均值 {a.mean():.2f}  峰值 {a.max():.2f}  边缘alpha {edge / 4:.4f}(应≈0)")
    return p


def build_detail(n=2048, outdir="web"):
    """雪面程序化细节贴图。

    卫星影像在雪区几乎没有纹理(实测 5x5 局部 std 只有 0.039,是岩石区的 1/3),
    所以直接提亮雪会变成一片死白 —— 这正是"水泥感"的来源。这里补一层多尺度高频
    (中性 0.5±0.09),在着色器里只作用于雪区,提亮时才有层次可看。

    频率必须够高且三档权重接近:低频那档权重过大时,雪面会被拉出方向性条纹,
    读成"水波/绸缎"而不是雪。
    """
    a = fbm_seamless(n, base=34, octaves=4, persistence=0.55, seed=101)
    b = fbm_seamless(n, base=95, octaves=4, persistence=0.50, seed=202)
    c = fbm_seamless(n, base=260, octaves=3, persistence=0.46, seed=303)
    d = a * 0.34 + b * 0.36 + c * 0.30
    d = _blur(d, 2)                     # 抹掉值噪声格点(base=15 时每 136px 一个,放大就是方块)
    d = (d - d.mean()) / max(float(d.std()), 1e-6) * 0.09 + 0.5
    d = np.clip(d, 0, 1)
    p = os.path.join(outdir, "detail.png")
    Image.fromarray((d * 255).astype(np.uint8)).save(p)
    print(f"[detail] {p}  中性 0.50±0.09")
    return p


def build_sky(w=2048, h=1024, outdir="web"):
    """等距天空穹顶贴图:竖直渐变 + 高空柔云。太阳辉光由场景里的加性精灵承担"""
    yy = np.linspace(0, 1, h)[:, None]              # 0 顶, 1 底
    deep = np.array([0.18, 0.38, 0.72])             # 天顶深蓝
    mid = np.array([0.40, 0.60, 0.85])              # 近地平线适度加深,别糊成灰白
    hor = np.array([0.62, 0.70, 0.81])              # 地平线带:别太苍白,相机压低时整片天空都是这一带
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
    cfb = _blur_wrap(cfb, 16)                        # 环绕模糊,否则天空球上出现竖直接缝
    cimg = Image.fromarray((cfb * 255).astype(np.uint8))
    cimg = cimg.resize((w, h), Image.LANCZOS)
    cf = np.asarray(cimg).astype(np.float32) / 255.0
    cov = np.clip((cf - 0.435) / 0.30, 0, 1)
    cov = cov * cov * (3 - 2 * cov)
    cov = _blur_wrap(cov, 10)
    # 刻意不做 band 限制:等距贴图上「可见天空」只占很窄一条,任何 band 都可能误伤它
    cov = np.clip(cov * 1.15, 0, 1)
    # 但覆盖率不能过高:超过 ~55% 时可见带里的云会连成一条白带,读成"过曝的天空"。
    # 也不加太大权重 —— 权重 0.20 会把云堆在可见带正中,同样连成白带。
    # 0.40 倍 + 0.22 的窄高斯权重:可见带有两三朵,其余稀疏。
    # 关键:可见带 v∈[0.40,0.55](用彩色调试贴图实测出来的),权重必须够强 ——
    # cov 只加到 0.22 时混合出的饱和度是 0.25,既看不出来也过不了判据,等于没有云。
    cov = np.clip(cov * 0.35, 0, 1)
    vis = np.exp(-((yy - 0.47) / 0.13) ** 2)
    cov = np.clip(cov + 0.44 * vis, 0, 1)
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
    build_detail(outdir=a.outdir)


if __name__ == "__main__":
    main()
