#!/usr/bin/env python3
"""卫星影像抓取:ESRI World Imagery(免 key)。

必须与 DEM 使用**同一个 bbox 与同一个 zoom** 裁剪,才能保证像素级对齐。
build_world.py 里影像会比高程高一档 zoom(拿真实细节),裁剪逻辑完全一致。

用法:
    python3 imagery.py --lat 28.4373 --lon 98.6817 --km 22 --z 12 --out sat.png
"""
import argparse
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

ESRI = ("https://server.arcgisonline.com/ArcGIS/rest/services/"
        "World_Imagery/MapServer/tile/{z}/{y}/{x}")
# Sentinel-2 年度无云合成(EOX)。全球**统一合成**,不存在"相邻瓦片来自不同时间"问题,
# 因而不产生 ESRI 那种方形色块。代价是雪原上几乎是纯白、细节靠法线+程序化补。
S2CL = ("https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2023_3857/"
        "default/g/{z}/{y}/{x}.jpg")
SOURCES = {"esri": ESRI, "s2": S2CL}
DEFAULT_CACHE = "cache_imagery"


def _fetch(z, x, y, cache=DEFAULT_CACHE, tries=4, url=ESRI):
    p = os.path.join(cache, str(z), f"{x}_{y}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url.format(z=z, x=x, y=y),
                                         headers={"User-Agent": "terrain-flythrough/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            return p
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.7 * (i + 1))
    raise RuntimeError(f"imagery {z}/{x}/{y} after {tries} tries: {last}")


def _balance_tiles(mos, rows, cols, ts=256, sigma=1.3):
    """逐瓦片色调校正 —— 目标是**邻域平滑均值**,不是全局均值。

    ESRI 相邻瓦片常来自不同时间/不同卫星,同一区域叠出来是调子不同的方块。

    关键教训:把每块都拉向**全局**均值是错的 —— 块内"雪/岩比例"各不相同,
    统一均值后边界处的纹理反而断层更明显(实测校正后方块比校正前更清楚)。
    正确做法是在块网格上对均值做高斯平滑,让每块向**邻域**靠拢:
    相邻块的跳变被抹平,而雪原/岩体的大尺度趋势得以保留。
    """
    if rows < 2 or cols < 2:
        return mos
    m = mos.astype(np.float32)

    means = np.zeros((rows, cols, 3), np.float32)
    for j in range(rows):
        for i in range(cols):
            means[j, i] = mos[j * ts:(j + 1) * ts, i * ts:(i + 1) * ts].reshape(-1, 3).mean(0)

    # 在"块网格"上平滑:得到每块邻域应有的调子
    from PIL import ImageFilter  # noqa: PLC0415
    target = np.zeros_like(means)
    for c in range(3):
        chan = Image.fromarray(np.clip(means[..., c], 0, 255).astype(np.uint8))
        target[..., c] = np.asarray(chan.filter(ImageFilter.GaussianBlur(sigma)))

    changed = 0
    for j in range(rows):
        for i in range(cols):
            mu = means[j, i]
            tg = target[j, i]
            gain = np.clip(tg / np.maximum(mu, 1.0), 0.80, 1.25)
            if np.abs(gain - 1.0).max() < 0.004:
                continue
            changed += 1
            blk = m[j * ts:(j + 1) * ts, i * ts:(i + 1) * ts]
            m[j * ts:(j + 1) * ts, i * ts:(i + 1) * ts] = blk * gain
    print(f"[imagery] 色调校正 {changed}/{rows * cols} 块(邻域平滑,sigma={sigma})")
    return np.clip(m, 0, 255).astype(np.uint8)


def _soften_seams(mos, rows, cols, ts=256, half=3):
    """接缝窄带柔化。

    调色只能抹平**均值差**;不同瓦片的纹理内容(一处是纯雪、一处露岩)依然在,
    接缝处会留下一条纹理断层。这里只沿 256 的整数倍线做 3px 带的高斯柔化,
    不动其余像素 —— 代价是接缝处损失极少量细节。
    """
    H, W, _ = mos.shape
    mask = np.zeros((H, W), np.float32)
    for i in range(1, cols):
        x = i * ts
        if 0 < x < W:
            mask[:, max(0, x - half):min(W, x + half)] = 1.0
    for j in range(1, rows):
        y = j * ts
        if 0 < y < H:
            mask[max(0, y - half):min(H, y + half), :] = 1.0
    if mask.max() < 0.5:
        return mos

    from PIL import ImageFilter  # noqa: PLC0415
    mk = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))
    mask = np.asarray(mk).astype(np.float32) / 255.0
    blurred = np.asarray(Image.fromarray(mos).filter(ImageFilter.GaussianBlur(1.2)))
    out = mos.astype(np.float32) * (1 - mask[..., None]) + blurred.astype(np.float32) * mask[..., None]
    return np.clip(out, 0, 255).astype(np.uint8)


def build_imagery(bbox, z, cache=DEFAULT_CACHE, workers=8, lonlat_to_px=None, source="esri"):
    """裁剪逻辑与 dem.build_elevation 完全一致。lonlat_to_px 由调用方注入以避免循环导入"""
    if lonlat_to_px is None:
        from dem import lonlat_to_px  # noqa: PLC0415
    url = SOURCES.get(source, ESRI)
    cache = os.path.join(cache, source)          # 不同源分开缓存,避免混用
    os.makedirs(cache, exist_ok=True)
    west, north, east, south = bbox
    x0f, y0f = lonlat_to_px(west, north, z)
    x1f, y1f = lonlat_to_px(east, south, z)
    tx0, ty0 = int(x0f // 256), int(y0f // 256)
    tx1, ty1 = int((x1f - 1) // 256), int((y1f - 1) // 256)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    print(f"[imagery] {source} z={z}  tiles {cols}x{rows}={cols*rows}")
    coords = [(z, tx0 + i, ty0 + j) for j in range(rows) for i in range(cols)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        paths = list(ex.map(lambda c: _fetch(*c, cache=cache, url=url), coords))

    mos = np.zeros((rows * 256, cols * 256, 3), np.uint8)
    for (tz, tx, ty), p in zip(coords, paths):
        im = np.asarray(Image.open(p).convert("RGB"))
        if im.shape[0] != 256:
            im = np.asarray(Image.fromarray(im).resize((256, 256), Image.LANCZOS))
        oy, ox = (ty - ty0) * 256, (tx - tx0) * 256
        mos[oy:oy + 256, ox:ox + 256] = im

    # 色调校正与接缝柔化只对 ESRI 有意义:S2 是统一合成,本来就没有块间色差
    if source == "esri":
        mos = _balance_tiles(mos, rows, cols)
        mos = _soften_seams(mos, rows, cols)

    cx, cy = x0f - tx0 * 256, y0f - ty0 * 256
    cw, ch = x1f - x0f, y1f - y0f
    return mos[int(round(cy)):int(round(cy + ch)), int(round(cx)):int(round(cx + cw))]


def main():
    ap = argparse.ArgumentParser(description="抓取 ESRI 卫星影像")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--km", type=float, default=20.0)
    ap.add_argument("--z", type=int, default=12)
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default="satellite.png")
    a = ap.parse_args()

    from dem import bbox_from_center, lonlat_to_px  # noqa: PLC0415
    bbox = bbox_from_center(a.lat, a.lon, a.km, 1.0)
    sat = build_imagery(bbox, a.z, cache=a.cache, lonlat_to_px=lonlat_to_px)
    Image.fromarray(sat).save(a.out)
    print(f"[ok] {a.out}  {sat.shape}")


if __name__ == "__main__":
    main()
