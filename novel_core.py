# -*- coding: utf-8 -*-
"""
轻小说模块 —— 屏幕 / 版心标定。

为什么测试页是 EPUB 而不是一张图片
--------------------------------
版心宽度只在「书里的图」上成立。把图片丢进阅读器的图片查看器，它一定满屏
显示，量出来永远等于屏幕宽，版心测不到。所以测试页做成一本极小的 EPUB：
正文里放一段文字 + 一块横版纯色块。阅读器按排版规则把它缩到版心宽度渲染，
截图上量这块色块，得到的就是版心。

色块为什么是纯黑且极大
--------------------
- 纯黑：墨水屏截图是灰度的，彩色区分度不可靠；黑白最稳
- 4000×2000：比任何阅读器的版心都宽，因此一定会被**缩小**到版心宽度。
  反过来如果图比版心窄、阅读器又不上采样，量出来就是原图宽度，错得没边。
- 2:1 横版：高度不会成为限制条件，宽度才能真实反映版心。
"""
import os
import posixpath
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from PIL import Image

import resize_core as core

TEST_EPUB_NAME = "屏幕标定测试.epub"
BLOCK_W, BLOCK_H = 4000, 2000       # 2:1，必然被缩小到版心宽

DARK_THRESHOLD = 100                # 色块判定阈值（纯黑，留足余量）
MIN_RUN_RATIO = 0.30                # 一行里至少这么长的连续黑段才算色块

GUIDE = """\
把生成的「%s」拷进阅读器 → 打开这本书 → 翻到第一页 → 对当前页面截图
→ 回到本程序点「从截图识别」。

注意：必须截「书里的这一页」，不能把这张图当图片打开看——图片查看器是满屏
显示，量不到版心。""" % TEST_EPUB_NAME


# ---------------------------------------------------------------------------
# 生成测试页
# ---------------------------------------------------------------------------

def _block_png(path):
    """纯黑色块。单色 PNG 压缩后只有几十 KB。"""
    im = Image.new("L", (BLOCK_W, BLOCK_H), 0)
    im.save(path, "PNG", optimize=True)


def _page_xhtml():
    return """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">
<head>
  <title>屏幕标定测试</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <h1>屏幕标定测试页</h1>
  <p>这一页用来测量阅读器的屏幕分辨率与版心宽度。请对当前页面截图，
  然后在「墨水屏阅读器点对点适配」的轻小说模块里点「从截图识别」。</p>
  <p>下面这块黑色横条应当刚好铺满版心宽度。如果它不是左右贯通的一条，
  或者被裁掉了边缘，请把阅读器的页边距设为平时使用的值后重新截图。</p>
  <div class="blk"><img src="block.png" alt=""/></div>
  <p>提示：截图请使用设备自带的截图功能，不要用手机拍屏幕。</p>
</body>
</html>
"""


def _style_css():
    # 关键：把图片周围所有可能的内边距、边框、最大宽度限制都清掉，
    # 让它严格等于版心宽度，否则量出来的值会偏小。
    return """\
html, body { margin: 0; padding: 0; }
h1 { font-size: 1.2em; margin: 0.4em 0; }
p { margin: 0.6em 0; line-height: 1.7; }
.blk { margin: 0.8em 0; padding: 0; text-align: center; }
.blk img { width: 100%; max-width: 100%; height: auto;
           margin: 0; padding: 0; border: 0; display: block; }
"""


def _container_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _content_opf():
    return """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:uuid:mrz-screen-calib-0001</dc:identifier>
    <dc:title>屏幕标定测试</dc:title>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="nav"  href="nav.xhtml"  media-type="application/xhtml+xml" properties="nav"/>
    <item id="page" href="page.xhtml" media-type="application/xhtml+xml"/>
    <item id="css"  href="style.css"  media-type="text/css"/>
    <item id="img"  href="block.png"  media-type="image/png"/>
  </manifest>
  <spine>
    <itemref idref="page"/>
  </spine>
</package>
"""


def _nav_xhtml():
    return """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目录</title></head>
<body>
  <nav epub:type="toc"><ol><li><a href="page.xhtml">屏幕标定测试页</a></li></ol></nav>
</body>
</html>
"""


def build_test_epub(dst: str) -> dict:
    """生成标定用的极小 EPUB。返回 {ok, path, size_kb, msg}。"""
    work = tempfile.mkdtemp(prefix="calib_")
    try:
        oebps = os.path.join(work, "OEBPS")
        meta = os.path.join(work, "META-INF")
        os.makedirs(oebps, exist_ok=True)
        os.makedirs(meta, exist_ok=True)

        _block_png(os.path.join(oebps, "block.png"))
        files = {
            "META-INF/container.xml": _container_xml(),
            "OEBPS/content.opf": _content_opf(),
            "OEBPS/nav.xhtml": _nav_xhtml(),
            "OEBPS/page.xhtml": _page_xhtml(),
            "OEBPS/style.css": _style_css(),
        }
        for rel, text in files.items():
            with open(os.path.join(work, rel.replace("/", os.sep)), "w",
                      encoding="utf-8", newline="\n") as f:
                f.write(text)

        tmp = dst + ".tmp"
        if os.path.exists(tmp):
            os.remove(tmp)
        with zipfile.ZipFile(tmp, "w") as z:
            # mimetype 必须是第一条且不压缩，否则部分阅读器直接判为非法
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            z.writestr(info, "application/epub+zip")
            for root, _, fs in os.walk(work):
                for f in sorted(fs):
                    p = os.path.join(root, f)
                    rel = os.path.relpath(p, work).replace("\\", "/")
                    z.write(p, rel, compress_type=zipfile.ZIP_DEFLATED)
        os.replace(tmp, dst)
        return {"ok": True, "path": dst,
                "size_kb": round(os.path.getsize(dst) / 1024.0, 1),
                "msg": "已生成标定测试页"}
    except Exception as e:
        try:
            if os.path.exists(dst + ".tmp"):
                os.remove(dst + ".tmp")
        except Exception:
            pass
        return {"ok": False, "path": dst, "size_kb": 0, "msg": "生成失败：%s" % e}
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# 从截图识别
# ---------------------------------------------------------------------------

def _longest_run_since(line: bytes, needle: bytes):
    """在一行里找一段足够长的连续暗像素，返回 (起, 止) 闭区间；没有则 None。"""
    i = line.find(needle)
    if i < 0:
        return None
    left = i
    while left > 0 and line[left - 1]:
        left -= 1
    right = i + len(needle)
    n = len(line)
    while right < n and line[right]:
        right += 1
    return left, right - 1


def _median(seq):
    s = sorted(seq)
    return s[len(s) // 2] if s else None


def analyze_shot(path: str) -> dict:
    """从标定页截图算出屏幕分辨率与版心宽度。

    返回 {ok, reason, screen, column, ratio, margin_l, margin_r, block, warn}
    """
    res = {"ok": False, "reason": "", "warn": "", "path": path}
    try:
        im = Image.open(path)
    except Exception as e:
        res["reason"] = "图片打不开：%s" % e
        return res

    w, h = im.size
    res["screen"] = (w, h)

    if w > h:
        if abs(w / float(h) - BLOCK_W / float(BLOCK_H)) < 0.06 and w >= 1000:
            res["reason"] = ("这张是测试页里的那块纯色图本身（%d×%d），不是阅读器截图。"
                             "请把测试 EPUB 放进阅读器，打开后对书里的那一页截图" % (w, h))
        else:
            res["reason"] = ("这是横向图（%d×%d）。标定页是竖屏阅读的，"
                             "请对竖屏状态的阅读器截图" % (w, h))
        return res
    if min(w, h) < 320:
        res["reason"] = "图太小（%d×%d），不像设备截图" % (w, h)
        return res

    g = im.convert("L")
    binary = g.point(lambda v: 255 if v < DARK_THRESHOLD else 0)
    raw = binary.tobytes()
    needle = b"\xff" * max(32, int(w * MIN_RUN_RATIO))

    hits = []
    for y in range(h):
        line = raw[y * w:(y + 1) * w]
        seg = _longest_run_since(line, needle)
        if seg:
            hits.append((y, seg[0], seg[1]))

    if not hits:
        if abs(w / float(h) - BLOCK_W / float(BLOCK_H)) < 0.05 and w < BLOCK_W:
            res["reason"] = ("这张看起来就是生成的测试图本身（%d×%d），不是阅读器截图。"
                             "请把测试 EPUB 放进阅读器，打开后对书里的那一页截图" % (w, h))
        else:
            res["reason"] = ("截图里找不到黑色横条。请确认翻到了标定测试页，"
                             "并且截图包含了整块黑色色块")
        return res

    # 长横条不止一种：阅读器的底部进度条、章节分隔线都可能是长横条。
    # 所以先按相邻行聚成独立色块，再挑「够高且面积最大」的那一块——
    # 真正的色块有几百行高，进度条只有两三个像素。
    bands, cur = [], [hits[0]]
    for prev, hit in zip(hits, hits[1:]):
        if hit[0] - prev[0] <= 2:
            cur.append(hit)
        else:
            bands.append(cur)
            cur = [hit]
    bands.append(cur)

    min_h = max(12, int(h * 0.02))
    best = None
    for band in bands:
        y0, y1 = band[0][0], band[-1][0]
        height = y1 - y0 + 1
        if height < min_h:
            continue
        bl = _median([b[1] for b in band])
        br = _median([b[2] for b in band])
        bw = br - bl + 1
        if bw <= 0:
            continue
        score = bw * height
        if best is None or score > best[0]:
            best = (score, y0, y1, bl, br, band)

    if best is None:
        res["reason"] = ("找到了长横条但都不够高（最高不足画面高度的 2%），"
                         "像是阅读器的进度条或分隔线，不是标定色块。"
                         "请确认翻到的确实是标定测试页")
        return res

    _, top, bottom, left, right, band = best
    col_w = right - left + 1
    col_h = bottom - top + 1

    # 矩形校验：色块的左右边在所有行上应该几乎一致，否则那是文字或图片而非色块
    off = sum(1 for b in band if abs(b[1] - left) > 3 or abs(b[2] - right) > 3)
    if off > len(band) * 0.05:
        res["reason"] = ("检测到的区域左右边缘不整齐（%d/%d 行偏离），"
                         "不像一块规整的纯色块。请重新截取标定测试页" % (off, len(band)))
        return res

    res.update({"column": col_w, "ratio": col_w / float(w),
                "margin_l": left, "margin_r": w - 1 - right,
                "block": (col_w, col_h), "rows": len(band)})

    if col_w > w * 1.01:
        res["reason"] = "色块超出画面（%d > %d），截图可能被裁过" % (col_w, w)
        return res

    # 比例校验：2:1 的图被等比缩放后高度应当约为宽度的一半
    expect_h = col_w / float(BLOCK_W) * BLOCK_H
    if abs(col_h - expect_h) > max(6, expect_h * 0.08):
        res["warn"] = ("色块高度 %d 与预期的 %d 差距较大，阅读器可能拉伸了图片。"
                       "宽度仍按实测值采用，但建议换一页再截一次确认"
                       % (col_h, int(expect_h)))

    if res["ratio"] < 0.45:
        res["reason"] = ("量到的色块只占屏宽 %.1f%%，不像正常版心。"
                         "可能是截图被裁剪或缩放过，请重新截整屏" % (res["ratio"] * 100))
        return res

    res["ok"] = True
    if res["ratio"] >= 0.985:
        res["reason"] = ("版心等于屏幕宽度（%.1f%%），说明这本测试书按满屏渲染图片。"
                         "轻小说模式可以直接用屏幕宽作为版心宽" % (res["ratio"] * 100))
    else:
        res["reason"] = ("版心占屏宽 %.1f%%，左右留白约 %d / %d px"
                         % (res["ratio"] * 100, res["margin_l"], res["margin_r"]))
    return res


# ---------------------------------------------------------------------------
# 轻小说插图适配
# ---------------------------------------------------------------------------
#
# 与漫画模块的根本差别：一本书里混着三种页面，各有各的目标宽度。
#   插图页（独立一屏）→ 贴屏     1440 × 1920
#   混排页（图文同页）→ 贴版心   1300 宽
#   文字页           → 不碰
# 所以这里不能复用 resize_core 那套「全局改 CSS + 全局改 viewport」的做法，
# 那会把整本书的版心改坏。

MIN_SIDE = 400          # 短边小于此值视为图标、分隔线，不动
ROT_RATIO = 1.35        # 整页插图宽高比超过此值才转 90°（4:3 的横插画不该转）
TOLERANCE = 0.01        # 与目标相差 1% 以内视为已达标，不重编码

BODY_RE = re.compile(r"<body\b[^>]*>(.*?)</body>", re.S | re.I)
HEAD_END_RE = re.compile(r"</head\s*>", re.I)
TAG_RE = re.compile(r"<[^>]+>")
IMG_RE = re.compile(r"<img\b[^>]*?/?>", re.I)
SVG_IMG_RE = re.compile(r"<image\b[^>]*?/?>", re.I)
SRC_RE = re.compile(r'\bsrc\s*=\s*"([^"]+)"', re.I)
HREF_RE = re.compile(r'\b(?:xlink:)?href\s*=\s*"([^"]+)"', re.I)
META_RE = re.compile(r"<meta\b[^>]*?/?>", re.I)
PAGE_EXT = (".xhtml", ".html", ".htm")
KF8_EXT = (".mobi", ".azw3", ".azw", ".prc")

_NAMED_ENT = {"nbsp": "160", "mdash": "8212", "ndash": "8211", "hellip": "8230",
              "ldquo": "8220", "rdquo": "8221", "lsquo": "8216", "rsquo": "8217",
              "midot": "183", "middot": "183", "times": "215", "copy": "169"}


class NovelTarget:
    """轻小说的目标：贴屏的宽高 + 混排页的版心宽。"""

    def __init__(self, screen_w: int = 1440, screen_h: int = 1920,
                 column_w: int = 1300):
        self.w = max(200, int(screen_w))
        self.h = max(200, int(screen_h))
        self.column = max(100, min(int(column_w), self.w))

    def plan(self, w: int, h: int):
        """等比装进屏幕，不裁切、不拉伸。"""
        s = min(self.w / float(w), self.h / float(h))
        tw = max(2, int(round(w * s)))
        th = max(2, int(round(h * s)))
        return tw + (tw % 2), th + (th % 2)

    def suffix(self) -> str:
        return "_%d屏" % self.h

    def label(self) -> str:
        return "屏幕 %d×%d，版心 %d" % (self.w, self.h, self.column)


def _body_of(text: str) -> str:
    m = BODY_RE.search(text)
    return m.group(1) if m else text


def _text_len(fragment: str) -> int:
    return len(re.sub(r"\s", "", TAG_RE.sub("", fragment)))


def _ent_sub(m):
    k = m.group(1)
    return "&#%s;" % _NAMED_ENT[k] if k in _NAMED_ENT else m.group(0)


def _xml_ok(text: str) -> bool:
    """能否作为 XML 解析。

    只用来做「改动前后」的对比：原本就不能解析的书（源书本身有毛病）不追究，
    原本能解析、改完不能，才说明是我们改坏的。
    """
    try:
        ET.fromstring(text)
        return True
    except Exception:
        pass
    t = re.sub(r"&([a-zA-Z][a-zA-Z0-9]*);", _ent_sub, text)
    t = re.sub(r"<!DOCTYPE[^>]*>", "", t, flags=re.I)
    try:
        ET.fromstring(t)
        return True
    except Exception:
        return False


def _resolve(base: str, href: str):
    href = href.split("#")[0].strip()
    if not href or href.lower().startswith(("http:", "https:", "data:", "mailto:")):
        return None
    return os.path.normpath(os.path.join(base, href.replace("/", os.sep)))


def _set_viewport(text: str, tw: int, th: int):
    """改已有的 viewport，没有就补一个。

    只在 <meta name="viewport"> 这个标签内部替换数字，不去碰跨标签的捕获组——
    那种写法一旦顺序错位就会生成 width=1360, height=19201500，直接 fatal。
    """
    for m in META_RE.finditer(text):
        tag = m.group(0)
        if "viewport" not in tag.lower():
            continue
        new = re.sub(r"(width\s*=\s*)\d+", lambda x: x.group(1) + str(tw), tag, count=1)
        new = re.sub(r"(height\s*=\s*)\d+", lambda x: x.group(1) + str(th), new, count=1)
        if new == tag:
            return text, False
        return text.replace(tag, new, 1), True
    meta = '<meta name="viewport" content="width=%d, height=%d"/>' % (tw, th)
    m = HEAD_END_RE.search(text)
    if m:
        return text[:m.start()] + meta + text[m.start():], True
    return text, False


def _fix_svg_attrs(text: str, tw: int, th: int) -> str:
    text = re.sub(r'(viewBox\s*=\s*"0 0 )\d+\s+\d+(")',
                  lambda m: "%s%d %d%s" % (m.group(1), tw, th, m.group(2)),
                  text, count=1)
    text = re.sub(r'(<image\b[^>]*?\bwidth\s*=\s*")\d+(")',
                  lambda m: m.group(1) + str(tw) + m.group(2), text, count=1)
    text = re.sub(r'(<image\b[^>]*?\bheight\s*=\s*")\d+(")',
                  lambda m: m.group(1) + str(th) + m.group(2), text, count=1)
    return text


def _img_full_width(tag: str) -> str:
    """给 img 设 width:100%，并保留自闭合斜杠——XHTML 里丢了 /> 就是致命错误。"""
    if re.search(r'\bstyle\s*=\s*"[^"]*"', tag):
        return re.sub(r'\bstyle\s*=\s*"[^"]*"',
                      'style="width:100%;height:auto"', tag, count=1)
    tail = "/>" if tag.rstrip().endswith("/>") else ">"
    body = tag.rstrip()[:-len(tail)].rstrip()
    return body + ' style="width:100%;height:auto"' + tail


def _plan_page(w: int, h: int, target: NovelTarget):
    """整页插图：先判断是否转 90°，再按旋转后的方向贴屏。"""
    rot = 0
    if w > h * ROT_RATIO:
        rot = 1
        w, h = h, w
    tw, th = target.plan(w, h)
    return tw, th, rot


def _do_page(text: str, page_path: str, target: NovelTarget, opts,
             jobs: list, st: dict, page_used: set) -> str:
    """整页插图页：缩放 + 可选旋转 + 改 viewBox / image / viewport。"""
    base = os.path.dirname(page_path)
    body = _body_of(text)

    svg_m = re.search(r"<svg\b[^>]*\bviewBox\s*=\s*\"0 0 (\d+)\s+(\d+)\"", body, re.I)
    if svg_m:
        kind = "A"
        end = body.find("</svg>", svg_m.start())
        block = body[svg_m.start(): end if end > 0 else len(body)]
        href_m = HREF_RE.search(block)
    else:
        kind = "B"
        img_m = IMG_RE.search(body)
        href_m = SRC_RE.search(img_m.group(0)) if img_m else None
    if not href_m:
        st["skip"] += 1
        return text

    p = _resolve(base, href_m.group(1))
    if not p or not os.path.isfile(p):
        st["skip"] += 1
        return text
    size = core.img_size_file(p)
    if not size or min(size) < MIN_SIDE:
        st["skip"] += 1
        return text

    page_used.add(p)          # 这张图以「整页插图」的身份出现，贴屏优先
    w0, h0 = size
    tw, th, rot = _plan_page(w0, h0, target)
    if rot:
        st["rot"] += 1

    near = (abs(tw - w0) <= max(2, w0 * TOLERANCE)
            and abs(th - h0) <= max(2, h0 * TOLERANCE))
    if rot == 0 and near:
        st["same"] += 1
    else:
        jobs.append((p, p, tw, th, rot))
        st["done"] += 1

    if kind == "A":
        text = _fix_svg_attrs(text, tw, th)
    else:
        text = IMG_RE.sub(lambda m: _img_full_width(m.group(0)), text, count=1)
    text, _ = _set_viewport(text, tw, th)
    return text


def _do_mixed(text: str, page_path: str, target: NovelTarget,
              jobs: list, st: dict, page_used: set) -> str:
    """混排页：只把内嵌图缩到版心宽，结构一点都不动。"""
    base = os.path.dirname(page_path)
    col = target.column

    def repl(m):
        tag = m.group(0)
        sm = SRC_RE.search(tag)
        if not sm:
            return tag
        p = _resolve(base, sm.group(1))
        if not p or not os.path.isfile(p):
            return tag
        if p in page_used:
            # 同一份文件已经是某张整页插图，按贴屏尺寸处理过；
            # 这里再缩会把那一页的 1:1 破坏掉，所以跳过。
            st["shared"] += 1
            return tag
        size = core.img_size_file(p)
        if not size:
            return tag
        w0, h0 = size
        if min(w0, h0) < MIN_SIDE:
            return tag
        if w0 <= col * 1.01:
            return tag                 # 不比版心宽，没必要动，也不放大
        if w0 < col * 0.5:
            return tag
        nh = max(2, int(round(h0 * col / float(w0))))
        jobs.append((p, p, col, nh, 0))
        st["done"] += 1
        st["inline"] += 1

        # 标签上若写死了更大的 width，一并改小，否则阅读器仍按老宽度排版
        def fix_w(mm):
            try:
                v = int(mm.group(2))
            except Exception:
                return mm.group(0)
            if v <= col:
                return mm.group(0)
            return mm.group(1) + str(col) + mm.group(3)

        return re.sub(r'(\bwidth\s*=\s*")(\d+)(")', fix_w, tag, count=1)

    return IMG_RE.sub(repl, text)


def process_epub_novel(src: str, dst: str, target: NovelTarget, opts,
                       log, progress=None):
    """处理一本 EPUB。返回 core.Result。"""
    work = tempfile.mkdtemp(prefix="ln_")
    st = {"page": 0, "mixed": 0, "text": 0, "skip": 0, "same": 0,
          "done": 0, "rot": 0, "inline": 0, "broke": 0, "shared": 0}
    jobs = []
    try:
        bad = core.unzip_tolerant(src, work)
        if bad:
            log("      源包有 %d 个条目 CRC 损坏，已跳过校验读取" % len(bad), "warn")

        pages = []
        for root, _, fs in os.walk(work):
            for f in fs:
                if f.lower().endswith(PAGE_EXT):
                    pages.append(os.path.join(root, f))
        pages.sort()

        items = []
        for page in pages:
            try:
                raw = open(page, encoding="utf-8").read()
            except Exception:
                continue
            body = _body_of(raw)
            n_img = len(IMG_RE.findall(body)) + len(SVG_IMG_RE.findall(body))
            if n_img == 0:
                st["text"] += 1
                continue
            kind = "page" if (n_img == 1 and _text_len(body) < 10) else "mixed"
            items.append((page, raw, kind))

        page_used = set()

        def apply(page, raw, new):
            if new == raw:
                return
            if _xml_ok(raw) and not _xml_ok(new):
                st["broke"] += 1
                log("      ⚠ %s 改写后结构不合法，已放弃该页改动"
                    % os.path.basename(page), "warn")
                return
            with open(page, "w", encoding="utf-8") as f:
                f.write(new)

        # 第一遍只处理插图页：先确定哪些图按「贴屏」尺寸走
        for page, raw, kind in items:
            if kind != "page":
                continue
            apply(page, raw, _do_page(raw, page, target, opts, jobs, st, page_used))
            st["page"] += 1
        # 第二遍处理混排页：被插图页占用的图不再缩到版心
        for page, raw, kind in items:
            if kind != "mixed":
                continue
            apply(page, raw, _do_mixed(raw, page, target, jobs, st, page_used))
            st["mixed"] += 1

        log("      插图页 %d（旋转 %d）· 混排页 %d（内嵌图 %d）· 文字页 %d · 跳过小图 %d"
            % (st["page"], st["rot"], st["mixed"], st["inline"], st["text"], st["skip"]))
        # 同一张图可能被多个页面引用（封面、目录缩略图），并发写同一个临时文件
        # 会撞出 WinError 5，所以按路径去重，取目标面积最大的那次
        uniq = {}
        for job in jobs:
            prev = uniq.get(job[0])
            if prev is None or job[2] * job[3] > prev[2] * prev[3]:
                uniq[job[0]] = job
        jobs = list(uniq.values())
        st["done"] = len(jobs)
        if jobs:
            core.process_images(jobs, opts, log, progress)
        core.pack_epub(work, dst)
        size_mb = os.path.getsize(dst) / 1048576.0
        msg = "完成"
        if st["broke"]:
            msg = "完成（%d 页因结构风险未改）" % st["broke"]
        elif st["done"] == 0:
            msg = "已适配，无需处理"
        return core.Result(True, msg, st["done"], st["skip"], size_mb)
    except Exception as e:
        return core.Result(False, "处理失败：%s" % e)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_kf8_novel(src: str, dst: str, target: NovelTarget, opts, log,
                      progress=None):
    """MOBI / AZW3：Calibre 转 EPUB → 处理 → Calibre 转回原格式。"""
    calibre = opts.calibre or core.find_calibre()
    if not calibre:
        return core.Result(False, "需要 Calibre 才能处理该格式")
    tmp = tempfile.mkdtemp(prefix="ln_kf8_")
    ext = os.path.splitext(src)[1].lower()
    try:
        mid_in = os.path.join(tmp, "in.epub")
        log("      Calibre 转 EPUB …")
        core._run([calibre, src, mid_in], log)
        if not os.path.exists(mid_in):
            return core.Result(False, "Calibre 转换失败")
        mid_out = os.path.join(tmp, "out.epub")
        r = process_epub_novel(mid_in, mid_out, target, opts, log, progress)
        if not r.ok:
            return r
        log("      Calibre 转回 %s …" % ext.lstrip(".").upper())
        tmp_out = os.path.splitext(dst)[0] + ".mrz_tmp" + ext
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        core._run([calibre, mid_out, tmp_out], log)
        if not os.path.exists(tmp_out):
            return core.Result(False, "Calibre 转回失败")
        core._commit(tmp_out, dst)
        return core.Result(True, "完成（经 Calibre 往返）", r.done, r.skipped,
                           os.path.getsize(dst) / 1048576.0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def process_novel(src: str, dst: str, target: NovelTarget, opts, log,
                  progress=None):
    ext = os.path.splitext(src)[1].lower()
    if ext == ".epub":
        return process_epub_novel(src, dst, target, opts, log, progress)
    if ext in KF8_EXT:
        return process_kf8_novel(src, dst, target, opts, log, progress)
    return core.Result(False, "轻小说模式只处理 EPUB / MOBI / AZW3")


def scan_novel(path: str, target: NovelTarget) -> dict:
    """轻量扫描：只读 XHTML 与图片头，不解码图片。"""
    info = {"path": path, "name": os.path.basename(path),
            "ext": os.path.splitext(path)[1].lower(),
            "size_mb": os.path.getsize(path) / 1048576.0,
            "page": 0, "mixed": 0, "text": 0, "need": 0, "err": ""}
    try:
        with zipfile.ZipFile(path) as z:
            sizes = {}
            for n in z.namelist():
                if n.lower().endswith(core.IMG_EXT):
                    try:
                        with z.open(n) as f:
                            sizes[n] = core.img_size_bytes(f.read(65536))
                    except Exception:
                        pass
            parsed = []
            for n in z.namelist():
                if not n.lower().endswith(PAGE_EXT):
                    continue
                try:
                    txt = z.read(n).decode("utf-8", "ignore")
                except Exception:
                    continue
                body = _body_of(txt)
                base = posixpath.dirname(n)
                # 只认 <img src> 和 <image href>，<a href> 是章节链接不是图
                raw_refs = []
                for tag in IMG_RE.findall(body):
                    m = SRC_RE.search(tag)
                    if m:
                        raw_refs.append(m.group(1))
                for tag in SVG_IMG_RE.findall(body):
                    m = HREF_RE.search(tag)
                    if m:
                        raw_refs.append(m.group(1))
                if not raw_refs:
                    info["text"] += 1
                    continue
                # href 是相对页面的，换算成包内路径才能查到尺寸
                refs = []
                for rel in raw_refs:
                    if rel.lower().startswith(("http:", "https:", "data:")):
                        continue
                    refs.append(posixpath.normpath(
                        posixpath.join(base, rel.split("#")[0].replace("\\", "/"))))
                kind = "page" if (len(raw_refs) == 1 and _text_len(body) < 10) else "mixed"
                parsed.append((kind, refs))

            # 先统计插图页占用了哪些图——混排页里的同一份文件按贴屏处理，
            # 不能算成「还要缩到版心」，否则判定会和实际处理对不上
            page_used = set()
            for kind, refs in parsed:
                if kind == "page" and refs:
                    size = sizes.get(refs[0])
                    if size and min(size) >= MIN_SIDE:
                        page_used.add(refs[0])
                        tw, th, rot = _plan_page(size[0], size[1], target)
                        if rot or abs(tw - size[0]) > max(2, size[0] * TOLERANCE):
                            info["need"] += 1
            for kind, refs in parsed:
                if kind == "page":
                    info["page"] += 1
                    continue
                info["mixed"] += 1
                for ref in refs:
                    if ref in page_used:
                        continue
                    size = sizes.get(ref)
                    if size and min(size) >= MIN_SIDE and size[0] > target.column * 1.01:
                        info["need"] += 1
    except Exception as e:
        info["err"] = "读取失败：%s" % e
    return info
