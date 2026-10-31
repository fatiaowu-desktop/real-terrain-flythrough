#!/usr/bin/env python3
"""准备 three.js。

优先从 skill 自带的 assets/three.module.js 拷贝(离线可用、版本锁死 r160);
若不存在则从 CDN 下载。产物放到 <项目>/vendor/three.module.js,
由 fly.html 的 importmap 引用。

用法:
    python3 fetch_three.py --dest vendor
"""
import argparse
import os
import shutil
import urllib.request

CDN = "https://unpkg.com/three@0.160.0/build/three.module.js"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="vendor", help="目标目录(通常是项目的 vendor/)")
    ap.add_argument("--version", default="0.160.0")
    a = ap.parse_args()

    os.makedirs(a.dest, exist_ok=True)
    out = os.path.join(a.dest, "three.module.js")

    here = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(here, "..", "assets", "three.module.js")
    if os.path.exists(bundled):
        shutil.copy(bundled, out)
        print(f"[ok] 从 skill 自带资源拷贝 -> {out}")
        return

    url = CDN if a.version == "0.160.0" else \
        f"https://unpkg.com/three@{a.version}/build/three.module.js"
    print(f"[download] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "terrain-flythrough/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    with open(out, "wb") as f:
        f.write(data)
    print(f"[ok] {out}  {len(data)/1024:.0f} KB")


if __name__ == "__main__":
    main()
