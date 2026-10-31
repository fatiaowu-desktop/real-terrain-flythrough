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
DEFAULT_CACHE = "cache_imagery"


def _fetch(z, x, y, cache=DEFAULT_CACHE, tries=4):
    p = os.path.join(cache, str(z), f"{x}_{y}.jpg")
    if os.path.exists(p) and os.path.getsize(p) > 0:
        return p
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(ESRI.format(z=z, x=x, y=y),
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


def build_imagery(bbox, z, cache=DEFAULT_CACHE, workers=8, lonlat_to_px=None):
    """裁剪逻辑与 dem.build_elevation 完全一致。lonlat_to_px 由调用方注入以避免循环导入"""
    if lonlat_to_px is None:
        from dem import lonlat_to_px  # noqa: PLC0415
    west, north, east, south = bbox
    x0f, y0f = lonlat_to_px(west, north, z)
    x1f, y1f = lonlat_to_px(east, south, z)
    tx0, ty0 = int(x0f // 256), int(y0f // 256)
    tx1, ty1 = int((x1f - 1) // 256), int((y1f - 1) // 256)
    cols, rows = tx1 - tx0 + 1, ty1 - ty0 + 1
    print(f"[imagery] z={z}  tiles {cols}x{rows}={cols*rows}")
    coords = [(z, tx0 + i, ty0 + j) for j in range(rows) for i in range(cols)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        paths = list(ex.map(lambda c: _fetch(*c, cache=cache), coords))

    mos = np.zeros((rows * 256, cols * 256, 3), np.uint8)
    for (tz, tx, ty), p in zip(coords, paths):
        im = np.asarray(Image.open(p).convert("RGB"))
        if im.shape[0] != 256:
            im = np.asarray(Image.fromarray(im).resize((256, 256), Image.LANCZOS))
        oy, ox = (ty - ty0) * 256, (tx - tx0) * 256
        mos[oy:oy + 256, ox:ox + 256] = im

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
