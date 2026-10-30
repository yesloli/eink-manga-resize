# -*- coding: utf-8 -*-
"""
墨水屏阅读器漫画优化 —— 处理引擎。

设计要点
--------
1. 唯一目标：让整页图片在目标屏幕上 1:1 显示，从而绕开阅读器糟糕的缩放算法。
   所以「输出像素高度」必须等于「屏幕高度」，这是唯一有意义的约束。
2. 缩放统一走 ffmpeg 的 swscale + spline（zscale 在 4:2:0 上会直接报错）。
3. EPUB / ZIP 内除了图片，还必须同步改尺寸声明，否则阅读器会把图缩回去。
4. 文字书必须跳过：本工具会改写整份 CSS 的 max-width/max-height，文字书会版心崩塌。
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zipfile

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp")

EXT_EPUB = (".epub",)
EXT_ZIP = (".zip", ".cbz")
EXT_PDF = (".pdf",)
EXT_KF8 = (".mobi", ".azw3", ".azw", ".prc")
SUPPORTED_EXT = EXT_EPUB + EXT_ZIP + EXT_PDF + EXT_KF8

TEXT_LIMIT = 20000          # 正文字数超过此值视为文字书
POINT_TOLERANCE = 2         # 高度差在此范围内算"已点对点"

FILTERS = ("spline", "lanczos", "bicubic", "bilinear")


# ---------------------------------------------------------------------------
# 环境探测
# ---------------------------------------------------------------------------

def find_ffmpeg(explicit: str | None = None) -> str | None:
    if explicit and os.path.isfile(explicit):
        return explicit

    p = shutil.which("ffmpeg")
    if p:
        return p

    cands = []
    la = os.environ.get("LOCALAPPDATA") or ""
    if la:
        cands += [
            os.path.join(la, "Programs", "ffmpeg", "ffmpeg.exe"),
            os.path.join(la, "Programs", "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(la, "Microsoft", "WinGet", "Links", "ffmpeg.exe"),
            os.path.join(la, "Microsoft", "WinGet", "Packages"),
        ]
    cands += [
        os.path.expanduser(r"~\scoop\shims\ffmpeg.exe"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
    ]
    if getattr(sys, "frozen", False):
        base = os.path.dirname(sys.executable)
        cands = [
            os.path.join(base, "ffmpeg.exe"),
            os.path.join(base, "bin", "ffmpeg.exe"),
            os.path.join(base, "tools", "ffmpeg.exe"),
            os.path.join(base, "ffmpeg", "ffmpeg.exe"),
        ] + cands

    for c in cands:
        if c and os.path.isfile(c):
            return c
        if c and os.path.isdir(c) and "Packages" in c:
            try:
                for d in os.listdir(c):
                    if d.lower().startswith("ffmpeg"):
                        for root, _, fs in os.walk(os.path.join(c, d)):
                            if "ffmpeg.exe" in fs:
                                return os.path.join(root, "ffmpeg.exe")
            except Exception:
                pass
    return None


def find_calibre(explicit: str | None = None) -> str | None:
    if explicit and os.path.isfile(explicit):
        return explicit
    p = shutil.which("ebook-convert")
    if p:
        return p
    cands = [
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Calibre2\ebook-convert.exe"),
        r"D:\Program Files\Calibre2\ebook-convert.exe",
        r"E:\Program Files\Calibre2\ebook-convert.exe",
    ]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def probe_pdf_renderer() -> bool:
    try:
        import pypdfium2  # noqa: F401
        return True
    except Exception:
        pass
    try:
        import fitz  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 图片尺寸识别（只读文件头，不解码）
# ---------------------------------------------------------------------------

def _be16(b, i):
    return (b[i] << 8) | b[i + 1]


def _be32(b, i):
    return (b[i] << 24) | (b[i + 1] << 16) | (b[i + 2] << 8) | b[i + 3]


def jpeg_size(d: bytes):
    n = len(d)
    i = 2
    while i < n - 1:
        if d[i] != 0xFF:
            i += 1
            continue
        while i < n and d[i] == 0xFF:
            i += 1
        if i >= n:
            return None
        m = d[i]
        i += 1
        if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
            continue
        if i + 2 > n:
            return None
        seglen = _be16(d, i)
        if seglen < 2:
            return None
        if 0xC0 <= m <= 0xCF and m not in (0xC4, 0xC8, 0xCC):
            if i + 7 > n:
                return None
            h = _be16(d, i + 3)
            w = _be16(d, i + 5)
            return (w, h)
        i += seglen
    return None


def webp_size(d: bytes):
    try:
        if d[:4] != b"RIFF" or d[8:12] != b"WEBP":
            return None
        sub = d[12:16]
        if sub == b"VP8X":
            w = (d[24] | (d[25] << 8) | (d[26] << 16)) + 1
            h = (d[27] | (d[28] << 8) | (d[29] << 16)) + 1
            return (w, h)
        if sub == b"VP8 ":
            i = d.find(b"\x9d\x01\x2a", 20)
            if i < 0:
                return None
            w = ((d[i + 3] | (d[i + 4] << 8)) & 0x3FFF)
            h = ((d[i + 5] | (d[i + 6] << 8)) & 0x3FFF)
            return (w, h)
        if sub == b"VP8L":
            b = d[21:26]
            bits = b[0] | (b[1] << 8) | (b[2] << 16) | (b[3] << 24)
            w = (bits & 0x3FFF) + 1
            h = ((bits >> 14) & 0x3FFF) + 1
            return (w, h)
    except Exception:
        pass
    return None


def img_size_bytes(d: bytes):
    if len(d) < 26:
        return None
    if d[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", d[16:24])
    if d[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", d[6:10])
    if d[:2] == b"\xff\xd8":
        return jpeg_size(d)
    if d[:4] == b"RIFF" and d[8:12] == b"WEBP":
        return webp_size(d)
    if d[:2] == b"BM":
        w, h = struct.unpack("<ii", d[18:26])
        return (abs(w), abs(h))
    return None


def img_size_file(path: str):
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
        s = img_size_bytes(head)
        if s:
            return s
        # JPEG 的 SOF 可能落在 64KB 之后（少见但存在）
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        try:
            from PIL import Image
            with Image.open(path) as im:
                return im.size
        except Exception:
            return None


def image_format(path: str) -> str:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return (im.format or "").upper()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 目标规格与缩放计划
# ---------------------------------------------------------------------------

class Target:
    """目标屏幕的像素框 (w, h)，竖屏。

    缩放规则只有一条：**等比例缩放到刚好放进这个框，不裁切**。
        s = min(w目标 / w原图, h目标 / h原图)
    竖版页（3:4）会由高度约束，横跨页（2:1）会由宽度约束，都是满的一边贴边、
    另一边等比留白，画面永远完整。

    曾经区分过「高度对齐 / 宽度对齐 / 完整适配」三种模式，但三者加上"溢出保护"
    之后化简都是同一个 min()，属于无效选项，已合并。
    """

    def __init__(self, w: int, h: int):
        self.w = max(1, int(w))
        self.h = max(1, int(h))

    def plan(self, w: int, h: int):
        """返回 (tw, th)。等比缩放，不裁切。"""
        s = min(self.w / float(w), self.h / float(h))
        tw = int(round(w * s))
        th = int(round(h * s))
        tw = max(2, tw + (tw % 2))
        th = max(2, th + (th % 2))
        return tw, th

    def is_point(self, tw: int, th: int) -> bool:
        """按面积判定：缩放倍率接近 1 就算已点对点。"""
        return abs(th - self.h) <= POINT_TOLERANCE or abs(tw - self.w) <= POINT_TOLERANCE

    def suffix(self) -> str:
        return "_%d高" % self.h

    def label(self) -> str:
        return "等比撑满 %d×%d" % (self.w, self.h)


class Options:
    def __init__(self, filter_name="spline", quality=2, overwrite=False,
                 keep_mtime=False, ffmpeg=None, calibre=None, workers=None,
                 rotate_spread=False, rotate_dir=1):
        self.filter = filter_name if filter_name in FILTERS else "spline"
        self.quality = max(1, min(8, int(quality)))
        self.overwrite = overwrite
        self.keep_mtime = keep_mtime
        self.ffmpeg = ffmpeg
        self.calibre = calibre
        self.workers = workers or min(8, (os.cpu_count() or 4))
        # 横跨页优化：横图先转 90° 再按竖图缩放
        self.rotate_spread = bool(rotate_spread)
        self.rotate_dir = 2 if int(rotate_dir) == 2 else 1   # 1=顺时针 2=逆时针

    def rot_of(self, w: int, h: int, target_w: int, target_h: int) -> int:
        """该图是否需要旋转。只有竖屏目标 + 明显横图才转。"""
        if not self.rotate_spread:
            return 0
        if target_w >= target_h:        # 目标是横屏（显示器/横持），转了反而错
            return 0
        if w < h * SPREAD_RATIO:        # 不够横，别动
            return 0
        return self.rotate_dir


# 宽高比超过这个值才算"横跨页"。1.15 能吃掉 1.5:1 和 2:1 的跨页，
# 但不会把接近正方形的页（1200×1100）误转。
SPREAD_RATIO = 1.15


def plan_image(w: int, h: int, target: "Target", opts: Options):
    """返回 (tw, th, rot)。横跨页先交换宽高（视同已旋转），再按目标框等比缩放。"""
    rot = opts.rot_of(w, h, target.w, target.h)
    if rot:
        w, h = h, w
    tw, th = target.plan(w, h)
    return tw, th, rot


# ---------------------------------------------------------------------------
# 缩放
# ---------------------------------------------------------------------------

_ffmpeg_locks = threading.local()


def resize_with_ffmpeg(ffmpeg: str, src: str, dst: str, tw: int, th: int,
                       opts: Options, fmt: str, rot: int = 0) -> None:
    parts = []
    if rot == 1:
        parts.append("transpose=1")      # 顺时针 90°
    elif rot == 2:
        parts.append("transpose=2")      # 逆时针 90°
    parts.append("scale=%d:%d:flags=%s" % (tw, th, opts.filter))
    vf = ",".join(parts)
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", src, "-vf", vf,
           "-frames:v", "1", "-map_metadata", "-1"]
    if fmt == "PNG":
        cmd += ["-c:v", "png", "-compression_level", "6"]
    elif fmt == "WEBP":
        cmd += ["-c:v", "libwebp", "-quality", "92"]
    elif fmt == "GIF":
        cmd += ["-c:v", "gif"]
    else:
        cmd += ["-c:v", "mjpeg", "-q:v", str(opts.quality),
                "-pix_fmt", "yuvj420p", "-huffman", "optimal"]
    cmd.append(dst)
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "ignore").strip()[:300])


def resize_with_pillow(src: str, dst: str, tw: int, th: int, fmt: str,
                       quality: int = 95, rot: int = 0) -> None:
    from PIL import Image
    with Image.open(src) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        if rot == 1:
            im = im.transpose(Image.ROTATE_270)   # ROTATE_270 = 顺时针 90°
        elif rot == 2:
            im = im.transpose(Image.ROTATE_90)    # ROTATE_90 = 逆时针 90°
        resample = Image.LANCZOS
        out = im.resize((tw, th), resample)
        if fmt == "PNG":
            out.save(dst, "PNG", optimize=True)
        elif fmt == "WEBP":
            out.save(dst, "WEBP", quality=92, method=4)
        else:
            if out.mode not in ("RGB", "L"):
                out = out.convert("RGB")
            out.save(dst, "JPEG", quality=quality, optimize=True,
                     subsampling=2, progressive=True)


def resize_image(ffmpeg, src: str, dst: str, tw: int, th: int,
                 opts: Options, rot: int = 0) -> None:
    fmt = image_format(src) or "JPEG"
    if ffmpeg:
        try:
            resize_with_ffmpeg(ffmpeg, src, dst, tw, th, opts, fmt, rot)
            # ffmpeg 输出 PNG 时可能变大很多；不干预，保证质量
            return
        except Exception:
            if os.path.exists(dst):
                try:
                    os.remove(dst)
                except Exception:
                    pass
    resize_with_pillow(src, dst, tw, th, fmt, rot=rot)


# ---------------------------------------------------------------------------
# 并发执行一组图片
# ---------------------------------------------------------------------------

def process_images(jobs, opts: Options, log, on_done=None):
    """jobs: [(src, dst, tw, th, rot), ...]；src 与 dst 可相同（原地替换）。
    rot: 0 不转 / 1 顺时针 90° / 2 逆时针 90°，在缩放之前执行。"""
    total = len(jobs)
    done = 0
    failed = 0
    lock = threading.Lock()

    def work(job):
        nonlocal done, failed
        src, dst, tw, th = job[0], job[1], job[2], job[3]
        rot = job[4] if len(job) > 4 else 0
        same = os.path.abspath(src) == os.path.abspath(dst)
        tmp = None
        try:
            if same:
                tmp = src + ".mrz_tmp"
                dst = tmp
            resize_image(opts.ffmpeg, src, dst, tw, th, opts, rot)
            if same:
                os.replace(tmp, src)
                tmp = None
            with lock:
                done += 1
                if on_done:
                    on_done(done, total)
        except Exception as e:
            with lock:
                failed += 1
                if on_done:
                    on_done(done + failed, total)
            log("      ! 跳过 %s：%s" % (os.path.basename(src), e), "warn")
        finally:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    if not jobs:
        return 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=opts.workers) as ex:
        list(ex.map(work, jobs))
    return done, failed


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------

def _new_info(path):
    return {
        "file": path,
        "name": os.path.basename(path),
        "ext": os.path.splitext(path)[1].lower(),
        "size_mb": round(os.path.getsize(path) / 1048576.0, 1) if os.path.exists(path) else 0.0,
        "n_img": 0,
        "h_med": 0, "h_min": 0, "h_max": 0,
        "w_med": 0,
        "chars": 0,
        "kind": "",
        "err": "",
        "note": "",
    }


def verdict_of(info, target: Target) -> str:
    if info.get("kind") == "text":
        return "文字版"
    if info["n_img"] == 0:
        return "无图片"
    w_med = info.get("w_med") or 0
    h_med = info["h_med"]
    if w_med <= 0 or h_med <= 0:
        return ""
    tw, th = target.plan(w_med, h_med)
    if abs(tw - w_med) <= POINT_TOLERANCE and abs(th - h_med) <= POINT_TOLERANCE:
        return "点对点"
    return "需放大" if tw > w_med else "需缩小"


def scan_epub(path):
    info = _new_info(path)
    hs, ws, chars = [], [], 0
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            low = n.lower()
            if low.endswith(IMG_EXT):
                try:
                    s = img_size_bytes(z.read(n)[:65536])
                except Exception:
                    s = None
                if s:
                    ws.append(s[0])
                    hs.append(s[1])
            elif low.endswith((".html", ".xhtml", ".htm", ".opf", ".ncx")):
                try:
                    t = z.read(n).decode("utf-8", "ignore")
                except Exception:
                    continue
                if low.endswith((".html", ".xhtml", ".htm")):
                    chars += len(re.sub(r"\s", "", re.sub(r"<[^>]+>", "", t)))
    if not hs:
        info["err"] = "包内没有图片"
        return info
    hs.sort()
    ws.sort()
    info["n_img"] = len(hs)
    info["h_med"] = hs[len(hs) // 2]
    info["h_min"], info["h_max"] = hs[0], hs[-1]
    info["w_med"] = ws[len(ws) // 2]
    info["chars"] = chars
    info["kind"] = "text" if chars > TEXT_LIMIT else "comic"
    return info


def scan_zip(path):
    info = _new_info(path)
    hs, ws = [], []
    with zipfile.ZipFile(path) as z:
        for n in z.namelist():
            if n.lower().endswith(IMG_EXT):
                try:
                    s = img_size_bytes(z.read(n)[:65536])
                except Exception:
                    s = None
                if s:
                    ws.append(s[0])
                    hs.append(s[1])
    if not hs:
        info["err"] = "包内没有图片"
        return info
    hs.sort()
    ws.sort()
    info["n_img"] = len(hs)
    info["h_med"] = hs[len(hs) // 2]
    info["h_min"], info["h_max"] = hs[0], hs[-1]
    info["w_med"] = ws[len(ws) // 2]
    info["kind"] = "comic"
    return info




def scan_kf8(path):
    """只按偏移读记录头，不解包文本也不是整文件读入，大文件也快。"""
    info = _new_info(path)
    fsize = os.path.getsize(path)
    try:
        f = open(path, "rb")
    except Exception as e:
        info["err"] = str(e)
        return info
    try:
        head = f.read(1 << 20)
        if len(head) < 86:
            info["err"] = "文件太小，不是有效的 MOBI/AZW3"
            return info
        n = _be16(head, 76)
        if n <= 0 or n > 200000:
            info["err"] = "PDB 记录表异常"
            return info
        need = 78 + n * 8
        if need > len(head):
            head = b""
            f.seek(0)
            head = f.read(need)
            if len(head) < need:
                info["err"] = "PDB 记录表不完整"
                return info
        offs = [_be32(head, 78 + i * 8) for i in range(n)] + [fsize]

        # record 0 里的 MOBI header 给出图片起始记录号
        first_image = 1
        f.seek(offs[0])
        rec0 = f.read(min(4096, max(0, offs[1] - offs[0])))
        if rec0[16:20] == b"MOBI":
            try:
                v = _be32(rec0, 16 + 0x6C)
                if 0 < v < n:
                    first_image = v
            except Exception:
                pass

        hs, ws = [], []
        for i in range(first_image, n):
            a, b = offs[i], offs[i + 1]
            if a <= 0 or b <= a or b > fsize:
                continue
            f.seek(a)
            chunk = f.read(min(65536, b - a))
            if not chunk:
                continue
            if not (chunk[:3] == b"\xff\xd8\xff" or chunk[:4] == b"\x89PNG"
                    or chunk[:3] == b"GIF"):
                continue
            s = img_size_bytes(chunk)
            if s:
                ws.append(s[0])
                hs.append(s[1])
    except Exception as e:
        info["err"] = "PDB 解析失败：%s" % e
        return info
    finally:
        f.close()

    if not hs:
        info["err"] = "未找到图片记录（可能是纯文字书）"
        return info
    hs.sort()
    ws.sort()
    info["n_img"] = len(hs)
    info["h_med"] = hs[len(hs) // 2]
    info["h_min"], info["h_max"] = hs[0], hs[-1]
    info["w_med"] = ws[len(ws) // 2]
    info["kind"] = "comic"
    info["note"] = "MOBI/KF8，处理时经 Calibre 转 EPUB 往返"
    return info


def _pdf_module():
    try:
        import pypdfium2 as pdfium
        return ("pdfium", pdfium)
    except Exception:
        pass
    try:
        import fitz
        return ("fitz", fitz)
    except Exception:
        return (None, None)


def scan_pdf(path):
    info = _new_info(path)
    kind, mod = _pdf_module()
    if not kind:
        info["err"] = "缺少 PDF 组件（pypdfium2 / pymupdf）"
        return info
    try:
        if kind == "pdfium":
            doc = mod.PdfDocument(path)
            n = len(doc)
            hs, ws = [], []
            for i in range(min(n, 60)):
                page = doc[i]
                wpt, hpt = page.get_size()
                # 尝试读页面内单张原图的分辨率
                got = None
                try:
                    objs = list(page.get_objects(filter=[mod.raw.FPDF_PAGEOBJ_IMAGE]))
                    if len(objs) == 1:
                        got = tuple(objs[0].get_px_size())
                except Exception:
                    got = None
                if got:
                    ws.append(got[0])
                    hs.append(got[1])
                else:
                    # 无原图可读时按 150dpi 估算
                    ws.append(int(wpt / 72.0 * 150))
                    hs.append(int(hpt / 72.0 * 150))
            doc.close()
        else:
            doc = mod.open(path)
            n = doc.page_count
            hs, ws = [], []
            for i in range(min(n, 60)):
                page = doc[i]
                r = page.rect
                got = None
                try:
                    imgs = page.get_images(full=True)
                    if len(imgs) == 1:
                        px = doc.extract_image(imgs[0][0])
                        got = (px["width"], px["height"])
                except Exception:
                    got = None
                if got:
                    ws.append(got[0])
                    hs.append(got[1])
                else:
                    ws.append(int(r.width / 72.0 * 150))
                    hs.append(int(r.height / 72.0 * 150))
            doc.close()
    except Exception as e:
        info["err"] = "PDF 读取失败：%s" % e
        return info

    if not hs:
        info["err"] = "PDF 没有页面"
        return info
    hs.sort()
    ws.sort()
    info["n_img"] = n
    info["h_med"] = hs[len(hs) // 2]
    info["h_min"], info["h_max"] = hs[0], hs[-1]
    info["w_med"] = ws[len(ws) // 2]
    info["kind"] = "comic"
    info["note"] = "PDF 共 %d 页；有原图可提取时按原图，否则按渲染" % n
    return info


def scan(path: str, target: Target | None = None):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in EXT_EPUB:
            info = scan_epub(path)
        elif ext in EXT_ZIP:
            info = scan_zip(path)
        elif ext in EXT_PDF:
            info = scan_pdf(path)
        elif ext in EXT_KF8:
            info = scan_kf8(path)
        else:
            info = _new_info(path)
            info["err"] = "不支持的格式"
    except Exception as e:
        info = _new_info(path)
        info["err"] = str(e)[:120]
    if target is not None:
        info["verdict"] = verdict_of(info, target) if not info["err"] else "读取失败"
    else:
        info["verdict"] = "文字版" if info.get("kind") == "text" else ""
    return info


# ---------------------------------------------------------------------------
# EPUB 内部改写
# ---------------------------------------------------------------------------

_VIEWPORT_RE = re.compile(
    r'(<meta[^>]*name\s*=\s*["\']viewport["\'][^>]*content\s*=\s*["\'])([^"\']*)(["\'])',
    re.I)
_W_H_RE = re.compile(r'(width\s*=\s*)(\d+)(\s*,\s*height\s*=\s*)(\d+)', re.I)

CSS_OVERRIDE = (
    "<style type=\"text/css\">/*mrz*/\n"
    "html,body{margin:0!important;padding:0!important;height:100%;}\n"
    "img,svg{max-width:100%!important;max-height:100%!important;}\n"
    "img{width:auto!important;height:auto!important;display:block;margin:0 auto;}\n"
    "svg{width:100%!important;height:100%!important;display:block;}\n"
    "</style>"
)


def _rel_key(p: str) -> str:
    return os.path.normpath(p).replace("\\", "/").lower()


def rewrite_html(path: str, target: Target, size_map: dict, log,
                 root: str | None = None) -> bool:
    try:
        t = open(path, encoding="utf-8").read()
    except UnicodeDecodeError:
        try:
            t = open(path, encoding="gb18030", errors="ignore").read()
        except Exception:
            return False
    except Exception:
        return False

    orig = t

    # 1) CSS 里的 max-width / max-height
    t = re.sub(r"max-width\s*:\s*\d+(?:\.\d+)?px",
               "max-width:%dpx" % target.w, t, flags=re.I)
    t = re.sub(r"max-height\s*:\s*\d+(?:\.\d+)?px",
               "max-height:%dpx" % target.h, t, flags=re.I)
    t = re.sub(r'"max-width"\s*:\s*"\d+(?:\.\d+)?px"',
               '"max-width":"%dpx"' % target.w, t, flags=re.I)
    t = re.sub(r'"max-height"\s*:\s*"\d+(?:\.\d+)?px"',
               '"max-height":"%dpx"' % target.h, t, flags=re.I)

    # 2) viewport
    def _vp(m):
        return m.group(1) + "width=%d, height=%d" % (target.w, target.h) + m.group(3)
    t = _VIEWPORT_RE.sub(_vp, t)
    t = _W_H_RE.sub(lambda m: m.group(1) + str(target.w) + m.group(3) + str(target.h), t)

    # 3) svg viewBox —— 日本轻小说/漫画常用 svg 包整页图，不改 viewBox 等于白做
    base = os.path.dirname(path)
    base_index = {}
    for k, v in size_map.items():
        base_index.setdefault(k.rsplit("/", 1)[-1], v)

    def _lookup(href):
        h = href.replace("%20", " ").split("#")[0].split("?")[0]
        if root:
            rel = _rel_key(os.path.relpath(os.path.join(base, h), root))
            if rel in size_map:
                return size_map[rel]
        rel = _rel_key(os.path.join(base, h))
        if rel in size_map:
            return size_map[rel]
        return base_index.get(rel.rsplit("/", 1)[-1])

    def _fix_svg(m):
        block = m.group(0)
        hrefs = re.findall(r'(?:xlink:href|href)\s*=\s*["\']([^"\']+)["\']', block, re.I)
        size = None
        for h in hrefs:
            if h.lower().startswith(("http:", "https:", "data:")):
                continue
            size = _lookup(h)
            if size:
                break
        if not size:
            return block

        # viewBox 改成新像素尺寸
        vb = "0 0 %d %d" % (size[0], size[1])
        if re.search(r'viewBox\s*=', block, re.I):
            block = re.sub(r'(viewBox\s*=\s*["\'])[^"\']*(["\'])',
                           lambda mm: mm.group(1) + vb + mm.group(2),
                           block, count=1, flags=re.I)
        else:
            block = re.sub(r"<svg", '<svg viewBox="%s"' % vb, block, count=1, flags=re.I)

        # <image> 的 width/height 必须跟着改，否则图只占 viewBox 的一角
        def _fix_img_tag(mm):
            tag = mm.group(0)
            if not re.search(r'(?:xlink:href|href)\s*=', tag, re.I):
                return tag
            tag = re.sub(r'\bwidth\s*=\s*["\']\d+(?:\.\d+)?["\']',
                         'width="%d"' % size[0], tag, count=1, flags=re.I)
            tag = re.sub(r'\bheight\s*=\s*["\']\d+(?:\.\d+)?["\']',
                         'height="%d"' % size[1], tag, count=1, flags=re.I)
            return tag

        block = re.sub(r"<image\b[^>]*>", _fix_img_tag, block, flags=re.I)
        return block

    t = re.sub(r"<svg\b.*?</svg>", _fix_svg, t, flags=re.I | re.S)

    # 4) 追加覆盖样式，处理没有尺寸声明的情况
    if "/*mrz*/" not in t:
        if re.search(r"</head\s*>", t, re.I):
            t = re.sub(r"</head\s*>", CSS_OVERRIDE + "</head>", t, count=1, flags=re.I)
        elif re.search(r"<body", t, re.I):
            t = re.sub(r"(<body[^>]*>)", r"\1" + CSS_OVERRIDE, t, count=1, flags=re.I)

    if t != orig:
        try:
            open(path, "w", encoding="utf-8", newline="").write(t)
        except Exception as e:
            log("      ! 写入失败 %s：%s" % (os.path.basename(path), e), "warn")
            return False
        return True
    return False


def rewrite_css(path: str, target: Target) -> bool:
    try:
        t = open(path, encoding="utf-8").read()
    except Exception:
        return False
    o = t
    t = re.sub(r"max-width\s*:\s*\d+(?:\.\d+)?px", "max-width:%dpx" % target.w, t, flags=re.I)
    t = re.sub(r"max-height\s*:\s*\d+(?:\.\d+)?px", "max-height:%dpx" % target.h, t, flags=re.I)
    if t != o:
        try:
            open(path, "w", encoding="utf-8", newline="").write(t)
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# 打包
# ---------------------------------------------------------------------------

def _commit(tmp: str, dst: str) -> None:
    """把临时文件原子替换成目标。

    比"先删再写"安全：目标被占用时替换失败，旧文件原样保留，不会两头空。
    """
    try:
        os.replace(tmp, dst)
    except Exception as e:
        try:
            os.remove(tmp)
        except Exception:
            pass
        raise RuntimeError(
            "无法覆盖「%s」：文件可能正被阅读器、预览窗格或其他程序占用，"
            "关掉后重试（%s）" % (os.path.basename(dst), e))


def replace_output(dst: str) -> None:
    """删除已存在的输出文件。

    被占用时重试几次 —— Windows 上阅读器/杀软短暂持有句柄很常见。
    仍然失败就抛出人话错误，而不是让上层看到光秃秃的 PermissionError。
    """
    if not os.path.exists(dst):
        return
    last = None
    for _ in range(4):
        try:
            os.remove(dst)
            return
        except Exception as e:
            last = e
            time.sleep(0.25)
    raise RuntimeError(
        "无法覆盖「%s」：文件可能正被阅读器、预览窗格或其他程序占用，"
        "关掉后重试（%s）" % (os.path.basename(dst), last))


def pack_epub(work: str, dst: str) -> None:
    tmp = dst + ".mrz_tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        mi = zipfile.ZipInfo("mimetype")
        mi.compress_type = zipfile.ZIP_STORED
        z.writestr(mi, "application/epub+zip")
        for root, dirs, files in os.walk(work):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, work).replace("\\", "/")
                if rel == "mimetype":
                    continue
                z.write(p, rel, compress_type=zipfile.ZIP_DEFLATED)
    _commit(tmp, dst)


def pack_zip(work: str, dst: str, keep_order=None) -> None:
    tmp = dst + ".mrz_tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as z:
        for root, dirs, files in os.walk(work):
            dirs.sort()
            for f in sorted(files):
                p = os.path.join(root, f)
                rel = os.path.relpath(p, work).replace("\\", "/")
                z.write(p, rel, compress_type=zipfile.ZIP_STORED)
    _commit(tmp, dst)


# ---------------------------------------------------------------------------
# 各格式处理
# ---------------------------------------------------------------------------

class Result:
    def __init__(self, ok=False, msg="", done=0, skipped=0, size_mb=0.0):
        self.ok = ok
        self.msg = msg
        self.done = done
        self.skipped = skipped
        self.size_mb = size_mb


def _collect_images(root: str):
    out = []
    for dp, dirs, files in os.walk(root):
        for f in files:
            if f.lower().endswith(IMG_EXT):
                out.append(os.path.join(dp, f))
    out.sort()
    return out


def process_epub(src: str, dst: str, target: Target, opts: Options,
                 log, progress=None, text_guard=True) -> Result:
    work = tempfile.mkdtemp(prefix="mrz_epub_")
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(work)

        if text_guard:
            chars = 0
            for dp, _, fs in os.walk(work):
                for f in fs:
                    if f.lower().endswith((".html", ".xhtml", ".htm")):
                        try:
                            t = open(os.path.join(dp, f), encoding="utf-8",
                                     errors="ignore").read()
                        except Exception:
                            continue
                        chars += len(re.sub(r"\s", "", re.sub(r"<[^>]+>", "", t)))
                        if chars > TEXT_LIMIT:
                            break
                if chars > TEXT_LIMIT:
                    break
            if chars > TEXT_LIMIT:
                return Result(False, "文字版（正文约 %d 字），已跳过" % chars)

        imgs = _collect_images(work)
        if not imgs:
            return Result(False, "包内没有图片")

        jobs = []
        size_map = {}
        skipped = 0
        rotated = 0
        for p in imgs:
            wh = img_size_file(p)
            if not wh:
                skipped += 1
                continue
            w, h = wh
            tw, th, rot = plan_image(w, h, target, opts)
            size_map[_rel_key(os.path.relpath(p, work))] = (tw, th)
            if rot == 0 and tw == w and th == h:
                skipped += 1
                continue
            if rot:
                rotated += 1
            jobs.append((p, p, tw, th, rot))

        log("      图片 %d 张，需处理 %d 张%s" % (
            len(imgs), len(jobs), "，其中横跨页旋转 %d 张" % rotated if rotated else ""))
        if jobs:
            process_images(jobs, opts, log, progress)

        # 尺寸声明同步
        changed = 0
        for dp, _, fs in os.walk(work):
            for f in fs:
                low = f.lower()
                p = os.path.join(dp, f)
                if low.endswith((".html", ".xhtml", ".htm")):
                    if rewrite_html(p, target, size_map, log, root=work):
                        changed += 1
                elif low.endswith(".css"):
                    if rewrite_css(p, target):
                        changed += 1
        log("      改写 %d 个样式/页面文件" % changed)

        pack_epub(work, dst)
        return Result(True, "完成", len(jobs), skipped,
                      round(os.path.getsize(dst) / 1048576.0, 1))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_zip(src: str, dst: str, target: Target, opts: Options,
                log, progress=None) -> Result:
    work = tempfile.mkdtemp(prefix="mrz_zip_")
    try:
        with zipfile.ZipFile(src) as z:
            z.extractall(work)
        imgs = _collect_images(work)
        if not imgs:
            return Result(False, "包内没有图片")

        jobs = []
        skipped = 0
        rotated = 0
        for p in imgs:
            wh = img_size_file(p)
            if not wh:
                skipped += 1
                continue
            w, h = wh
            tw, th, rot = plan_image(w, h, target, opts)
            if rot == 0 and tw == w and th == h:
                skipped += 1
                continue
            if rot:
                rotated += 1
            jobs.append((p, p, tw, th, rot))
        log("      图片 %d 张，需处理 %d 张%s" % (
            len(imgs), len(jobs), "，其中横跨页旋转 %d 张" % rotated if rotated else ""))
        process_images(jobs, opts, log, progress)
        pack_zip(work, dst)
        return Result(True, "完成", len(jobs), skipped,
                      round(os.path.getsize(dst) / 1048576.0, 1))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_pdf(src: str, dst: str, target: Target, opts: Options,
                log, progress=None) -> Result:
    kind, mod = _pdf_module()
    if not kind:
        return Result(False, "缺少 PDF 组件")

    from PIL import Image

    tmpdir = tempfile.mkdtemp(prefix="mrz_pdf_")
    pages = []
    try:
        if kind == "pdfium":
            doc = mod.PdfDocument(src)
            n = len(doc)
            log("      PDF 共 %d 页" % n)
            for i in range(n):
                page = doc[i]
                wpt, hpt = page.get_size()
                pil = None
                # 优先提取页面内的单张原图，避免二次重采样
                try:
                    objs = list(page.get_objects(filter=[mod.raw.FPDF_PAGEOBJ_IMAGE]))
                    if len(objs) == 1:
                        l, b, r, t = objs[0].get_bounds()
                        cov = abs((r - l) * (t - b))
                        if cov > 0.55 * wpt * hpt:
                            ex = objs[0].extract()
                            if ex is not None:
                                pil = ex if hasattr(ex, "size") else None
                except Exception:
                    pil = None
                if pil is None:
                    scale = min(target.w / float(wpt), target.h / float(hpt))
                    bmp = page.render(scale=scale)
                    pil = bmp.to_pil()
                if pil.mode not in ("RGB", "L"):
                    pil = pil.convert("RGB")
                pages.append(pil)
                if progress:
                    progress(i + 1, n)
            doc.close()
        else:
            doc = mod.open(src)
            n = doc.page_count
            log("      PDF 共 %d 页" % n)
            for i in range(n):
                page = doc[i]
                pil = None
                try:
                    imgs = page.get_images(full=True)
                    if len(imgs) == 1:
                        px = doc.extract_image(imgs[0][0])
                        import io
                        pil = Image.open(io.BytesIO(px["image"])).convert("RGB")
                        r = page.rect
                        if pil.width * pil.height < 0.25 * r.width * r.height:
                            pil = None
                except Exception:
                    pil = None
                if pil is None:
                    pr = page.rect
                    zoom = min(target.w / float(pr.width), target.h / float(pr.height))
                    pix = page.get_pixmap(matrix=mod.Matrix(zoom, zoom), alpha=False)
                    pil = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                pages.append(pil)
                if progress:
                    progress(i + 1, n)
            doc.close()
    except Exception as e:
        if kind == "pdfium":
            try:
                doc.close()
            except Exception:
                pass
        shutil.rmtree(tmpdir, ignore_errors=True)
        return Result(False, "PDF 解析失败：%s" % e)

    if not pages:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return Result(False, "PDF 没有可渲染页面")

    # 统一缩放到目标
    out_pages = []
    jobs = []
    idx = 0
    rotated = 0
    for pil in pages:
        w, h = pil.size
        tw, th, rot = plan_image(w, h, target, opts)
        fp = os.path.join(tmpdir, "p%05d.png" % idx)
        pil.save(fp, "PNG")
        idx += 1
        if rot:
            rotated += 1
        if rot or tw != w or th != h:
            jobs.append((fp, fp, tw, th, rot))
        out_pages.append(fp)
    if rotated:
        log("      其中横跨页旋转 %d 页" % rotated)
    if jobs:
        process_images(jobs, opts, log)

    try:
        loaded = [Image.open(p).convert("RGB") for p in out_pages]
        first = loaded[0]
        tmp_pdf = dst + ".mrz_tmp.pdf"
        if os.path.exists(tmp_pdf):
            os.remove(tmp_pdf)
        first.save(tmp_pdf, "PDF", resolution=72.0, save_all=True,
                   append_images=loaded[1:], quality=max(60, 96 - opts.quality * 4))
        _commit(tmp_pdf, dst)
        for im in loaded:
            im.close()
    except Exception as e:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return Result(False, "PDF 写出失败：%s" % e)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return Result(True, "完成（%d 页）" % len(out_pages), len(out_pages), 0,
                  round(os.path.getsize(dst) / 1048576.0, 1))


def _run(cmd, log, timeout=1800):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       timeout=timeout,
                       creationflags=0x08000000 if os.name == "nt" else 0)
    if r.returncode != 0:
        msg = (r.stderr or b"").decode("utf-8", "ignore").strip()
        if not msg:
            msg = (r.stdout or b"").decode("utf-8", "ignore").strip()
        raise RuntimeError(msg[-400:] or ("exit %d" % r.returncode))


def process_kf8(src: str, dst: str, target: Target, opts: Options,
                log, progress=None) -> Result:
    calibre = opts.calibre or find_calibre()
    if not calibre:
        return Result(False, "需要 Calibre：请在设置里指定 ebook-convert.exe，"
                             "或先手动转成 EPUB 再处理")

    work = tempfile.mkdtemp(prefix="mrz_kf8_")
    try:
        mid = os.path.join(work, "in.epub")
        log("      Calibre 转换成 EPUB …")
        _run([calibre, src, mid, "--no-default-epub-cover"], log)
        if not os.path.exists(mid):
            return Result(False, "Calibre 转换未产出文件")

        out_epub = os.path.join(work, "out.epub")
        r = process_epub(mid, out_epub, target, opts, log, progress)
        if not r.ok:
            return r

        ext = os.path.splitext(dst)[1].lower()
        log("      Calibre 转回 %s …" % ext.lstrip(".").upper())
        # 先写临时文件再替换，原地覆盖源文件时不会中途丢原件
        tmp_out = os.path.splitext(dst)[0] + ".mrz_tmp" + ext
        if os.path.exists(tmp_out):
            os.remove(tmp_out)
        _run([calibre, out_epub, tmp_out], log)
        if not os.path.exists(tmp_out):
            return Result(False, "Calibre 转回失败")
        _commit(tmp_out, dst)
        return Result(True, "完成（经 Calibre 往返）", r.done, r.skipped,
                      round(os.path.getsize(dst) / 1048576.0, 1))
    except subprocess.TimeoutExpired:
        return Result(False, "Calibre 转换超时")
    except Exception as e:
        return Result(False, str(e)[:200])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_file(src: str, dst: str, target: Target, opts: Options,
                 log, progress=None) -> Result:
    ext = os.path.splitext(src)[1].lower()
    if ext in EXT_EPUB:
        return process_epub(src, dst, target, opts, log, progress)
    if ext in EXT_ZIP:
        return process_zip(src, dst, target, opts, log, progress)
    if ext in EXT_PDF:
        return process_pdf(src, dst, target, opts, log, progress)
    if ext in EXT_KF8:
        return process_kf8(src, dst, target, opts, log, progress)
    return Result(False, "不支持的格式：%s" % ext)


# ---------------------------------------------------------------------------
# 文件收集
# ---------------------------------------------------------------------------

def collect(paths, recursive=False):
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isfile(p):
            if p.lower().endswith(SUPPORTED_EXT):
                out.append(p)
        elif os.path.isdir(p):
            if recursive:
                for root, _, fs in os.walk(p):
                    for f in fs:
                        if f.lower().endswith(SUPPORTED_EXT):
                            out.append(os.path.abspath(os.path.join(root, f)))
            else:
                for f in os.listdir(p):
                    fp = os.path.join(p, f)
                    if os.path.isfile(fp) and f.lower().endswith(SUPPORTED_EXT):
                        out.append(os.path.abspath(fp))
    seen = set()
    uniq = []
    for f in out:
        k = f.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return sorted(uniq)


def out_name(src: str, target: Target) -> str:
    base = os.path.splitext(os.path.basename(src))[0]
    ext = os.path.splitext(src)[1]
    return base + target.suffix() + ext
