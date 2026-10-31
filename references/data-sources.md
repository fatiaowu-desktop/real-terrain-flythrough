# 数据源

全部免 key、免注册。按本工作流的默认选择排列。

---

## 高程:Terrarium RGB DEM(默认)

- 端点:`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`
- 来源:AWS Open Data / Tilezen,基于 SRTM、GMTED、NED 等混合
- 解码:`h = R*256 + G + B/256 - 32768`(单位:米)
- 分辨率:zoom 决定。`res_m ≈ 156543.03 * cos(lat) / 2^z`
  | z | 赤道附近 | 中纬度(30°) |
  |---|---|---|
  | 10 | ~153 m | ~132 m |
  | 12 | ~38 m | ~33 m |
  | 14 | ~9.5 m | ~8.2 m |
- `pick_zoom` 默认把总像素压在 3600 以内(24 km 区域约落在 z=12),约 30 m/px

**必须处理的两件事**:
1. **重试**:TLS 握手偶发超时是常态,`fetch_tile` 已内置 4 次退避重试
2. **补洞**:缺失瓦片会返回异常值(< -400 或 > 9000),不补会渲染成黑缝,
   `fill_voids` 用多半径邻域插值逐级补

---

## 影像:ESRI World Imagery(默认)

- 端点:`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
- 注意 URL 里是 **`{z}/{y}/{x}`** 顺序(行在前),与 DEM 的 `{z}/{x}/{y}` 不同,别写反
- **必须与 DEM 用同一个 bbox 与裁剪逻辑**,否则贴图和地形错开。
  `build_world.py` 里影像比高程高一档 zoom(拿更多真实细节),裁剪代码完全一致

**已知偏差**:ESRI 影像整体绿通道偏高(实测某区 R0.417 / G0.483 / B0.417)。
直接贴上去雪会发青。`fly.html` 里的处理是**只对亮部(雪/冰)约束**:
`base.g = min(base.g, max(base.r, base.b))`,暗部植被的绿保留 ——
一刀切去绿会把植被也搞灰。

---

## 备选高程源

| 源 | 分辨率 | 覆盖 | 备注 |
|---|---|---|---|
| **USGS 3DEP** | 1 m(Lidar)/ 5~10 m | 美国境内 | 想榨最高细节时用它做对照,需走 The National Map API/下载 |
| **Copernicus DEM GLO-30/90** | 30 / 90 m | 全球 | 欧空局,需注册下载单个 tile |
| **NASADEM** | 30 m | 全球(±60°) | SRTM 重处理版,Earthdata 需注册 |
| **ALOS AW3D30** | 30 m | 全球 | 日本 JAXA,需注册 |

换源时只需替换 `scripts/dem.py` 里的 `build_elevation`,
保持返回 `(elev_m_array, meters_per_pixel, zoom)` 即可,下游不用改。

---

## 网格分辨率怎么选

`--grid` 是地形网格顶点数(默认 640,即 640×640 ≈ 41 万顶点)。

- 网格只决定**轮廓**精度;近景细节主要来自 **2048 法线贴图 + 卫星影像**,不在网格上
- 640 在软件光栅下每帧约 0.2s,是可用的甜点
- 提到 1024 顶点数翻 2.6 倍,但视觉收益很小(因为细节靠法线贴图)

法线贴图强度:shader 里是 `mix(顶点法线, 贴图法线, 0.34)`。
强度调高会在雪面上出现斑块 —— 0.34 是压过的稳定值,别轻易上调。
