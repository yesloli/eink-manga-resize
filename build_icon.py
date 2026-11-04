# -*- coding: utf-8 -*-
"""
生成应用图标：墨青底 + 微胶囊。

为什么是微胶囊
--------------
电子墨水的物理结构就是一颗颗微胶囊：里面悬浮着带正电的白色粒子和带负电的
黑色粒子，加电场后黑白换位，像素就变了颜色。这个符号既准确又不像「一格
灰阶条」那样容易被误认成柱状图。

尺寸分级
--------
小图标不能等比缩小大图标——胶囊里的粒子在 24px 以下会糊成噪点。所以：
  ≥96px  每半 6 颗粒子
  ≥32px  每半 2 颗粒子
  <32px  不画粒子，只留白 / 墨两截和描边，保证轮廓清楚
描边试过 12px / 8px / 不加三种：加了会在底部形成一圈白瓷似的弧，
反而更脏；不加时深色那半和墨青底本身就有足够对比，所以不描边。

产出三份，都写进 assets/：
  app.ico          多尺寸图标（EXE / 快捷方式用）
  app_header.png   72px，界面顶栏用（Tk 只认 png，不认 ico）
  app_sizes.png    16~128px 对照条，用于肉眼检查小尺寸是否糊
"""
import os

from PIL import Image, ImageDraw

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "assets")

S = 512
RADIUS = 112
TEAL = (13, 118, 128, 255)
WHITE = (255, 255, 255, 255)
INK = (10, 40, 45, 255)

# 胶囊在 512 画布上的位置
CAP = (172, 62, 340, 450)


def dots_for(size: int) -> int:
    if size >= 96:
        return 2      # 每半 6 颗
    if size >= 32:
        return 1      # 每半 2 颗
    return 0          # 不画


def render(size: int) -> Image.Image:
    k = size / float(S)
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle([0, 0, size - 1, size - 1],
                        radius=max(2, int(RADIUS * k)), fill=TEAL)

    x0, y0, x1, y1 = [v * k for v in CAP]
    r = (x1 - x0) / 2.0
    mid = (y0 + y1) / 2.0

    # 用蒙版拼两截：直接画两个圆角矩形的话，各自的圆角会把接缝和底部拱形
    # 错开，深色那半会缩进白色里露出白边。
    body = Image.new("L", (size, size), 0)
    ImageDraw.Draw(body).rounded_rectangle([x0, y0, x1, y1], radius=r, fill=255)

    im.paste(Image.new("RGBA", (size, size), WHITE), (0, 0), body)

    lower = Image.new("L", (size, size), 0)
    ImageDraw.Draw(lower).rectangle([0, mid, size, size], fill=255)
    dark_area = Image.new("L", (size, size), 0)
    dark_area.paste(lower, (0, 0), body)
    im.paste(Image.new("RGBA", (size, size), INK), (0, 0), dark_area)

    n = dots_for(size)
    if n:
        cols = (0.32, 0.68)
        rows_top = (0.26, 0.44) if n == 1 else (0.22, 0.38, 0.54)
        rows_bot = (0.62, 0.80) if n == 1 else (0.60, 0.76, 0.92)
        rad = (15 if n == 1 else 13) * k
        for cx in cols:
            px = x0 + (x1 - x0) * cx
            for ry in rows_top:
                py = y0 + (y1 - y0) * ry
                d.ellipse([px - rad, py - rad, px + rad, py + rad], fill=INK)
            for ry in rows_bot:
                py = y0 + (y1 - y0) * ry
                d.ellipse([px - rad, py - rad, px + rad, py + rad], fill=WHITE)
    return im


def main():
    os.makedirs(OUT, exist_ok=True)
    master = render(S)

    ico = os.path.join(OUT, "app.ico")
    master.save(ico, format="ICO",
                sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("icon   :", ico, os.path.getsize(ico), "bytes")

    png256 = os.path.join(OUT, "app_preview.png")
    master.resize((256, 256), Image.LANCZOS).save(png256)
    print("preview:", png256)

    hdr = os.path.join(OUT, "app_header.png")
    render(72).save(hdr)
    print("header :", hdr)

    items = (16, 24, 32, 48, 64, 128)
    width = 8 + sum(items) + 24 * len(items)
    strip = Image.new("RGBA", (width, 140), (238, 241, 246, 255))
    x = 8
    for s in items:
        ic = render(s)
        strip.paste(ic, (x, (140 - s) // 2), ic)
        x += s + 24
    strip.save(os.path.join(OUT, "app_sizes.png"))
    print("sizes  :", os.path.join(OUT, "app_sizes.png"))


if __name__ == "__main__":
    main()
