# -*- coding: utf-8 -*-
"""
墨水屏阅读器漫画优化 —— GUI 入口。

直接运行本文件即可：python manga_resize.py
"""
from __future__ import annotations

import json
import os
import queue
import sys
import threading
import traceback
import webbrowser

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import resize_core as core
import readers_db

APP_NAME = "墨水屏阅读器漫画优化"
APP_VER = "1.0"
CALIBRE_URL = "https://calibre-ebook.com/download_windows"
KF8_EXT = (".mobi", ".azw3", ".azw", ".prc")

# ---------------- 配色 ----------------
BG = "#eef1f6"
CARD = "#ffffff"
LINE = "#d8dee9"
TEXT = "#1f2933"
MUTED = "#6b7a8d"
ACCENT = "#2563eb"
ACCENT_D = "#1d4ed8"
OKC = "#15803d"
WARNC = "#b45309"
ERRC = "#b91c1c"
ROW_ALT = "#f7f9fc"


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "MangaResize")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "config.json")


DEFAULT_CFG = {
    "mode": "auto",                 # auto | manual
    "brand": "掌阅 iReader",
    "model": "Ocean 3 Plus",
    "width": 1440,
    "height": 1920,
    "filter": "spline",
    "quality": 2,
    "out_mode": "same",             # same | custom
    "out_dir": "",
    "recursive": False,
    "overwrite": False,
    "rotate_spread": False,
    "rotate_dir": 1,
    "ffmpeg": "",
    "calibre": "",
    "last_dir": "",
    "geom": "",
    "custom_devices": {},           # {brand: [[model,w,h,inch,ppi,note], ...]}
}


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        with open(config_path(), encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 阅读器数据（内置 + 自定义）
# ---------------------------------------------------------------------------

def build_catalog(custom: dict) -> dict:
    cat = {}
    for brand, devs in readers_db.BRANDS.items():
        cat[brand] = [list(d) for d in devs]
    for brand, devs in (custom or {}).items():
        if not isinstance(devs, list):
            continue
        cat.setdefault(brand, [])
        names = {d[0] for d in cat[brand]}
        for d in devs:
            try:
                item = [str(d[0]), int(d[1]), int(d[2]),
                        float(d[3]) if len(d) > 3 and d[3] else 0.0,
                        int(d[4]) if len(d) > 4 and d[4] else 0,
                        str(d[5]) if len(d) > 5 and d[5] else ""]
            except Exception:
                continue
            if item[0] in names:
                for i, e in enumerate(cat[brand]):
                    if e[0] == item[0]:
                        cat[brand][i] = item
                        break
            else:
                cat[brand].append(item)
                names.add(item[0])
    return cat


# ---------------------------------------------------------------------------
# 主窗口
# ---------------------------------------------------------------------------

def _make_root():
    try:
        from tkinterdnd2 import TkinterDnD
        return TkinterDnD.Tk(), True
    except Exception:
        return tk.Tk(), False


class App:
    def __init__(self, root, dnd_ok: bool):
        self.root = root
        self.dnd_ok = dnd_ok
        self.cfg = load_cfg()
        self.catalog = build_catalog(self.cfg.get("custom_devices"))
        self.files = []            # 绝对路径列表
        self.infos = {}            # path -> scan info
        self.q = queue.Queue()
        self.worker = None
        self.stop_flag = threading.Event()
        self.busy = False
        self._save_job = None
        self._calibre_warned = False
        self._dirty = set()

        self.ffmpeg = core.find_ffmpeg(self.cfg.get("ffmpeg") or None)
        self.calibre = core.find_calibre(self.cfg.get("calibre") or None)

        self._init_style()
        self._build()
        self._load_cfg_to_ui()
        self._apply_geom()
        self._poll()

        self.log("%s v%s 就绪" % (APP_NAME, APP_VER), "muted")
        self._report_env()

    # ---------------- 样式 ----------------
    def _init_style(self):
        r = self.root
        r.title("%s  v%s" % (APP_NAME, APP_VER))
        r.configure(bg=BG)
        r.minsize(980, 680)
        try:
            r.iconbitmap(default=self._res("assets/app.ico"))
        except Exception:
            pass

        st = ttk.Style(r)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", background=BG, foreground=TEXT,
                     font=("Microsoft YaHei UI", 9))
        st.configure("Card.TFrame", background=CARD, relief="flat")
        st.configure("Bg.TFrame", background=BG)
        st.configure("TLabel", background=BG, foreground=TEXT)
        st.configure("Card.TLabel", background=CARD, foreground=TEXT)
        st.configure("H1.TLabel", background=BG, foreground=TEXT,
                     font=("Microsoft YaHei UI", 14, "bold"))
        st.configure("Sub.TLabel", background=BG, foreground=MUTED,
                     font=("Microsoft YaHei UI", 8))
        st.configure("Muted.TLabel", background=CARD, foreground=MUTED,
                     font=("Microsoft YaHei UI", 8))
        st.configure("Val.TLabel", background=CARD, foreground=ACCENT,
                     font=("Microsoft YaHei UI", 12, "bold"))
        st.configure("Hint.TLabel", background=CARD, foreground=MUTED,
                     font=("Microsoft YaHei UI", 8))

        st.configure("TButton", padding=(10, 5), background="#f2f4f8",
                     relief="flat", borderwidth=1)
        st.map("TButton",
               background=[("active", "#e6ebf3"), ("disabled", "#f0f2f5")],
               foreground=[("disabled", "#a5b0bf")])
        st.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                     font=("Microsoft YaHei UI", 9, "bold"), padding=(14, 7))
        st.map("Accent.TButton",
               background=[("active", ACCENT_D), ("disabled", "#9db8f0")],
               foreground=[("disabled", "#ffffff")])
        st.configure("Ghost.TButton", background=CARD, relief="flat")

        st.configure("TEntry", fieldbackground="#ffffff", bordercolor=LINE,
                     lightcolor=LINE, darkcolor=LINE, padding=4)
        st.configure("TCombobox", fieldbackground="#ffffff", background="#ffffff",
                     bordercolor=LINE, arrowcolor=MUTED, padding=3)
        st.map("TCombobox", fieldbackground=[("readonly", "#ffffff")])
        st.configure("TSpinbox", fieldbackground="#ffffff", bordercolor=LINE,
                     padding=3, arrowsize=12)
        st.configure("TRadiobutton", background=CARD, foreground=TEXT)
        st.map("TRadiobutton", background=[("active", CARD)])
        st.configure("TCheckbutton", background=CARD, foreground=TEXT)
        st.map("TCheckbutton", background=[("active", CARD)])
        st.configure("TNotebook", background=BG, borderwidth=0)
        st.configure("TNotebook.Tab", padding=(14, 6))

        st.configure("Treeview", background=CARD, fieldbackground=CARD,
                     foreground=TEXT, rowheight=25, borderwidth=0)
        st.configure("Treeview.Heading", background="#f2f4f8",
                     foreground=MUTED, relief="flat",
                     font=("Microsoft YaHei UI", 8, "bold"))
        st.map("Treeview", background=[("selected", "#dbeafe")],
               foreground=[("selected", TEXT)])
        st.configure("TProgressbar", background=ACCENT, troughcolor="#e2e8f0",
                     borderwidth=0, thickness=6)

    def _res(self, name):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base, name)

    # ---------------- 布局 ----------------
    def _build(self):
        r = self.root
        r.columnconfigure(0, weight=1)
        r.rowconfigure(1, weight=1)
        r.rowconfigure(2, weight=0)

        # 顶栏
        head = ttk.Frame(r, style="Bg.TFrame")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 4))
        head.columnconfigure(2, weight=1)

        self._hdr_img = None
        logo = tk.Label(head, bg=BG, bd=0)
        try:
            self._hdr_img = tk.PhotoImage(file=self._res("assets/app_header.png"))
            logo.configure(image=self._hdr_img)
        except Exception:
            logo.configure(text="◆", bg=ACCENT, fg="#ffffff",
                           font=("Microsoft YaHei UI", 18, "bold"), width=2, height=1)
        logo.grid(row=0, column=0, rowspan=2, padx=(0, 12))

        ttk.Label(head, text=APP_NAME, style="H1.TLabel").grid(
            row=0, column=1, sticky="w")
        self.env_lbl = ttk.Label(head, text="", style="Sub.TLabel")
        self.env_lbl.grid(row=0, column=2, sticky="e", padx=(0, 2))
        ttk.Label(head, text="按目标屏幕的物理分辨率把整页图重采样到 1:1，绕开阅读器的缩放锯齿",
                  style="Sub.TLabel").grid(row=1, column=1, columnspan=2, sticky="w",
                                           pady=(3, 0))

        # 主体：左列（列表 + 日志），右列（设置）
        body = ttk.Frame(r, style="Bg.TFrame")
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=6)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        self._build_list(body)
        self._build_settings(body)
        self._build_log(body)

        # 底部
        foot = ttk.Frame(r, style="Bg.TFrame")
        foot.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 12))
        foot.columnconfigure(0, weight=1)
        self.pbar = ttk.Progressbar(foot, mode="determinate", maximum=100)
        self.pbar.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.status = ttk.Label(foot, text="空闲", style="Sub.TLabel")
        self.status.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.btn_stop = ttk.Button(foot, text="停止", command=self.on_stop,
                                   state="disabled")
        self.btn_stop.grid(row=0, column=1, rowspan=2, padx=(0, 6))
        self.btn_scan = ttk.Button(foot, text="预演扫描", command=lambda: self.start(False))
        self.btn_scan.grid(row=0, column=2, rowspan=2, padx=(0, 6))
        self.btn_run = ttk.Button(foot, text="开始处理", style="Accent.TButton",
                                  command=lambda: self.start(True))
        self.btn_run.grid(row=0, column=3, rowspan=2)

    def _build_list(self, parent):
        wrap = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1)
        wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(0, 8))
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        bar = tk.Frame(wrap, bg=CARD)
        bar.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 6))
        ttk.Button(bar, text="添加文件", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="添加文件夹", command=self.add_folder).pack(side="left", padx=6)
        ttk.Button(bar, text="移除选中", command=self.remove_sel).pack(side="left")
        ttk.Button(bar, text="清空", command=self.clear_all).pack(side="left", padx=6)
        self.count_lbl = ttk.Label(bar, text="", style="Muted.TLabel")
        self.count_lbl.pack(side="right")

        cols = ("name", "fmt", "size", "n", "h", "verdict")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        heads = [("name", "文件", 250), ("fmt", "格式", 58), ("size", "体积", 74),
                 ("n", "图片", 56), ("h", "高度", 152), ("verdict", "状态", 84)]
        for k, t, w in heads:
            self.tree.heading(k, text=t)
            anchor = "w" if k == "name" else "center"
            self.tree.column(k, width=w, anchor=anchor,
                             stretch=(k == "name"))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set)
        self.tree.grid(row=1, column=0, sticky="nsew", padx=(10, 0))
        vs.grid(row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 8))

        self.tree.tag_configure("odd", background=CARD)
        self.tree.tag_configure("even", background="#f7f9fc")
        self.tree.tag_configure("ok", foreground=OKC)
        self.tree.tag_configure("warn", foreground=WARNC)
        self.tree.tag_configure("err", foreground=ERRC)
        self.tree.tag_configure("muted", foreground=MUTED)

        self.drop_hint = tk.Label(wrap, text="把 EPUB / MOBI / AZW3 / ZIP / CBZ / PDF 拖到这里",
                                  bg=CARD, fg=MUTED, font=("Microsoft YaHei UI", 9))
        self.drop_hint.grid(row=2, column=0, columnspan=2, pady=(0, 8))
        if not self.dnd_ok:
            self.drop_hint.configure(text="（拖放不可用，请用「添加文件」）")

    def _build_settings(self, parent):
        wrap = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1)
        wrap.grid(row=0, column=1, rowspan=2, sticky="nsew", pady=(0, 8))
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)
        wrap.configure(width=392)
        wrap.grid_propagate(False)

        shell = tk.Frame(wrap, bg=CARD)
        shell.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        # 分段式页签（替代 Notebook，选中态更明确）
        bar = tk.Frame(shell, bg="#e9eef6")
        bar.grid(row=0, column=0, sticky="ew")
        self.tab_btns = []
        for i, (title, sub) in enumerate((("目标", "屏幕 / 缩放"),
                                          ("输出", "位置 / 质量"))):
            bar.columnconfigure(i, weight=1)
            cell = tk.Frame(bar, bg="#e9eef6", cursor="hand2")
            cell.grid(row=0, column=i, sticky="ew",
                      padx=(0, 3) if i == 0 else (3, 0))
            lbl = tk.Label(cell, text=title, bg="#e9eef6", fg=MUTED,
                           font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
            lbl.pack(pady=(8, 0))
            slbl = tk.Label(cell, text=sub, bg="#e9eef6", fg="#9aa7b8",
                            font=("Microsoft YaHei UI", 7), cursor="hand2")
            slbl.pack(pady=(1, 8))
            for w in (cell, lbl, slbl):
                w.bind("<Button-1>", lambda e, idx=i: self.switch_tab(idx))
            self.tab_btns.append((cell, lbl, slbl))

        holder = tk.Frame(shell, bg=CARD)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        tab1 = tk.Frame(holder, bg=CARD)
        tab2 = tk.Frame(holder, bg=CARD)
        for f in (tab1, tab2):
            f.grid(row=0, column=0, sticky="nsew")
        self.tab_pages = [tab1, tab2]
        self.tab_index = 0

        # ---- 目标 ----
        tab1.columnconfigure(1, weight=1)

        self.mode_var = tk.StringVar(value="auto")
        rf = ttk.Frame(tab1, style="Card.TFrame")
        rf.grid(row=0, column=0, columnspan=3, sticky="w", pady=(10, 4), padx=10)
        ttk.Radiobutton(rf, text="自动匹配阅读器", value="auto",
                        variable=self.mode_var,
                        command=self.on_mode).pack(side="left")
        ttk.Radiobutton(rf, text="手动指定", value="manual",
                        variable=self.mode_var,
                        command=self.on_mode).pack(side="left", padx=(16, 0))

        self.auto_box = ttk.Frame(tab1, style="Card.TFrame")
        self.auto_box.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10)
        self.auto_box.columnconfigure(1, weight=1)

        ttk.Label(self.auto_box, text="品牌", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self.brand_var = tk.StringVar()
        self.brand_cb = ttk.Combobox(self.auto_box, textvariable=self.brand_var,
                                     state="readonly", values=readers_db.all_brands())
        self.brand_cb.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.brand_cb.bind("<<ComboboxSelected>>", self.on_brand)

        ttk.Label(self.auto_box, text="型号", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self.model_var = tk.StringVar()
        self.model_cb = ttk.Combobox(self.auto_box, textvariable=self.model_var,
                                     state="readonly", values=[])
        self.model_cb.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.model_cb.bind("<<ComboboxSelected>>", self.on_model)

        self.dev_lbl = ttk.Label(self.auto_box, text="", style="Muted.TLabel")
        self.dev_lbl.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        self.man_box = ttk.Frame(tab1, style="Card.TFrame")
        self.man_box.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10)
        self.man_box.columnconfigure(1, weight=1)
        ttk.Label(self.man_box, text="屏幕高", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        self.h_var = tk.IntVar(value=1920)
        self.h_sp = ttk.Spinbox(self.man_box, from_=200, to=6000, increment=2,
                                textvariable=self.h_var, command=self.on_manual,
                                width=10)
        self.h_sp.grid(row=0, column=1, sticky="w", padx=(8, 0), pady=3)
        self.h_sp.bind("<FocusOut>", self.on_manual)
        self.h_sp.bind("<Return>", self.on_manual)

        ttk.Label(self.man_box, text="屏幕宽", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        self.w_var = tk.IntVar(value=1440)
        self.w_sp = ttk.Spinbox(self.man_box, from_=200, to=6000, increment=2,
                                textvariable=self.w_var, command=self.on_manual,
                                width=10)
        self.w_sp.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=3)
        self.w_sp.bind("<FocusOut>", self.on_manual)
        self.w_sp.bind("<Return>", self.on_manual)

        ttk.Label(self.man_box, text="常用预设", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        self.preset_cb = ttk.Combobox(self.man_box, state="readonly",
                                      values=[p[0] for p in readers_db.PRESETS])
        self.preset_cb.grid(row=2, column=1, sticky="ew", padx=(8, 0), pady=3)
        self.preset_cb.bind("<<ComboboxSelected>>", self.on_preset)

        # 缩放规则说明（只有一条规则，不再给选项）
        sep = ttk.Separator(tab1, orient="horizontal")
        sep.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=10)
        ttk.Label(tab1, text="缩放规则", style="Card.TLabel").grid(
            row=4, column=0, sticky="nw", padx=10)
        ttk.Label(tab1, text="等比撑满，不裁切", style="Card.TLabel").grid(
            row=4, column=1, columnspan=2, sticky="w")

        self.fit_hint = ttk.Label(
            tab1, style="Hint.TLabel", wraplength=330, justify="left",
            text="每页等比缩放到刚好放进屏幕：竖版页高度贴满、横跨页宽度贴满，"
                 "另一边留白。画面永远完整，不会被切开或切掉。")
        self.fit_hint.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 6))

        # 横跨页优化（可选）
        rotf = tk.Frame(tab1, bg=CARD)
        rotf.grid(row=6, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 2))
        self.rot_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(rotf, text="横跨页优化", variable=self.rot_var,
                        command=self.on_rotate).pack(side="left")
        self.rot_dir_var = tk.StringVar(value="右转 90°（顺时针）")
        self.rot_dir_cb = ttk.Combobox(
            rotf, textvariable=self.rot_dir_var, state="disabled", width=16,
            values=["右转 90°（顺时针）", "左转 90°（逆时针）"])
        self.rot_dir_cb.pack(side="left", padx=(10, 0))
        self.rot_dir_cb.bind("<<ComboboxSelected>>", lambda e: self.delay_save())

        ttk.Label(tab1, style="Hint.TLabel", wraplength=330, justify="left",
                  text="宽高比 > 1.15 的横图转 90° 后撑满整页，看时把设备转过来。").grid(
            row=7, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))

        ttk.Button(tab1, text="管理型号…", command=self.open_manager).grid(
            row=8, column=0, sticky="w", padx=10, pady=(2, 8))
        self.cat_lbl = ttk.Label(tab1, text="", style="Hint.TLabel")
        self.cat_lbl.grid(row=8, column=1, columnspan=2, sticky="w")
        try:
            n_dev = sum(len(v) for v in self.catalog.values())
            self.cat_lbl.configure(text="内置 %d 品牌 / %d 型号" % (len(self.catalog), n_dev))
        except Exception:
            pass

        sumwrap = tk.Frame(tab1, bg="#f2f6ff", highlightbackground="#d7e3fb",
                           highlightthickness=1)
        sumwrap.grid(row=9, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 10))
        self.sum_lbl = tk.Label(sumwrap, text="", bg="#f2f6ff", fg="#1e40af",
                                justify="left", anchor="w", font=("Microsoft YaHei UI", 9))
        self.sum_lbl.pack(fill="x", padx=10, pady=8)

        legend = tk.Frame(tab1, bg=CARD)
        legend.grid(row=10, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 6))
        tk.Label(legend, text="状态含义", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w")
        for word, desc, color in (
                ("需放大", "图片比屏幕小，会被阅读器拉伸并出锯齿", WARNC),
                ("需缩小", "图片比屏幕大，会被阅读器压缩，锯齿更明显", WARNC),
                ("点对点", "已经是屏幕分辨率，无需处理", OKC),
                ("文字版", "正文超过 2 万字，为保护版式自动跳过", MUTED),
                ("需 Calibre", "MOBI / AZW3 需要 Calibre 才能处理", WARNC)):
            row = tk.Frame(legend, bg=CARD)
            row.pack(anchor="w")
            tk.Label(row, text=word, bg=CARD, fg=color, width=10, anchor="w",
                     font=("Microsoft YaHei UI", 8)).pack(side="left")
            tk.Label(row, text=desc, bg=CARD, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="left")

        # ---- 输出 ----
        tab2.columnconfigure(1, weight=1)

        self.out_var = tk.StringVar(value="same")
        tk.Label(tab2, text="输出位置", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 9, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(12, 2))
        of = ttk.Frame(tab2, style="Card.TFrame")
        of.grid(row=1, column=0, columnspan=3, sticky="w", padx=10)
        ttk.Radiobutton(of, text="与源文件同目录，名字加后缀", value="same",
                        variable=self.out_var, command=self.on_out).pack(anchor="w")
        ttk.Radiobutton(of, text="放到指定目录", value="custom",
                        variable=self.out_var, command=self.on_out).pack(anchor="w")
        ttk.Radiobutton(of, text="直接覆盖源文件（名字不变）", value="inplace",
                        variable=self.out_var, command=self.on_out).pack(anchor="w")

        self.out_entry = ttk.Entry(tab2)
        self.out_entry.grid(row=2, column=0, columnspan=2, sticky="ew", padx=10, pady=4)
        self.out_btn = ttk.Button(tab2, text="浏览…", command=self.pick_out)
        self.out_btn.grid(row=2, column=2, sticky="e", padx=(0, 10), pady=4)

        self.inplace_warn = tk.Label(
            tab2, bg="#fff3e0", fg="#b45309", justify="left", anchor="w",
            font=("Microsoft YaHei UI", 8),
            text="⚠ 会直接改写原文件，原始内容不可恢复。\n"
                 "　 建议先手动备份，或先用「加后缀」跑一遍确认效果。")
        self.inplace_warn.grid(row=3, column=0, columnspan=3, sticky="ew",
                               padx=10, pady=(2, 0))
        self.inplace_warn.grid_remove()

        self.recursive_var = tk.BooleanVar(value=False)
        self.overwrite_var = tk.BooleanVar(value=False)
        self.chk_recursive = ttk.Checkbutton(
            tab2, text="添加文件夹时包含子目录", variable=self.recursive_var,
            command=self.delay_save)
        self.chk_recursive.grid(row=4, column=0, columnspan=3, sticky="w",
                                padx=10, pady=(10, 2))
        self.chk_overwrite = ttk.Checkbutton(
            tab2, text="覆盖已存在的输出文件", variable=self.overwrite_var,
            command=self.delay_save)
        self.chk_overwrite.grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=2)

        ttk.Separator(tab2, orient="horizontal").grid(row=6, column=0, columnspan=3,
                                                      sticky="ew", padx=10, pady=10)

        ttk.Label(tab2, text="缩放算法", style="Card.TLabel").grid(row=7, column=0, sticky="w", padx=10, pady=3)
        self.filter_var = tk.StringVar(value="spline")
        fc = ttk.Combobox(tab2, textvariable=self.filter_var, state="readonly",
                          values=list(core.FILTERS), width=14)
        fc.grid(row=7, column=1, sticky="w")
        fc.bind("<<ComboboxSelected>>", lambda e: self.delay_save())

        ttk.Label(tab2, text="图像质量", style="Card.TLabel").grid(row=8, column=0, sticky="w", padx=10, pady=3)
        self.q_var = tk.IntVar(value=2)
        ttk.Spinbox(tab2, from_=2, to=8, textvariable=self.q_var, width=14,
                    command=self.delay_save).grid(row=8, column=1, sticky="w")

        ttk.Label(tab2, text="2 = 高保真（默认，推荐）\n"
                             "3 = 体积小 15%，肉眼几乎无差\n"
                             "4 = 小 24%，已到下限，再大开始出现块状噪点\n\n"
                             "这是 JPEG 的量化等级，只对 JPG 生效；包内 PNG 走无损，"
                             "不受此项影响。spline 算法本身没有可调数值。",
                  style="Hint.TLabel", justify="left", wraplength=330).grid(
            row=9, column=0, columnspan=3, sticky="w", padx=10, pady=(4, 0))

        prev = tk.Frame(tab2, bg="#f6f8fc", highlightbackground="#e3eaf6",
                        highlightthickness=1)
        prev.grid(row=10, column=0, columnspan=3, sticky="ew", padx=10, pady=(14, 4))
        tk.Label(prev, text="输出示例", bg="#f6f8fc", fg=MUTED,
                 font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", padx=10, pady=(7, 0))
        self.name_lbl = tk.Label(prev, text="", bg="#f6f8fc", fg="#1e40af",
                                 justify="left", anchor="w", wraplength=320,
                                 font=("Microsoft YaHei UI", 8))
        self.name_lbl.pack(anchor="w", padx=10, pady=(2, 8))

        ttk.Button(tab2, text="外部工具…", command=self.open_tools).grid(
            row=11, column=0, sticky="w", padx=10, pady=(6, 2))
        tab2.rowconfigure(12, weight=1)

        self.switch_tab(0)

    def switch_tab(self, idx):
        for j, f in enumerate(self.tab_pages):
            if j == idx:
                f.grid()
            else:
                f.grid_remove()
        for j, (cell, lbl, slbl) in enumerate(self.tab_btns):
            on = (j == idx)
            bg = ACCENT if on else "#e9eef6"
            cell.configure(bg=bg)
            lbl.configure(bg=bg, fg="#ffffff" if on else MUTED)
            slbl.configure(bg=bg, fg="#cbdcff" if on else "#9aa7b8")
        self.tab_index = idx

    def _build_log(self, parent):
        wrap = tk.Frame(parent, bg=CARD, highlightbackground=LINE,
                        highlightthickness=1)
        wrap.grid(row=1, column=0, sticky="nsew", padx=(0, 12), pady=(0, 8))
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        ttk.Label(wrap, text="日志", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2))
        self.log_txt = tk.Text(wrap, height=7, bg=CARD, fg=TEXT, bd=0,
                               font=("Consolas", 9), wrap="word",
                               padx=10, pady=4, insertwidth=0)
        self.log_txt.grid(row=1, column=0, sticky="nsew", padx=(4, 0), pady=(0, 6))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.log_txt.yview)
        self.log_txt.configure(yscrollcommand=vs.set)
        vs.grid(row=1, column=1, sticky="ns", padx=(0, 4), pady=(0, 6))
        for tag, color in (("info", TEXT), ("ok", OKC), ("warn", WARNC),
                           ("err", ERRC), ("muted", MUTED)):
            self.log_txt.tag_configure(tag, foreground=color)
        self.log_txt.configure(state="disabled")

        # 拖放
        if self.dnd_ok:
            try:
                from tkinterdnd2 import DND_FILES
                for w in (self.tree, self.drop_hint):
                    w.drop_target_register(DND_FILES)
                    w.dnd_bind("<<Drop>>", self.on_drop)
            except Exception:
                pass

    # ---------------- 日志 ----------------
    def log(self, msg: str, level="info"):
        self.q.put(("log", msg, level))

    def _append_log(self, msg, level):
        self.log_txt.configure(state="normal")
        self.log_txt.insert("end", msg + "\n", level)
        self.log_txt.see("end")
        self.log_txt.configure(state="disabled")

    # ---------------- 配置 ----------------
    def _load_cfg_to_ui(self):
        c = self.cfg
        self.mode_var.set(c.get("mode", "auto"))
        self.brand_var.set(c.get("brand") or readers_db.all_brands()[0])
        self._refresh_models(c.get("model"))
        self.h_var.set(int(c.get("height", 1920) or 1920))
        self.w_var.set(int(c.get("width", 1440) or 1440))
        self.filter_var.set(c.get("filter", "spline"))
        self.q_var.set(int(c.get("quality", 2) or 2))
        self.out_var.set(c.get("out_mode", "same"))
        self.out_entry.insert(0, c.get("out_dir", ""))
        self.recursive_var.set(bool(c.get("recursive")))
        self.overwrite_var.set(bool(c.get("overwrite")))
        self.rot_var.set(bool(c.get("rotate_spread")))
        self.rot_dir_var.set("右转 90°（顺时针）"
                             if int(c.get("rotate_dir", 1) or 1) == 1
                             else "左转 90°（逆时针）")
        self.on_mode()
        self.on_rotate()
        self.on_out()

    def _refresh_models(self, want=None):
        devs = self.catalog.get(self.brand_var.get(), [])
        names = [d[0] for d in devs]
        self.model_cb.configure(values=names)
        if want in names:
            self.model_var.set(want)
        elif names:
            self.model_var.set(names[0])
        else:
            self.model_var.set("")

    def _dev(self):
        brand = self.brand_var.get()
        model = self.model_var.get()
        for d in self.catalog.get(brand, []):
            if d[0] == model:
                return d
        return None

    def current_target(self) -> core.Target:
        try:
            w, h = int(self.w_var.get()), int(self.h_var.get())
        except Exception:
            w, h = 1440, 1920
        if self.mode_var.get() == "auto":
            d = self._dev()
            if d:
                return core.Target(d[1], d[2])
            return core.Target(w, h)
        return core.Target(w, h)

    def on_mode(self, *_):
        auto = self.mode_var.get() == "auto"
        if auto:
            self.man_box.grid_remove()
            self.auto_box.grid()
        else:
            self.auto_box.grid_remove()
            self.man_box.grid()
        self.update_dev_label()
        self.delay_save()

    def on_brand(self, *_):
        self._refresh_models()
        self.on_model()

    def on_model(self, *_):
        self.update_dev_label()
        self.delay_save()
        self.rescan_existing()

    def on_manual(self, *_):
        try:
            h = int(self.h_var.get())
            w = int(self.w_var.get())
        except Exception:
            return
        if h < 100:
            self.h_var.set(1920)
        if w < 100:
            self.w_var.set(1440)
        self.update_dev_label()
        self.delay_save()
        self.rescan_existing()

    def on_preset(self, *_):
        name = self.preset_cb.get()
        for label, h in readers_db.PRESETS:
            if label == name:
                self.mode_var.set("manual")
                self.on_mode()
                self.h_var.set(h)
                self.w_var.set(int(round(h * 0.75 / 2)) * 2)
                self.on_manual()
                break

    def on_rotate(self, *_):
        self.rot_dir_cb.configure(state="readonly" if self.rot_var.get() else "disabled")
        self.update_dev_label()
        self.delay_save()

    def _update_name_preview(self):
        if getattr(self, "name_lbl", None) is None:
            return
        t = self.current_target()
        src = "某漫画 卷01.epub"
        mode = self.out_var.get()
        if mode == "inplace":
            txt = "%s  →  同名覆盖原文件" % src
        elif mode == "custom":
            d = self.out_entry.get().strip() or "D:/已适配"
            txt = "%s  →  %s\\某漫画 卷01%s.epub" % (src, d.rstrip("\\"), t.suffix())
        else:
            txt = "%s  →  某漫画 卷01%s.epub" % (src, t.suffix())
        self.name_lbl.configure(text=txt)

    def on_out(self, *_):
        mode = self.out_var.get()
        custom = mode == "custom"
        state = "normal" if custom else "disabled"
        self.out_entry.configure(state=state)
        self.out_btn.configure(state=state)
        if mode == "inplace":
            self.inplace_warn.grid()
            self.chk_overwrite.configure(state="disabled")
        else:
            self.inplace_warn.grid_remove()
            self.chk_overwrite.configure(state="normal")
        self._update_name_preview()
        self.delay_save()

    def pick_out(self):
        d = filedialog.askdirectory(title="选择输出目录",
                                    initialdir=self.out_entry.get() or self.cfg.get("last_dir") or "")
        if d:
            self.out_entry.delete(0, "end")
            self.out_entry.insert(0, d)
            self.delay_save()

    def delay_save(self, *_):
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(400, self.save_now)

    def collect_cfg(self) -> dict:
        def _int(var, default):
            try:
                v = int(var.get())
                return v if v > 0 else default
            except Exception:
                return default

        c = dict(DEFAULT_CFG)          # 以默认值为底，旧版本残留的键会被自然清掉
        c.update({
            "mode": self.mode_var.get(),
            "brand": self.brand_var.get(),
            "model": self.model_var.get(),
            "width": _int(self.w_var, 1440),
            "height": _int(self.h_var, 1920),
            "filter": self.filter_var.get(),
            "quality": _int(self.q_var, 2),
            "out_mode": self.out_var.get(),
            "out_dir": self.out_entry.get().strip(),
            "recursive": bool(self.recursive_var.get()),
            "overwrite": bool(self.overwrite_var.get()),
            "rotate_spread": bool(self.rot_var.get()),
            "rotate_dir": 1 if "顺" in self.rot_dir_var.get() else 2,
            "ffmpeg": self.cfg.get("ffmpeg", ""),
            "calibre": self.cfg.get("calibre", ""),
            "last_dir": self.cfg.get("last_dir", ""),
            "custom_devices": self.cfg.get("custom_devices", {}),
        })
        try:
            c["geom"] = self.root.geometry()
        except Exception:
            pass
        return c

    def save_now(self):
        self._save_job = None
        self.cfg = self.collect_cfg()
        save_cfg(self.cfg)

    def _apply_geom(self):
        g = self.cfg.get("geom") or ""
        if g and "x" in g:
            try:
                self.root.geometry(g)
                return
            except Exception:
                pass
        self.root.geometry("1180x820")

    def update_dev_label(self):
        t = self.current_target()
        if self.mode_var.get() == "auto":
            d = self._dev()
            if d:
                inch = d[3] if len(d) > 3 else 0
                ppi = d[4] if len(d) > 4 else 0
                certain = d[5] if len(d) > 5 else True
                txt = "%d × %d" % (d[1], d[2])
                if inch:
                    txt += "   %.2f 寸" % inch
                if ppi:
                    txt += "   %d ppi" % ppi
                if len(d) > 6 and d[6]:
                    txt += "\n" + str(d[6])
                if not certain:
                    txt += "\n⚠ 该型号参数待核，建议以设备实测为准"
                self.dev_lbl.configure(text=txt,
                                       foreground=WARNC if not certain else MUTED)
            else:
                self.dev_lbl.configure(text="未选择型号", foreground=MUTED)
        else:
            self.dev_lbl.configure(
                foreground=MUTED,
                text="手动目标：%d × %d px" % (t.w, t.h))

        if getattr(self, "sum_lbl", None) is not None:
            body = ("等比撑满 %d × %d 的屏幕，不裁切；\n"
                    "竖版页高度贴满，横跨页宽度贴满" % (t.w, t.h))
            if self.rot_var.get():
                body += "\n横跨页：转 90° 后撑满整页"
            self.sum_lbl.configure(text="本次输出：%s\n文件名后缀：%s"
                                        % (body, t.suffix()))
        self._update_name_preview()

    # ---------------- Calibre 依赖 ----------------
    def need_calibre(self, path) -> bool:
        return (not self.calibre) and path.lower().endswith(KF8_EXT)

    def kf8_files(self):
        return [f for f in self.files if self.need_calibre(f)]

    def calibre_dialog(self, count) -> str:
        """返回 'download' / 'skip' / 'cancel'。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("需要 Calibre")
        dlg.configure(bg=CARD)
        dlg.transient(self.root)
        dlg.resizable(False, False)
        dlg.grab_set()

        wrap = tk.Frame(dlg, bg=CARD, padx=24, pady=20)
        wrap.pack(fill="both", expand=True)

        tk.Label(wrap, text="处理 MOBI / AZW3 需要 Calibre", bg=CARD, fg=TEXT,
                 font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        tk.Label(wrap, justify="left", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 9),
                 text=("当前列表里有 %d 个 MOBI / AZW3 文件。\n"
                       "这两种格式的正文是压缩流，直接改二进制会破坏目录索引，\n"
                       "所以本程序要通过 Calibre 的 ebook-convert 做\n"
                       "「转 EPUB → 处理 → 转回原格式」的往返。\n\n"
                       "Calibre 是免费开源软件，装好后本程序会自动找到它，\n"
                       "不需要额外配置。" % count)).pack(anchor="w", pady=(10, 0))

        result = {"v": "cancel"}

        def done(v):
            result["v"] = v
            dlg.destroy()

        btns = tk.Frame(wrap, bg=CARD)
        btns.pack(fill="x", pady=(18, 0))
        ttk.Button(btns, text="打开下载页", style="Accent.TButton",
                   command=lambda: done("download")).pack(side="left")
        ttk.Button(btns, text="跳过这些文件，先处理其他",
                   command=lambda: done("skip")).pack(side="left", padx=8)
        ttk.Button(btns, text="取消", command=lambda: done("cancel")).pack(side="right")

        dlg.protocol("WM_DELETE_WINDOW", lambda: done("cancel"))
        dlg.update_idletasks()
        w, h = dlg.winfo_reqwidth(), dlg.winfo_reqheight()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - h) // 3
        dlg.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.root.wait_window(dlg)
        return result["v"]

    def handle_calibre_need(self, count) -> str:
        """弹提示并处理「去下载」；返回 'skip' 或 'cancel' 或 'ok'。"""
        act = self.calibre_dialog(count)
        if act == "download":
            try:
                webbrowser.open(CALIBRE_URL)
            except Exception:
                pass
            self.log("已打开 Calibre 下载页：%s" % CALIBRE_URL, "info")
            return "cancel"
        return act

    # ---------------- 环境报告 ----------------
    def _report_env(self):
        parts = []
        if self.ffmpeg:
            parts.append("ffmpeg ✓")
        else:
            parts.append("ffmpeg ✗ 未找到")
            self.log("未找到 ffmpeg，将退化为 Pillow 缩放（质量略低）。"
                     "可在「输出 → 外部工具」里手动指定。", "warn")
        if self.calibre:
            parts.append("Calibre ✓")
        else:
            parts.append("Calibre ✗")
            self.log("未找到 Calibre：MOBI / AZW3 无法处理，加入这类文件时会提示。"
                     "装好后到「输出 → 外部工具」点「重新检测」即可，不必重启。", "warn")
        if not core.probe_pdf_renderer():
            parts.append("PDF ✗")
            self.log("缺少 PDF 组件（pypdfium2），PDF 无法处理。", "warn")
        else:
            parts.append("PDF ✓")
        self.env_lbl.configure(text="  ·  ".join(parts))

    # ---------------- 文件操作 ----------------
    def on_drop(self, event):
        try:
            paths = self.root.tk.splitlist(event.data)
        except Exception:
            paths = [event.data]
        self.add_paths(list(paths))

    def add_files(self):
        types = [("支持的电子书", "*.epub *.mobi *.azw3 *.azw *.prc *.zip *.cbz *.pdf"),
                 ("EPUB", "*.epub"), ("Kindle", "*.mobi *.azw3 *.azw"),
                 ("压缩包", "*.zip *.cbz"), ("PDF", "*.pdf"), ("全部文件", "*.*")]
        fs = filedialog.askopenfilenames(title="选择电子书",
                                         initialdir=self.cfg.get("last_dir") or "",
                                         filetypes=types)
        if fs:
            self.add_paths(list(fs))

    def add_folder(self):
        d = filedialog.askdirectory(title="选择文件夹",
                                    initialdir=self.cfg.get("last_dir") or "")
        if d:
            self.add_paths([d])

    def add_paths(self, paths):
        found = core.collect(paths, recursive=bool(self.recursive_var.get()))
        if not found:
            self.log("没有找到支持的文件（%s）" % "、".join(core.SUPPORTED_EXT), "warn")
            return
        added = 0
        for f in found:
            if f not in self.files:
                self.files.append(f)
                added += 1
        for p in paths:
            if os.path.isdir(p):
                self.cfg["last_dir"] = p
            elif os.path.isfile(p):
                self.cfg["last_dir"] = os.path.dirname(p)
        self.delay_save()
        self.log("加入 %d 个文件（共 %d 个）" % (added, len(self.files)))
        need = self.kf8_files()
        if need and not self._calibre_warned:
            self._calibre_warned = True
            self.log("检测到 %d 个 MOBI / AZW3，但本机没找到 Calibre。" % len(need), "warn")
            self.handle_calibre_need(len(need))
        self.refresh_tree()
        self.scan_all()

    def remove_sel(self):
        sel = self.tree.selection()
        keep = []
        drop = set()
        for iid in sel:
            drop.add(self.tree.set(iid, "name"))
        # 用 iid 对应行序号更可靠
        idxs = sorted([self.tree.index(i) for i in sel], reverse=True)
        for i in idxs:
            if 0 <= i < len(self.files):
                self.files.pop(i)
        self.refresh_tree()
        self.scan_all()
        self.count_lbl.configure(text="共 %d 个文件" % len(self.files))

    def clear_all(self):
        self.files = []
        self.infos = {}
        self.refresh_tree()
        self.count_lbl.configure(text="")

    def refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        for p in self.files:
            info = self.infos.get(p)
            if info:
                self._row(p, info)
            else:
                self.tree.insert("", "end", values=(
                    os.path.basename(p), os.path.splitext(p)[1].lstrip(".").upper(),
                    "", "", "", "待扫描"), tags=("muted", "odd"))
        self.count_lbl.configure(text="共 %d 个文件" % len(self.files))

    def _row(self, p, info):
        ext = info["ext"].lstrip(".").upper()
        need_cal = self.need_calibre(p)
        if info.get("err"):
            if need_cal:
                vals = (info["name"], ext, "%.1f MB" % info["size_mb"], "-", "-", "需 Calibre")
                tag = ("warn",)
            else:
                vals = (info["name"], ext, "%.1f MB" % info["size_mb"], "-", "-", "读取失败")
                tag = ("err",)
        else:
            h = "%d  (%d~%d)" % (info["h_med"], info["h_min"], info["h_max"])
            v = info.get("verdict", "")
            if need_cal:
                v = "需 Calibre"
            tag = ("ok",) if v == "点对点" else ("warn",) if v in ("需放大", "需缩小", "需 Calibre") else ("muted",)
            vals = (info["name"], ext, "%.1f MB" % info["size_mb"],
                    str(info["n_img"]), h, v)
        alt = "even" if len(self.tree.get_children()) % 2 else "odd"
        self.tree.insert("", "end", values=vals, tags=(tag[0], alt))
        self.infos[p] = info

    def rescan_existing(self):
        """目标参数变了只需重算判定，不必重新读文件。"""
        if not self.files or not self.infos:
            return
        target = self.current_target()
        for info in self.infos.values():
            if info.get("err"):
                info["verdict"] = "读取失败"
            elif info.get("kind") == "text":
                info["verdict"] = "文字版"
            elif info.get("n_img"):
                info["verdict"] = core.verdict_of(info, target)
        self.refresh_tree()

    # ---------------- 扫描 ----------------
    def scan_all(self, silent=False):
        if not self.files:
            return
        if self.busy:
            return
        self.busy = True
        target = self.current_target()
        files = list(self.files)
        self._set_busy_ui(True, "扫描中…")
        self.pbar.configure(mode="determinate", value=0, maximum=len(files))

        def run():
            ok = 0
            for i, f in enumerate(files, 1):
                if self.stop_flag.is_set():
                    break
                info = core.scan(f, target)
                self.q.put(("scan", f, info))
                self.q.put(("progress", i, len(files), "扫描 %d/%d" % (i, len(files))))
                ok += 1
            self.q.put(("scan_done", silent))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    # ---------------- 处理 ----------------
    def start(self, apply: bool):
        if self.busy:
            self.status.configure(text="正在忙，等当前任务结束再试")
            return
        if not self.files:
            messagebox.showinfo(APP_NAME, "先添加文件。")
            return
        self.save_now()

        target = self.current_target()
        if self.mode_var.get() == "auto" and not self._dev():
            messagebox.showwarning(APP_NAME, "请先选择阅读器型号。")
            return

        skip_set = set()
        kf8 = self.kf8_files()
        if kf8 and apply:
            act = self.handle_calibre_need(len(kf8))
            if act != "skip":
                return
            skip_set = set(kf8)

        try:
            q = int(self.q_var.get())
        except Exception:
            q = 2
        opts = core.Options(filter_name=self.filter_var.get(),
                            quality=q,
                            overwrite=bool(self.overwrite_var.get()),
                            ffmpeg=self.ffmpeg, calibre=self.calibre,
                            rotate_spread=bool(self.rot_var.get()),
                            rotate_dir=1 if "顺" in self.rot_dir_var.get() else 2)

        inplace = self.out_var.get() == "inplace"
        out_dir = None
        if self.out_var.get() == "custom":
            out_dir = self.out_entry.get().strip()
            if not out_dir:
                messagebox.showwarning(APP_NAME, "请选择输出目录。")
                return
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception as e:
                messagebox.showerror(APP_NAME, "输出目录不可用：%s" % e)
                return

        if apply and inplace:
            if not messagebox.askyesno(
                    APP_NAME,
                    "将直接覆盖列表里的 %d 个源文件，原始内容不可恢复。\n\n"
                    "已经点对点的文件会自动跳过，不会被改写。\n\n确定继续吗？"
                    % len(self.files)):
                return

        self.stop_flag.clear()
        self.busy = True
        self._set_busy_ui(True, "处理中…")
        self.pbar.configure(mode="determinate", value=0, maximum=100)

        files = list(self.files)
        self.log("")
        self.log("── %s ──  目标：%s%s" % ("开始处理" if apply else "预演扫描",
                                         target.label(),
                                         "" if apply else "（不改动任何文件）"),
                 "info")

        def run():
            total = len(files)
            done = 0
            skipped = 0
            failed = 0
            for idx, src in enumerate(files, 1):
                if self.stop_flag.is_set():
                    self.q.put(("log", "已停止。", "warn"))
                    break
                info = self.infos.get(src) or core.scan(src, target)
                verdict = info.get("verdict", "")
                name = os.path.basename(src)

                if src in skip_set:
                    self.q.put(("log", "  [%d/%d] %s  →  需要 Calibre，已跳过"
                                % (idx, total, name), "warn"))
                    skipped += 1
                    self.q.put(("progress", idx, total, name))
                    continue

                if info.get("err"):
                    self.q.put(("log", "  [%d/%d] %s  →  %s" % (idx, total, name, info["err"]), "err"))
                    failed += 1
                    self.q.put(("progress", idx, total, name))
                    continue

                if verdict == "文字版":
                    self.q.put(("log", "  [%d/%d] %s  →  文字版，跳过" % (idx, total, name), "muted"))
                    skipped += 1
                    self.q.put(("progress", idx, total, name))
                    continue

                if verdict == "点对点":
                    self.q.put(("log", "  [%d/%d] %s  →  已点对点，跳过" % (idx, total, name), "muted"))
                    skipped += 1
                    self.q.put(("progress", idx, total, name))
                    continue

                if not apply:
                    self.q.put(("log", "  [%d/%d] %s  →  需处理（%d 张，中位高 %d）"
                                % (idx, total, name, info["n_img"], info["h_med"]), "ok"))
                    done += 1
                    self.q.put(("progress", idx, total, name))
                    continue

                if inplace:
                    dst = src
                    overwriting = True
                    self.q.put(("log", "  [%d/%d] %s  →  原地覆盖源文件"
                                % (idx, total, name), "warn"))
                    dst_dir = os.path.dirname(src)
                else:
                    dst_dir = out_dir or os.path.dirname(src)
                    dst = os.path.join(dst_dir, core.out_name(src, target))

                overwriting = False
                if not inplace and os.path.exists(dst):
                    if not opts.overwrite:
                        self.q.put(("log", "  [%d/%d] %s  →  输出已存在，跳过（未勾选覆盖）"
                                    % (idx, total, name), "warn"))
                        skipped += 1
                        self.q.put(("progress", idx, total, name))
                        continue
                    overwriting = True
                    try:
                        _sz = os.path.getsize(dst)
                        old_txt = ("%.1f MB" % (_sz / 1048576.0)) if _sz >= 1048576 \
                            else ("%d KB" % max(1, _sz // 1024))
                    except Exception:
                        old_txt = "大小未知"
                    # 预检：被占用就提前报，别等打包完才失败
                    try:
                        open(dst, "ab").close()
                    except Exception:
                        self.q.put(("log", "  [%d/%d] %s  →  输出文件被占用，无法覆盖"
                                    "（关掉正在看它的程序再试）" % (idx, total, name), "err"))
                        failed += 1
                        self.q.put(("progress", idx, total, name))
                        continue
                    self.q.put(("log", "  [%d/%d] %s  →  覆盖已有输出（原 %s）"
                                % (idx, total, name, old_txt), "warn"))

                self.q.put(("log", "  [%d/%d] %s" % (idx, total, name), "info"))
                try:
                    r = core.process_file(
                        src, dst, target, opts,
                        log=lambda m, lv="info": self.q.put(("log", "  " + m if not m.startswith("  ") else m, lv)),
                        progress=lambda a, b: self.q.put(("sub", a, b)))
                    if r.ok:
                        self.q.put(("log", "      ✓ %s  %s  %.1f MB%s" %
                                    (os.path.basename(dst), r.msg, r.size_mb,
                                     "（已覆盖）" if overwriting else ""), "ok"))
                        done += 1
                        if inplace:
                            self.q.put(("rescan", src, src))
                    else:
                        self.q.put(("log", "      ✗ %s" % r.msg, "err"))
                        failed += 1
                except Exception as e:
                    msg = str(e).strip() or e.__class__.__name__
                    if isinstance(e, RuntimeError):
                        self.q.put(("log", "      ✗ %s" % msg, "err"))
                    else:
                        self.q.put(("log", "      ✗ 出错：%s" % msg, "err"))
                        self.q.put(("log", traceback.format_exc(limit=2), "muted"))
                    failed += 1
                self.q.put(("progress", idx, total, name))

            self.q.put(("done", done, skipped, failed))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def on_stop(self):
        self.stop_flag.set()
        self.status.configure(text="正在停止…")

    def _set_busy_ui(self, busy, text=""):
        st = "disabled" if busy else "normal"
        self.btn_run.configure(state=st)
        self.btn_scan.configure(state=st)
        self.btn_stop.configure(state="normal" if busy else "disabled")
        self.status.configure(text=text or ("忙" if busy else "空闲"))

    # ---------------- 消息泵 ----------------
    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self._append_log(msg[1], msg[2])
                elif kind == "progress":
                    i, n, label = msg[1], msg[2], msg[3]
                    self.pbar.configure(maximum=max(1, n), value=i)
                    self.status.configure(text="%s  %d/%d" % (label, i, n))
                elif kind == "sub":
                    a, b = msg[1], msg[2]
                    self.status.configure(text="图片 %d/%d" % (a, b))
                elif kind == "scan":
                    f, info = msg[1], msg[2]
                    self.infos[f] = info
                elif kind == "scan_done":
                    self.refresh_tree()
                    self.busy = False
                    self._set_busy_ui(False)
                    self.pbar.configure(maximum=100, value=0)
                    n_todo = sum(1 for i in self.infos.values()
                                 if i.get("verdict") in ("需放大", "需缩小"))
                    n_ok = sum(1 for i in self.infos.values() if i.get("verdict") == "点对点")
                    n_txt = sum(1 for i in self.infos.values() if i.get("verdict") == "文字版")
                    n_bad = sum(1 for i in self.infos.values() if i.get("err"))
                    self.status.configure(
                        text="扫描完成：需处理 %d · 点对点 %d · 文字版 %d · 失败 %d"
                             % (n_todo, n_ok, n_txt, n_bad))
                    if not msg[1]:
                        self.log("扫描完成：需处理 %d、点对点 %d、文字版 %d、失败 %d"
                                 % (n_todo, n_ok, n_txt, n_bad),
                                 "warn" if n_bad else "info")
                        if n_todo == 0 and n_bad == 0:
                            self.log("所有文件都已是目标分辨率，无需处理。", "ok")
                elif kind == "rescan":
                    self._dirty.add(msg[1])
                elif kind == "done":
                    done, skipped, failed = msg[1], msg[2], msg[3]
                    self.busy = False
                    self._set_busy_ui(False)
                    self.pbar.configure(value=self.pbar["maximum"])
                    self.status.configure(
                        text="完成：成功 %d · 跳过 %d · 失败 %d" % (done, skipped, failed))
                    self.log("完成：成功 %d，跳过 %d，失败 %d" % (done, skipped, failed),
                             "ok" if failed == 0 else "warn")
                    if self._dirty:
                        self._dirty.clear()
                        self.root.after(400, lambda: self.scan_all(silent=True))
        except queue.Empty:
            pass
        except Exception:
            self._append_log(traceback.format_exc(limit=2), "err")
        self.root.after(90, self._poll)

    # ---------------- 型号管理 ----------------
    def open_manager(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("型号管理")
        dlg.configure(bg=BG)
        dlg.geometry("760x520")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="内置型号可在此校正，也可添加自己的设备。修改后立即生效并记住。",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(12, 6))

        top = tk.Frame(dlg, bg=BG)
        top.pack(fill="both", expand=True, padx=14)
        top.columnconfigure(0, weight=1)
        top.rowconfigure(0, weight=1)

        cols = ("brand", "model", "w", "h", "inch", "note", "src")
        tv = ttk.Treeview(top, columns=cols, show="headings", selectmode="browse")
        for k, t, w in (("brand", "品牌", 110), ("model", "型号", 170),
                        ("w", "宽", 60), ("h", "高", 60),
                        ("inch", "英寸", 60), ("note", "备注", 170),
                        ("src", "来源", 60)):
            tv.heading(k, text=t)
            tv.column(k, width=w, anchor="w" if k in ("brand", "model", "note") else "center")
        tv.grid(row=0, column=0, sticky="nsew")
        vs = ttk.Scrollbar(top, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        vs.grid(row=0, column=1, sticky="ns")

        for brand, devs in self.catalog.items():
            for d in devs:
                src = "自定义" if brand in (self.cfg.get("custom_devices") or {}) else "内置"
                tv.insert("", "end", values=(brand, d[0], d[1], d[2],
                                             d[3] if len(d) > 3 else "", 
                                             d[6] if len(d) > 6 else "", src))

        form = tk.Frame(dlg, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        form.pack(fill="x", padx=14, pady=10)
        f = ttk.Frame(form, style="Card.TFrame")
        f.pack(fill="x", padx=10, pady=10)
        labels = ["品牌", "型号", "宽", "高", "英寸", "备注"]
        vars_ = [tk.StringVar() for _ in labels]
        for i, (lb, v) in enumerate(zip(labels, vars_)):
            ttk.Label(f, text=lb, style="Card.TLabel").grid(row=0, column=i * 2, padx=(0, 4))
            ttk.Entry(f, textvariable=v, width=12 if i > 1 else 14).grid(row=0, column=i * 2 + 1, padx=(0, 10))

        def fill_sel(_=None):
            sel = tv.selection()
            if not sel:
                return
            v = tv.item(sel[0], "values")
            for var, val in zip(vars_, v):
                var.set(val)

        tv.bind("<<TreeviewSelect>>", fill_sel)

        btns = tk.Frame(dlg, bg=BG)
        btns.pack(fill="x", padx=14, pady=(0, 14))

        def add_or_update():
            brand = vars_[0].get().strip()
            model = vars_[1].get().strip()
            try:
                w = int(vars_[2].get()); h = int(vars_[3].get())
            except Exception:
                messagebox.showerror(APP_NAME, "宽高必须是数字。", parent=dlg)
                return
            if not brand or not model:
                messagebox.showerror(APP_NAME, "品牌和型号不能为空。", parent=dlg)
                return
            inch = 0.0
            try:
                inch = float(vars_[4].get()) if vars_[4].get().strip() else 0.0
            except Exception:
                pass
            note = vars_[5].get().strip()
            dec = self.cfg.setdefault("custom_devices", {}).setdefault(brand, [])
            item = [model, w, h, inch, 0, note]
            for i, e in enumerate(dec):
                if e[0] == model:
                    dec[i] = item
                    break
            else:
                dec.append(item)
            self.cfg["custom_devices"] = dict(self.cfg.get("custom_devices", {}))
            self.catalog = build_catalog(self.cfg["custom_devices"])
            save_cfg(self.collect_cfg())
            dlg.destroy()
            self.open_manager()
            if brand == self.brand_var.get():
                self._refresh_models(model)
            self.log("型号已保存：%s %s %d×%d" % (brand, model, w, h), "ok")

        def delete_sel():
            sel = tv.selection()
            if not sel:
                return
            v = tv.item(sel[0], "values")
            brand, model = v[0], v[1]
            if not messagebox.askyesno(APP_NAME, "删除 %s %s ？" % (brand, model), parent=dlg):
                return
            dec = (self.cfg.get("custom_devices") or {}).get(brand, [])
            found = False
            for i, e in enumerate(dec):
                if e[0] == model:
                    dec.pop(i)
                    found = True
                    break
            if found:
                save_cfg(self.collect_cfg())
                dlg.destroy()
                self.open_manager()
            else:
                messagebox.showinfo(APP_NAME, "内置型号不能删除，可以选中后修改参数并保存覆盖。", parent=dlg)

        ttk.Button(btns, text="新增 / 覆盖保存", style="Accent.TButton",
                   command=add_or_update).pack(side="left")
        ttk.Button(btns, text="删除自定义型号", command=delete_sel).pack(side="left", padx=8)
        ttk.Button(btns, text="关闭", command=dlg.destroy).pack(side="right")
        tk.Label(btns, text="内置型号参数已交叉校验，仍建议以设备实测为准",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(side="left", padx=12)

    # ---------------- 外部工具 ----------------
    def open_tools(self):
        dlg = tk.Toplevel(self.root)
        dlg.title("外部工具")
        dlg.configure(bg=BG)
        dlg.geometry("660x330")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="ffmpeg 负责高质量缩放；Calibre 负责 MOBI / AZW3 的往返转换。",
                 bg=BG, fg=TEXT, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(dlg, text="留空则自动查找。改完点保存即时生效，不必重启。",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=14, pady=(0, 10))

        f1 = tk.Frame(dlg, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        f1.pack(fill="x", padx=14, pady=4)
        v1 = tk.StringVar(value=self.cfg.get("ffmpeg", ""))
        tk.Label(f1, text="ffmpeg.exe", bg=CARD, fg=TEXT).pack(side="left", padx=10, pady=8)
        ttk.Entry(f1, textvariable=v1, width=44).pack(side="left", expand=True, fill="x", pady=8)
        ttk.Button(f1, text="选择…", command=lambda: self._pick_exe(v1, "ffmpeg.exe")).pack(side="left", padx=6)

        f2 = tk.Frame(dlg, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        f2.pack(fill="x", padx=14, pady=4)
        v2 = tk.StringVar(value=self.cfg.get("calibre", ""))
        tk.Label(f2, text="ebook-convert.exe", bg=CARD, fg=TEXT).pack(side="left", padx=10, pady=8)
        ttk.Entry(f2, textvariable=v2, width=44).pack(side="left", expand=True, fill="x", pady=8)
        ttk.Button(f2, text="选择…", command=lambda: self._pick_exe(v2, "ebook-convert.exe")).pack(side="left", padx=6)

        stat = tk.Label(dlg, text="", bg=BG, fg=MUTED, justify="left",
                        font=("Microsoft YaHei UI", 8))
        stat.pack(anchor="w", padx=14, pady=(8, 0))

        def refresh_stat():
            parts = []
            parts.append("ffmpeg：" + ("已找到" if self.ffmpeg else "未找到"))
            parts.append("Calibre：" + ("已找到" if self.calibre else "未找到（MOBI / AZW3 需要它）"))
            stat.configure(text="   ·   ".join(parts), fg=TEXT if self.calibre else WARNC)

        def redetect():
            self.cfg["ffmpeg"] = v1.get().strip()
            self.cfg["calibre"] = v2.get().strip()
            self.ffmpeg = core.find_ffmpeg(self.cfg["ffmpeg"] or None)
            self.calibre = core.find_calibre(self.cfg["calibre"] or None)
            if self.ffmpeg:
                v1.set(self.ffmpeg)
            if self.calibre:
                v2.set(self.calibre)
            refresh_stat()
            self.log("重新检测：ffmpeg=%s  calibre=%s" %
                     (self.ffmpeg or "未找到", self.calibre or "未找到"),
                     "ok" if (self.ffmpeg and self.calibre) else "warn")

        def apply():
            redetect()
            save_cfg(self.collect_cfg())
            self._report_env()
            self.refresh_tree()
            dlg.destroy()
            if not self.calibre and self.kf8_files():
                self.log("仍没找到 Calibre，MOBI / AZW3 会继续被跳过。", "warn")

        b = tk.Frame(dlg, bg=BG)
        b.pack(fill="x", padx=14, pady=12)
        ttk.Button(b, text="保存", style="Accent.TButton", command=apply).pack(side="left")
        ttk.Button(b, text="重新检测", command=redetect).pack(side="left", padx=8)
        ttk.Button(b, text="下载 Calibre",
                   command=lambda: webbrowser.open(CALIBRE_URL)).pack(side="left")
        ttk.Button(b, text="取消", command=dlg.destroy).pack(side="right")
        refresh_stat()

    def _pick_exe(self, var, name):
        p = filedialog.askopenfilename(title="选择 " + name,
                                       filetypes=[("可执行文件", "*.exe"), ("全部", "*.*")])
        if p:
            var.set(p)


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def enable_dpi():
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _selftest(argv):
    """--selftest <报告文件> [扫描路径...]：供打包后验证引擎，不弹窗。"""
    i = argv.index("--selftest")
    out = argv[i + 1] if len(argv) > i + 1 else "selftest.txt"
    paths = [a for a in argv[i + 2:] if not a.startswith("--")]
    L = []
    L.append("APP      %s v%s" % (APP_NAME, APP_VER))
    L.append("frozen   %s" % bool(getattr(sys, "frozen", False)))
    L.append("exe      %s" % sys.executable)
    ff = core.find_ffmpeg()
    cal = core.find_calibre()
    L.append("ffmpeg   %s" % (ff or "NOT FOUND"))
    L.append("calibre  %s" % (cal or "NOT FOUND"))
    L.append("pdf      %s" % core.probe_pdf_renderer())
    if ff:
        try:
            import subprocess
            r = subprocess.run([ff, "-version"], capture_output=True, timeout=20)
            L.append("ffmpeg_ok %s" % (r.returncode == 0))
            L.append("ffmpeg_v  %s" % r.stdout.decode("utf-8", "ignore").split("\n")[0][:90])
        except Exception as e:
            L.append("ffmpeg_ok False (%s)" % e)
    try:
        from PIL import Image
        L.append("pillow   %s" % Image.__version__)
    except Exception as e:
        L.append("pillow   MISSING %s" % e)
    try:
        import pypdfium2
        L.append("pdfium   ok")
    except Exception as e:
        L.append("pdfium   MISSING %s" % e)
    try:
        from tkinterdnd2 import TkinterDnD  # noqa: F401
        L.append("tkdnd    ok")
    except Exception as e:
        L.append("tkdnd    MISSING %s" % e)
    L.append("config   %s" % config_path())

    t = core.Target(1440, 1920)
    if paths:
        L.append("")
        L.append("== 扫描 ==")
        for f in core.collect(paths, recursive=False):
            info = core.scan(f, t)
            L.append("%s | %s | %d 张 | h=%d | %s | %s" % (
                info["name"], "%.1fMB" % info["size_mb"], info["n_img"],
                info["h_med"], info["verdict"], info["err"]))

    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


def main():
    if "--selftest" in sys.argv:
        _selftest(sys.argv)
        return
    enable_dpi()
    root, dnd = _make_root()
    app = App(root, dnd)
    try:
        scale = root.winfo_fpixels("1i") / 96.0
        if 0.8 <= scale <= 3.0:
            root.tk.call("tk", "scaling", scale * 1.3333)
    except Exception:
        pass

    def on_close():
        try:
            app.save_now()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # 支持把文件/文件夹拖到 EXE 图标上直接打开
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        root.after(300, lambda: app.add_paths(args))

    root.mainloop()


if __name__ == "__main__":
    main()
