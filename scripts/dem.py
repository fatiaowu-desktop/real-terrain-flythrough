#!/usr/bin/env python3
"""高程数据抓取:Terrarium RGB DEM(AWS S3 公开瓦片,免 key、免注册)。

用法:
    python3 dem.py --lat 28.4373 --lon 98.6817 --km 22 --out elev.npy
    python3 dem.py --lat 45.9763 --lon 7.6586 --km 15 --cache ./cache_tiles --out /tmp/mh.npy

也可作为库使用:
    from dem import bbox_from_center, build_elevation
"""
import argparse
import math
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
DEFAULT_CACHE = "cache_tiles"


# ------------------------------------------------------------------ 地图数学
def lonlat_to_px(lon, lat, z):
    n = 2.0 ** z
    x = (lon + 180.0) / 360.0 * n * 256.0
    lat_r = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n * 256.0
    return x, y


def bbox_from_center(lat, lon, width_km, aspect_h_over_w=1.0):
    """按中心点 + 宽度(km)求 Web Mercator 包围盒 (west, north, east, south)"""
    km_lat = 111.32
    km_lon = 111.32 * math.cos(math.radians(lat))
    h_km = width_km * aspect_h_over_w
    dlat, dlon = h_km / km_lat, width_km / km_lon
    return (lon - dlon / 2, lat + dlat / 2, lon + dlon / 2, lat - dlat / 2)


def pick_zoom(bbox, px_max=3600):
    """选最大的、仍不超过 px_max 像素的 zoom。数字越大越细,但瓦片数呈平方增长"""
    west, north, east, south = bbox
    for z in range(16, 5, -1):
        x0, y0 = lonlat_to_px(west, north, z)
        x1, y1 = lonlat_to_px(east, south, z)
        if max(x1 - x0, y1 - y0) <= px_max:
            return z
    return 6


# ------------------------------------------------------------------ 取瓦片
def fetch_tile(z, x, y, cache=DEFAULT_CACHE, tries=4):
    """带磁盘缓存 + 指数退避重试。TLS 握手偶发超时是常态,不重试必挂"""
    p = os.path.join(cache, str(z), f"{x}_{y}.png")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(
                TILE_URL.format(z=z, x=x, y=y),
                headers={"User-Agent": "terrain-flythrough/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read()
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(data)
            return p
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(0.8 * (i + 1))
    raise RuntimeError(f"tile {z}/{x}/{y} failed after {tries} tries: {last}")


def decode_terrarium(path):
    """Terrarium 解码: h = R*256 + G + B/256 - 32768 (单位米)"""
    a = np.asarray(Image.open(path).convert("RGB")).astype(np.float32)
    return a[:, :, 0] * 256.0 + a[:, :, 1] + a[:, :, 2] / 256.0 - 32768.0


def box_blur(img, r, passes=3):
    """numpy 可分离 box blur。注意:大半径会非常慢,见 pitfalls.md"""
    if r < 1:
        return img.astype(np.float32)
    out = img.astype(np.float32)
    for _ in range(passes):
        for axis in (0, 1):
            pad = [(0, 0), (0, 0)]
            pad[axis] = (r + 1, r)
            p = np.pad(out, pad, mode="edge")
            c = np.cumsum(p, axis=axis, dtype=np.float32)
            out = (c[2 * r + 1:, :] - c[:-2 * r - 1, :]) / (2 * r + 1) if axis == 0 \
                else (c[:, 2 * r + 1:] - c[:, :-2 * r - 1]) / (2 * r + 1)
    return out


def fill_voids(e, lo=-400.0, hi=9000.0):
    """缺失瓦片会返回异常值,不补会渲染成黑缝"""
    bad = (e < lo) | (e > hi) | ~np.isfinite(e)
    n = int(bad.sum())
    if n == 0:
        return e
    print(f"[dem] 修补无效值 {n} 个 ({n / e.size * 100:.2f}%)")
    good = (~bad).astype(np.float32)
    for r in (2, 6, 16, 40):
        if not bad.any():
            break
        num = box_blur(np.where(bad, 0.0, e), r, passes=2)
        den = box_blur(good, r, passes=2)
        est = num / np.maximum(den, 1e-6)
        ok = bad & (den > 0.02)
        e = np.where(ok, est, e)
        bad = bad & ~ok
    return e


def build_elevation(bbox, cache=DEFAULT_CACHE, workers=8):
    """拼瓦片并按 bbox 精确裁剪。返回 (elev_m, 每像素米数, zoom)"""
    west, north, east, south = bbox
    z = pick_zoom(bbox)
    x0f, y0f = lonlat_to_px(west, north, z)
    x1f, y1f = lonlat_to_px(east, south, z)
    tx0, ty0 = int(x0f // 256), int(y0f // 256)
    tx1, ty1 = int((x1f - 1) // 256), int((y1f - 1) // 256)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    res_m = 156543.03392 * math.cos(math.radians((north + south) / 2)) / (2 ** z)
    print(f"[dem] z={z}  tiles {cols}x{rows}={cols*rows}  res~{res_m:.1f} m/px")

    coords = [(z, tx0 + i, ty0 + j) for j in range(rows) for i in range(cols)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        paths = list(ex.map(lambda c: fetch_tile(*c, cache=cache), coords))

    mosaic = np.zeros((rows * 256, cols * 256), dtype=np.float32)
    for (tz, tx, ty), p in zip(coords, paths):
        oy, ox = (ty - ty0) * 256, (tx - tx0) * 256
        mosaic[oy:oy + 256, ox:ox + 256] = decode_terrarium(p)

    cx, cy = x0f - tx0 * 256, y0f - ty0 * 256
    crop = mosaic[int(round(cy)):int(round(cy + y1f - y0f)),
                  int(round(cx)):int(round(cx + x1f - x0f))]
    crop = fill_voids(crop)
    return crop, res_m, z


def main():
    ap = argparse.ArgumentParser(description="抓取 Terrarium DEM")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--km", type=float, default=20.0, help="区域宽度(公里)")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--out", default="elev.npy")
    a = ap.parse_args()

    bbox = bbox_from_center(a.lat, a.lon, a.km, 1.0)
    elev, res, z = build_elevation(bbox, cache=a.cache)
    np.save(a.out, elev)
    print(f"[ok] {a.out}  {elev.shape}  {res:.1f}m/px  z={z}  "
          f"高程 {elev.min():.0f}-{elev.max():.0f}m")


if __name__ == "__main__":
    main()
