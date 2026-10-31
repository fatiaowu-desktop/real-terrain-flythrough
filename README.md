# 真实地形穿越 · Real Terrain Flythrough

> 给一个经纬度，还你一段真实山脉的三维飞行成片。

输入任意地点的经纬度和范围，输出一段带卫星纹理、真实光照、云层与天空的三维地形穿越动画（mp4）。全程免 key、免注册、不依赖 Blender 或 GPU。

```
--lat 28.4373 --lon 98.6817 --km 22   →   out/fly.mp4 (1280×720 / 30fps / 8s)
```

---

## 亮点

**1. 任意地点，一条命令出片**
不需要准备模型、不需要手工建模。经纬度 + 范围（km）就是全部输入，换一座山只是改两个数字。场景由 `world.json` 驱动，**换地点不用改任何代码**。

**2. 全程免 key、免注册**
高程走 AWS 公开 Terrarium DEM 瓦片，纹理走 ESRI World Imagery，两者严格同 zoom 同裁剪、像素级对齐。没有 API key，没有配额，没有账号。

**3. 不需要 Blender，也不需要 GPU**
three.js 场景 + headless Chrome 逐帧渲染，软件光栅下 240 帧约 1 分钟，一条命令出 mp4。three.js r160 随包提供，离线可用。

**4. 真实感靠三件实打实的东西，不是滤镜**
- **压反照率**：卫星图里雪的反照率接近满值，直出会裁成死白；把亮度交还给光照决定。
- **预计算投射阴影**：沿太阳方向做投影步进，山体层次约 80% 来自「山挡山」，不是来自网格密度。
- **逐像素法线贴图**：顶点法线 37 m 一个太粗，烘 2048 法线贴图补足近景细节。

**5. 三层云系，不是贴图糊一片**
天空穹顶（相机转动时云有正确视差）+ 低位云海（峰尖从云里戳出）+ 真实尺度积云场（云宽 1.2–4.6 km，按真实积云尺度来，带距离相关的空气透视）。太阳辉光与地形光照必然同侧。

**6. 数值判色：把「调观感」从玄学变成可测量**
这是本 skill 最有差异化的部分。三维观感调试最大的陷阱是**肉眼和视觉模型的误判率极高**——本管线开发过程中，视觉判读连报数轮「天空发灰/雪面青绿」，而实测数值否定了两条。因此内置 `measure.py`，给出可复算的健康区间：

| 指标 | 健康区间 | 越界含义 |
|---|---|---|
| 天空饱和度（上 35%） | 0.30–0.45 | < 0.25 发灰 → 多半有巨型云挡镜头 |
| 天空亮度（上 35%） | 0.50–0.65 | > 0.75 过曝 → 加性辉光过大 |
| 天空云像素占比 | 10%–30% | < 3% 云被抹没了 |
| 地形亮部 RGB 三通道差 | < 0.05 | 差值大 = 有色偏 |

同一份判据可以量贴图也可以量合成帧，**一步区分「素材问题」还是「场景问题」**。`references/pitfalls.md` 记录了九个反复踩、且直觉会指向错误方向的坑。

---

## 安装

**Codex**

```
$skill-installer install https://github.com/fatiaowu-desktop/real-terrain-flythrough
```

或手动放到 `~/.agents/skills/real-terrain-flythrough/`（项目级放 `.agents/skills/`），重启后 `/skills` 可见，也可用 `$real-terrain-flythrough` 显式调用。

**WorkBuddy**：技能面板 → 添加技能 → 上传技能，选择 `real-terrain-flythrough.zip`。

## 快速开始

```bash
# 环境（每个环境一次）
python3 -m venv .venv && .venv/bin/pip install numpy pillow
python3 scripts/fetch_three.py --dest vendor

# 1. 骨架
mkdir -p web && cp assets/fly.html web/

# 2. 抓数据 + 烘焙资源（换地点只需重跑这一步）
.venv/bin/python scripts/build_world.py \
    --name 卡瓦格博 --lat 28.4373 --lon 98.6817 --km 22 \
    --sun-az 318 --sun-alt 33 --outdir web

# 3. 起服务并用 1 帧冒烟（http.server 必须常驻后台）
python3 -m http.server 8765

# 4. 数值判读（先看数，再看图）
FRAMES=1 PORT=8765 node scripts/render.js
.venv/bin/python scripts/measure.py frames/f0000.png --grid

# 5. 成片
FRAMES=240 PORT=8765 node scripts/render.js
.venv/bin/python scripts/encode.py --frames frames --out out/fly.mp4 --fps 30 --crf 20
```

太阳高度角 20–35° 是山地飞行的甜点：阴影长、层次强。改了 `--sun-az/--sun-alt` 必须重跑 `build_world.py` 重烘阴影与法线。

## 已验证案例

| 地点 | 坐标 | 结果 |
|---|---|---|
| 卡瓦格博（梅里雪山） | 28.4373, 98.6817 | 成片 1280×720 / 8s，全链路通过 |
| 马特洪峰（阿尔卑斯） | 45.9763, 7.6586 | 高程 1593–4345 m（真实约 4478 m），60 帧 + 编码通过，判据全绿 |

## 能力边界

- 云是**公告板精灵**，没有体积自阴影与光线步进。彻底去贴纸感需上体积云（ray marching），但软件光栅下单帧会从 0.2 s 涨到几十秒，必须换 GPU 渲染。
- 无头浏览器的 **fps 不可信**（软件光栅只有 1–3 fps），它只能验证画面对不对。
- 默认 DEM 约 30 m/px，近景细节靠法线贴图 + 卫星影像，不靠网格密度。需要 1 m 可换 USGS 3DEP（见 `references/data-sources.md`）。

## 结构

```
SKILL.md              工作流与调参速查
scripts/              9 个脚本：抓高程 / 影像 / 烘阴影 / 云贴图 / 渲染 / 编码 / 数值判色
assets/fly.html       已验证的 three.js 场景模板（由 world.json 驱动）
assets/three.module.js  r160
references/pitfalls.md   动手前必读：九个高频坑
references/data-sources.md  数据源端点、解码公式、备选高程源
```
