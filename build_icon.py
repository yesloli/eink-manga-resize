# -*- coding: utf-8 -*-
"""
生成应用图标：墨青底 + 白色「漫」+ 细白圆环。

产出三份，都写进 assets/：
  app.ico          多尺寸图标（EXE / 快捷方式用）
  app_header.png   72px，界面顶栏用（Tk 只认 png，不认 ico）
  app_sizes.png    16~128px 对照条，用于肉眼检查小尺寸是否糊

字标渲染：先按 getbbox 量出真实墨迹框再居中（中文字形的字身框上下不对称，
直接按字号框居中会显得偏高）。加粗只在 192px 以上启用——黑体笔画本就密集，
小图标再描边会糊成一块白斑。

依赖：Pillow，以及一款带「漫」字的中文字体。
"""
import os

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "assets")

S = 512
RADIUS = 112
TEAL = (13, 118, 128, 255)
WHITE = (255, 255, 255, 255)
RING = (255, 255, 255, 130)
CHAR = "漫"

# 各平台常见中文字体，按顺序找第一个存在的
FONT_CANDIDATES = [
    "C:/Windows/Fonts/simhei.ttf",                     # Windows 黑体
    "C:/Windows/Fonts/msyhbd.ttc",                     # Windows 雅黑粗体
    "/System/Library/Fonts/PingFang.ttc",              # macOS
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]


def find_font() -> str:
    for p in FONT_CANDIDATES:
        if os.path.isfile(p):
            return p
    env = os.environ.get("MRZ_FONT")
    if env and os.path.isfile(env):
        return env
    raise SystemExit(
        "找不到可用中文字体。请把路径设进环境变量 MRZ_FONT，"
        "例如：set MRZ_FONT=D:\\fonts\\SourceHanSans-Bold.otf")


def draw_char(im, ch, color, box, font_path, embolden=False):
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    lo, hi = 32, 900
    while lo < hi:                       # 二分找最大可用字号
        mid = (lo + hi + 1) // 2
        f = ImageFont.truetype(font_path, mid)
        l, t, r, b = f.getbbox(ch)
        if (r - l) <= bw and (b - t) <= bh:
            lo = mid
        else:
            hi = mid - 1
    font = ImageFont.truetype(font_path, lo)
    l, t, r, b = font.getbbox(ch)
    px = x0 + (bw - (r - l)) // 2 - l
    py = y0 + (bh - (b - t)) // 2 - t
    d = ImageDraw.Draw(im)
    d.text((px, py), ch, font=font, fill=color)
    if embolden:
        for ox, oy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            d.text((px + ox, py + oy), ch, font=font, fill=color)


def render(size: int, font_path: str) -> Image.Image:
    k = size / float(S)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=max(2, int(RADIUS * k)), fill=TEAL)
    # 细白圆环
    d.ellipse([int(42 * k), int(42 * k), int(470 * k), int(470 * k)],
              outline=RING, width=max(1, int(8 * k)))
    draw_char(im, CHAR, WHITE,
              (int(110 * k), int(96 * k), int(402 * k), int(396 * k)),
              font_path, embolden=(size >= 192))
    return im


def main():
    font = find_font()
    os.makedirs(OUT, exist_ok=True)
    master = render(S, font)

    ico = os.path.join(OUT, "app.ico")
    master.save(ico, format="ICO",
                sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("icon   :", ico, os.path.getsize(ico), "bytes")

    png256 = os.path.join(OUT, "app_preview.png")
    master.resize((256, 256), Image.LANCZOS).save(png256)
    print("preview:", png256)

    hdr = os.path.join(OUT, "app_header.png")
    render(72, font).save(hdr)
    print("header :", hdr)

    # 各尺寸对照条，确认小图不糊
    items = (16, 24, 32, 48, 64, 128)
    width = 8 + sum(items) + 24 * len(items)
    strip = Image.new("RGBA", (width, 140), (238, 241, 246, 255))
    x = 8
    for s in items:
        ic = render(s, font)
        strip.paste(ic, (x, (140 - s) // 2), ic)
        x += s + 24
    strip.save(os.path.join(OUT, "app_sizes.png"))
    print("sizes  :", os.path.join(OUT, "app_sizes.png"))


if __name__ == "__main__":
    main()
