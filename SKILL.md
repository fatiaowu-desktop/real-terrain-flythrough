---
name: real-terrain-flythrough
description: 用真实地点的高程(DEM)+ 卫星影像生成可飞行的三维地形场景并渲染成视频。当用户要求做真实地形、三维地形穿越/飞行动画、地形可视化、把某座山或某片区域做成 3D flythrough、给地形叠加卫星贴图、或要求地形场景加云层/天空/大气效果时使用。覆盖从经纬度到成片(mp4)的完整管线,数据源免 key。
agent_created: true
---

# Real Terrain Flythrough

## Overview

把任意经纬度 + 范围(km)变成一条真实地形的三维飞行穿越动画,输出 mp4。
管线全程免 key、免注册:公开 DEM 高程 + ESRI 卫星影像 → 烘焙法线/投射阴影/云贴图
→ three.js 场景 → headless Chrome 逐帧渲染 → ffmpeg 成片。

已验证产出:1280×720 / 30fps / 8s,软件光栅下 240 帧约 1 分钟。

**开始动手前必读** `references/pitfalls.md`。这套管线最难的不是搭起来,
而是调观感 —— 该文件记录了九个反复踩、且直觉会指向错误方向的坑,
其中"数值判色优先于视觉判读"决定了调色效率。

## 环境准备(每个环境一次)

```bash
python3 -m venv .venv && .venv/bin/pip install numpy pillow   # numpy 常不在默认 python3 里
python3 scripts/fetch_three.py --dest vendor                  # three.js r160(自带或 CDN)
```

渲染需要 `puppeteer-core` + 本机 Chrome。若不在项目里:

```bash
export NODE_PATH=/path/to/node_modules-containing-puppeteer-core   # 常是 ~/.workbuddy/binaries/node/workspace/node_modules
export CHROME=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome
```

## 工作流

设 `SKILL` 为本 skill 目录。约定工作目录结构(静态服务器的根):

```
<project>/vendor/three.module.js     # fetch_three.py 产出
<project>/web/fly.html               # assets/fly.html 拷贝
<project>/web/{heights,normal,shadow,ao}.png, satellite.jpg, {cloud,cumulus,sky}.png, world.json
```

### Step 1 — 搭骨架

```bash
python3 $SKILL/scripts/fetch_three.py --dest vendor
mkdir -p web && cp $SKILL/assets/fly.html web/
```

### Step 2 — 抓数据并烘焙资源

```bash
.venv/bin/python $SKILL/scripts/build_world.py \
    --name 卡瓦格博 --sub 梅里雪山 --lat 28.4373 --lon 98.6817 \
    --km 22 --grid 640 --sun-az 318 --sun-alt 33 --outdir web
```

产出 `web/` 下全部资源 + `world.json`(场景由它驱动,换地点只需重跑本步)。
首次会下载瓦片并缓存到 `cache_tiles/` `cache_imagery/`,换地点可直接复用。
加 `--no-clouds` 可跳过云贴图。

太阳角度:低角度(alt 20~35°)阴影长、层次强,是山地飞行的甜点。

### Step 3 — 起服务并冒烟

**必须用正式后台任务常驻** —— 用 `cmd &` 起的 http.server 会随命令结束被回收,
表现为渲染时 `ERR_CONNECTION_REFUSED`。

```bash
python3 -m http.server 8765          # 以 background 方式运行
```

先渲 1 帧确认无 `pageerror`、shader 编译无 `VALIDATE_STATUS false`:

```bash
FRAMES=1 PORT=8765 node $SKILL/scripts/render.js
```

### Step 4 — 判读(这一步不能省)

**先用数值判据,再看图。** 视觉判读在本任务上误判率很高。

```bash
.venv/bin/python $SKILL/scripts/measure.py frames/f0000.png --grid
```

| 指标 | 健康区间 | 越界含义 |
|---|---|---|
| 天空饱和度(上 35%) | **0.30~0.45** | <0.25 发灰 → 多半有巨型云挡镜头 |
| 天空亮度(上 35%) | 0.50~0.65 | >0.75 过曝 → 加性辉光过大 |
| 天空云像素占比 | 10%~30% | <3% 云被抹没了 |
| 地形亮部 RGB 三通道差 | <0.05 | 差值大 = 有色偏 |

量贴图本身(`web/sky.png`)与合成帧对比,可立刻区分"素材问题"和"场景问题"。

### Step 5 — 渲染成片

```bash
FRAMES=240 PORT=8765 node $SKILL/scripts/render.js        # 约 1 分钟
.venv/bin/python $SKILL/scripts/encode.py --frames frames --out out/fly.mp4 --fps 30 --crf 20
```

抽查多帧确认全片无退化:`measure.py frames/f0000.png frames/f0060.png frames/f0120.png --brief`

### Step 6 — 清理

帧序列归档到 `_scratch/`,停掉后台服务。
杀掉后台渲染任务后记得 `pkill -9 -f playwright_chromiumdev_profile`,
残留的孤儿 Chrome 会让后续任何测速失真。

## 调参速查

改 `web/fly.html`(顶部注释块列出了全部旋钮):

| 想改什么 | 动哪里 |
|---|---|
| 雪太白/死白 | 降 `ALBEDO_GAIN`(默认 0.72) |
| 远山糊成灰片 | 推远 `fogFar`、降混合量 `f * 0.50`;雾色要掺天空色 |
| 天空无云 | 重跑 `clouds.py`;检查是否误加了 band 限制 |
| 云像贴纸 | 加/强化按距离的空气透视;云宽改到 1.2~4.6 km |
| 换飞行路线 | `renderFrame()` 里的 theta / radius / 高度插值 |
| 换时刻光影 | `--sun-az/--sun-alt` 后**必须重跑 build_world.py** 重烘阴影和法线 |

改完一律回到 Step 4 复核数值。

## 资源

### scripts/
| 文件 | 用途 |
|---|---|
| `build_world.py` | **主编排**:经纬度 → web/ 全套资源 + world.json |
| `dem.py` | Terrarium DEM 抓取(bbox/zoom/瓦片/解码/补洞),也可 CLI 单独跑 |
| `imagery.py` | ESRI 卫星影像,裁剪逻辑与 DEM 严格对齐 |
| `shade.py` | 投射阴影预烘(山体层次约 80% 来自"山挡山")+ AO(高差小、阴影少的地形全靠它提供体量感) |
| `clouds.py` | 云海密度 / 积云 / 天空穹顶三张贴图 |
| `render.js` | headless Chrome 逐帧驱动 |
| `encode.py` | 帧序列 → mp4,以及改前/改后对比图 |
| `measure.py` | **数值判色**,主要的调参判据 |
| `fetch_three.py` | 准备 three.js r160 |

### assets/
- `fly.html` — 已验证的 three.js 场景模板(天空穹顶 / 低位云海 / 成簇积云 / 太阳辉光),
  由 `world.json` 驱动,换地点无需改代码。顶部注释含全部可调旋钮与判据
- `three.module.js` — r160,随包提供以保证离线可用

### references/
- `pitfalls.md` — **动手前必读**。九个高频坑 + 数值判据的来源与推导
- `data-sources.md` — 数据源端点、解码公式、分辨率对照、备选高程源(含 USGS 3DEP 1 m)

## 能力边界

- 云是**公告板精灵**,没有真正的体积自阴影与光线步进。要彻底去贴纸感需上体积云
  (ray marching),但软件光栅下单帧会从 0.2s 涨到几十秒,必须换 GPU 渲染
- 无头浏览器的 **fps 不可信**(软件光栅只有 1~3 fps);它只能验证画面对不对
- 默认 DEM 约 30 m/px,近景细节靠法线贴图 + 卫星影像,不是靠网格密度
