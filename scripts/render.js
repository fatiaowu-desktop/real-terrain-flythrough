// 逐帧渲染驱动:headless Chrome + WebGL(软件光栅 SwiftShader)
//
// 用法:
//   export NODE_PATH=<包含 puppeteer-core 的 node_modules>   # 必需
//   PORT=8765 FRAMES=240 node render.js
//
// 环境变量:
//   PORT    本地 http 服务端口 (默认 8765)
//   FRAMES  帧数 (默认 240)
//   OUTDIR  帧输出目录 (默认 ./frames)
//   CHROME  Chrome 可执行文件绝对路径
//   WIDTH / HEIGHT  分辨率 (默认 1280x720)
//   PAGE    页面路径 (默认 /web/fly.html)
//
// 速度参考:软件光栅约 0.2s/帧 @1280x720,240 帧约 1 分钟。
// **注意:无头浏览器给出的 fps 完全不可信(软件光栅只有 1-3fps),
//   它只能验证"画面对不对",不能验证"跑得快不快"。**
const puppeteer = require('puppeteer-core');
const fs = require('fs');
const path = require('path');

const CHROME = process.env.CHROME ||
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = process.env.PORT || 8765;
const FRAMES = parseInt(process.env.FRAMES || '240', 10);
const OUTDIR = path.resolve(process.env.OUTDIR || 'frames');
const W = parseInt(process.env.WIDTH || '1280', 10);
const H = parseInt(process.env.HEIGHT || '720', 10);
const PAGE = process.env.PAGE || '/web/fly.html';

const timeout = (ms) => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUTDIR, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: [
      '--enable-unsafe-swiftshader',
      '--use-gl=angle', '--use-angle=swiftshader',
      '--no-sandbox', '--disable-gpu-sandbox',
      '--hide-scrollbars', '--mute-audio',
      `--window-size=${W},${H}`,
    ],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: W, height: H, deviceScaleFactor: 1 });
  // 必须禁用缓存:否则重烘的贴图(sky.png 等)会被浏览器旧缓存盖住,
  // 表现为"参数改了但画面数值逐位相同",极难排查。
  await page.setCacheEnabled(false);
  page.on('console', m => console.log('[page]', m.text()));
  page.on('pageerror', e => console.error('[pageerror]', e.message));

  const t0 = Date.now();
  await page.goto(`http://127.0.0.1:${PORT}${PAGE}?t=${Date.now()}`, { waitUntil: 'load', timeout: 120000 });
  await page.waitForFunction('window.__ready === true', { timeout: 180000 });
  console.log(`[init] ${((Date.now() - t0) / 1000).toFixed(1)}s`);

  for (let i = 0; i < FRAMES; i++) {
    const t = FRAMES === 1 ? 0 : i / (FRAMES - 1);
    await page.evaluate((tt) => window.renderFrame(tt), t);
    const file = path.join(OUTDIR, `f${String(i).padStart(4, '0')}.png`);
    await page.screenshot({ path: file, clip: { x: 0, y: 0, width: W, height: H } });
    if (i % 20 === 0) {
      const el = (Date.now() - t0) / 1000;
      console.log(`[frame] ${i}/${FRAMES}  ${(el / (i + 1)).toFixed(2)}s/frame  ` +
                  `剩余约 ${(((FRAMES - i) * el / (i + 1)) / 60).toFixed(1)}min`);
    }
  }
  console.log(`[done] ${FRAMES} 帧  总 ${((Date.now() - t0) / 60000).toFixed(1)}min`);

  // 关浏览器可能巨慢(实测卡 30s,曾占全片耗时的 90%)。
  // 帧已全部落盘,不必等它 —— 进程退出时 puppeteer 自己的 exit hook 会杀掉残余进程。
  await Promise.race([browser.close().catch(() => {}), timeout(1500)]);
  process.exit(0);
})();
