#!/usr/bin/env python3
"""编排:经纬度 -> WebGL 可直接消费的一整套地形资源。

产出(写到 --outdir,默认 web/):
    heights.png    高程(16bit 打包进 R,G 两通道)
    shadow.png     山体投射阴影(0-1)
    normal.png     逐像素法线贴图(2048)
    satellite.jpg  ESRI 卫星影像
    world.json     元数据,由 fly.html 读取驱动场景

用法:
    python3 build_world.py --name 卡瓦格博 --sub 梅里雪山 --lat 28.4373 --lon 98.6817 \
        --km 22 --grid 640 --sun-az 318 --sun-alt 33 --outdir web
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clouds as CL  # noqa: E402
import dem  # noqa: E402
import imagery  # noqa: E402
import shade as SH  # noqa: E402


def build(name="未命名", sub="", latin="", lat=0.0, lon=0.0, km=20.0,
          grid=640, tex=4096, sun_az=318.0, sun_alt=33.0,
          outdir="web", cache_tiles="cache_tiles", cache_imagery="cache_imagery",
          gen_clouds=True):
    os.makedirs(outdir, exist_ok=True)
    bbox = dem.bbox_from_center(lat, lon, km, 1.0)
    elev, res, z = dem.build_elevation(bbox, cache=cache_tiles)
    sat = imagery.build_imagery(bbox, z + 1, cache=cache_imagery,
                                lonlat_to_px=dem.lonlat_to_px)
    print(f"[world] DEM {elev.shape}  {res:.1f}m/px  影像 {sat.shape}")

    # --- 高程按网格重采样,打包成 16bit ---
    g = np.asarray(Image.fromarray(elev, mode="F").resize((grid, grid), Image.LANCZOS),
                   np.float32)
    g = dem.box_blur(g, 1, passes=1)          # 边缘轻微压平,避免飞行时看到悬空的边
    lo, hi = float(g.min()), float(g.max())
    v = np.clip((g - lo) / max(hi - lo, 1e-6) * 65535.0, 0, 65535).astype(np.uint32)
    packed = np.zeros((grid, grid, 3), np.uint8)
    packed[:, :, 0] = (v >> 8) & 255
    packed[:, :, 1] = v & 255
    Image.fromarray(packed).save(os.path.join(outdir, "heights.png"))

    # --- 投射阴影:在高一档分辨率上算,再降到网格尺寸 ---
    gs = np.asarray(Image.fromarray(elev, mode="F").resize((1024, 1024), Image.LANCZOS),
                    np.float32)
    sh = SH.sun_shadow(gs, km * 1000.0 / 1024.0, az_deg=sun_az, alt_deg=sun_alt, soft=60.0)
    Image.fromarray((sh * 255).astype(np.uint8)).resize((grid, grid), Image.LANCZOS) \
         .save(os.path.join(outdir, "shadow.png"))
    print(f"[shadow] 平均受照 {sh.mean():.2f}  全影占比 {(sh < 0.15).mean()*100:.1f}%")

    # --- AO(环境光遮蔽):双尺度曲率 ---
    # ambient_occlusion 原始输出只有 0.55~1.0,压暗太弱(实测画面 std 几乎不变),
    # 这里重映射到 0.48~1.0 再双尺度叠加。高差小/阴影少的地形(如高原面)
    # 全靠 AO 提供体量感,否则整片读成"白石膏"。
    def _stretch(a):
        t = np.clip((a - 0.55) / 0.45, 0.0, 1.0)
        t = t * t * (3 - 2 * t)
        return 0.48 + 0.52 * t

    cell = km * 1000.0 / (grid - 1)
    ao = 0.58 * _stretch(SH.ambient_occlusion(g, cell, radius_px=14)) \
        + 0.42 * _stretch(SH.ambient_occlusion(g, cell, radius_px=55))
    ao = np.clip(ao, 0, 1)
    Image.fromarray((ao * 255).astype(np.uint8)).resize((1024, 1024), Image.LANCZOS) \
         .save(os.path.join(outdir, "ao.png"))
    print(f"[ao] 均值 {ao.mean():.2f}  暗部(<0.6) {(ao < 0.6).mean()*100:.1f}%")

    # --- 逐像素法线:比顶点法线细一个数量级,是近景细节的关键 ---
    NM = 2048
    gn = np.asarray(Image.fromarray(elev, mode="F").resize((NM, NM), Image.LANCZOS),
                    np.float32)
    c = km * 1000.0 / NM
    gy, gx = np.gradient(gn, c)
    nx, ny, nz = -gx, np.ones_like(gx), -gy      # 世界系: x 东, y 上, z = -北
    ln = np.sqrt(nx * nx + ny * ny + nz * nz)
    nmap = np.stack([nx / ln, ny / ln, nz / ln], -1) * 0.5 + 0.5
    Image.fromarray((np.clip(nmap, 0, 1) * 255).astype(np.uint8)) \
         .save(os.path.join(outdir, "normal.png"))

    # --- 卫星影像 ---
    im = Image.fromarray(sat)
    if max(im.size) > tex:
        k = tex / max(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    im.save(os.path.join(outdir, "satellite.jpg"), quality=90)

    meta = dict(name=name, latin=latin or name, sub=sub,
                grid=grid, km=km, minH=lo, maxH=hi,
                lat=lat, lon=lon, sunAz=sun_az, sunAlt=sun_alt,
                texW=im.width, texH=im.height)
    with open(os.path.join(outdir, "world.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if gen_clouds:
        CL.build(outdir=outdir)
        CL.build_cumulus(outdir=outdir)
        CL.build_sky(outdir=outdir)
        CL.build_detail(outdir=outdir)

    print(f"[ok] {outdir}/  高程 {lo:.0f}-{hi:.0f}m  网格 {grid}x{grid}  "
          f"纹理 {im.width}x{im.height}")
    return meta


def main():
    ap = argparse.ArgumentParser(description="构建三维地形飞行场景所需的全部资源")
    ap.add_argument("--name", default="未命名")
    ap.add_argument("--sub", default="")
    ap.add_argument("--latin", default="")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--km", type=float, default=20.0, help="区域宽度(公里)")
    ap.add_argument("--grid", type=int, default=640, help="高程网格分辨率")
    ap.add_argument("--tex", type=int, default=4096, help="卫星影像最大边长")
    ap.add_argument("--sun-az", type=float, default=318.0, help="太阳方位角(正北起顺时针)")
    ap.add_argument("--sun-alt", type=float, default=33.0, help="太阳高度角")
    ap.add_argument("--outdir", default="web")
    ap.add_argument("--cache-tiles", default="cache_tiles")
    ap.add_argument("--cache-imagery", default="cache_imagery")
    ap.add_argument("--no-clouds", action="store_true", help="跳过云贴图生成")
    a = ap.parse_args()
    build(name=a.name, sub=a.sub, latin=a.latin, lat=a.lat, lon=a.lon, km=a.km,
          grid=a.grid, tex=a.tex, sun_az=a.sun_az, sun_alt=a.sun_alt,
          outdir=a.outdir, cache_tiles=a.cache_tiles, cache_imagery=a.cache_imagery,
          gen_clouds=not a.no_clouds)


if __name__ == "__main__":
    main()
