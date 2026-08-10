# -*- coding: utf-8 -*-
"""塔科夫离线版mod解压 — 主程序（GUI）。"""
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from tkinterdnd2 import DND_FILES, TkinterDnD

import mod_detector as md
import moddb
from winrar import find_winrar, list_archive, extract_to
from nanazip import find_nanazip
from archive_backend import looks_like_archive, no_window as _no_window

APP_TITLE = "塔科夫离线版mod解压"


KINDS_TO_REJECT = {md.KIND_TRAVERSAL, md.KIND_EMPTY, md.KIND_NOT_ARCHIVE, md.KIND_UNKNOWN}


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "config.json")


def default_config():
    return {"client_root": "", "server_root": "", "winrar_path": "",
            "nanazip_path": "", "mod_manager_enabled": True, "backup_dir": ""}


def load_config():
    cfg = default_config()
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in cfg:
            if k in data:
                cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def detect_server_root(client_root):
    if not client_root or not os.path.isdir(client_root):
        return ""
    if os.path.isdir(os.path.join(client_root, "user", "mods")):
        return client_root
    try:
        with os.scandir(client_root) as it:
            for entry in it:
                if not entry.is_dir():
                    continue
                sub = os.path.join(entry.path, "user", "mods")
                if os.path.isdir(sub):
                    return entry.path
    except OSError:
        pass
    return ""


def parse_drop_data(data):
    """解析拖拽事件数据，返回文件路径列表。"""
    paths = re.findall(r"\{([^{}]*)\}|(\S+)", data)
    return [a or b for a, b in paths]


class ChoiceDialog:
    def __init__(self, title, msg, buttons, ev):
        self.top = tk.Toplevel()
        self.top.title(title)
        self.result = None
        self._ev = ev
        self.top.resizable(False, False)
        self.top.attributes("-topmost", True)
        tk.Label(self.top, text=msg, wraplength=460, justify="left",
                 anchor="w").pack(padx=14, pady=(14, 4), fill="x")
        bar = tk.Frame(self.top)
        bar.pack(pady=10)
        for b in buttons:
            tk.Button(bar, text=b, width=16, command=lambda x=b: self._pick(x)
                      ).pack(side="left", padx=5)
        self.top.protocol("WM_DELETE_WINDOW", self._cancel)
        self.top.transient(self.top.master)
        self.top.grab_set()
        self._center()

    def _center(self):
        self.top.update_idletasks()
        w, h = self.top.winfo_width(), self.top.winfo_height()
        x = (self.top.winfo_screenwidth() - w) // 2
        y = (self.top.winfo_screenheight() - h) // 2
        self.top.geometry("+%d+%d" % (x, y))

    def _pick(self, value):
        self.result = value
        self.top.destroy()
        self._ev.set()

    def _cancel(self):
        self.top.destroy()
        self._ev.set()


class NameDialog(ChoiceDialog):
    def __init__(self, title, msg, buttons, ev, initial=""):
        self.entry = None
        ChoiceDialog.__init__(self, title, msg, buttons, ev)
        self.entry = tk.Entry(self.top, width=40)
        self.entry.insert(0, initial)
        self.entry.pack(padx=14, pady=6)
        self.top.wait_visibility()
        self.entry.focus_set()

    def _pick(self, value):
        self.result = (value, self.entry.get().strip())
        self.top.destroy()
        self._ev.set()


class App:
    def __init__(self, root):
        self.root = root
        self.cfg = load_config()
        self.moddb = moddb.ModDB(os.path.join(app_dir(), "mods_db.json"))
        self.ui_jobs = queue.Queue()
        self.queue = queue.Queue()
        self.worker_active = False
        self.scan_busy = False
        self._backend_cache = None  # (backend, path) 缓存，避免每个包都重新探测

        root.title(APP_TITLE)
        root.geometry("920x640")
        root.minsize(800, 560)
        root.drop_target_register(DND_FILES)
        root.dnd_bind("<<Drop>>", self._on_drop)

        self._build_ui()
        self._load_cfg_to_ui()
        self.root.after(100, self.poll_ui)
        self.root.after(300, self._first_run)
        self.root.after(900, self._first_overview)

    def _first_overview(self):
        self._refresh_overview()
        if not self.moddb.mods and os.path.isdir(self.cfg.get("client_root", "")):
            self.log("首次打开概览：自动扫描已安装 mod…")
            self._scan_mods()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.nb = nb

        self.tab_extract = ttk.Frame(nb)
        self.tab_overview = ttk.Frame(nb)
        self.tab_settings = ttk.Frame(nb)
        nb.add(self.tab_extract, text="  Mod 解压  ")
        nb.add(self.tab_overview, text="  Mod 管理  ")
        nb.add(self.tab_settings, text="  设置  ")
        nb.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._build_extract_tab()
        self._build_overview_tab()
        self._build_settings_tab()

    def _on_tab_changed(self, event):
        if self.nb.index("current") == 1:
            self._refresh_overview()

    def _build_extract_tab(self):
        f = self.tab_extract
        drop = tk.Label(f, text="将 mod 压缩包 (zip / rar / 7z) 拖入此窗口",
                        relief="sunken", bd=2, height=4, font=("Microsoft YaHei", 12),
                        fg="#555555", bg="#f5f5f5")
        drop.pack(fill="x", padx=10, pady=(10, 4))
        self.drop_label = drop

        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=10)
        ttk.Button(bar, text="选择压缩包…", command=self._pick_files).pack(side="left")
        self.lbl_status = ttk.Label(bar, text="就绪")
        self.lbl_status.pack(side="right")

        info = ttk.LabelFrame(f, text="识别结果")
        info.pack(fill="x", padx=10, pady=6)
        self.txt_info = tk.Text(info, height=8, state="disabled", wrap="word",
                                font=("Consolas", 9))
        self.txt_info.pack(fill="x", padx=6, pady=6)

        logf = ttk.LabelFrame(f, text="日志")
        logf.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.txt_log = tk.Text(logf, state="disabled", wrap="word",
                               font=("Consolas", 9))
        sb = ttk.Scrollbar(logf, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        sb.pack(side="right", fill="y", pady=6)

        self.pending = None

    def _build_overview_tab(self):
        f = self.tab_overview
        flt = ttk.Frame(f)
        flt.pack(fill="x", padx=10, pady=(10, 0))
        self.filter_var = tk.StringVar(value="全部")
        self.filter_var.trace_add("write", lambda *a: self._refresh_overview())
        self._filter_radios = {}
        for label in ("全部", "已启用", "已备份", "不可操作"):
            rb = ttk.Radiobutton(flt, text=label, value=label,
                                 variable=self.filter_var)
            rb.pack(side="left", padx=(0, 16))
            self._filter_radios[label] = rb
        bar = ttk.Frame(f)
        bar.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Button(bar, text="扫描更新", command=self._scan_mods).pack(side="left")
        ttk.Button(bar, text="打开游戏目录", command=self._open_game_dir).pack(side="left", padx=6)
        ttk.Label(bar, text="排序:").pack(side="left", padx=(14, 2))
        self.sort_var = tk.StringVar(value="名称")
        self._sort_key_map = {v: k for k, v in moddb.SORT_KEYS.items()}
        ttk.Combobox(bar, textvariable=self.sort_var, state="readonly", width=10,
                     values=list(moddb.SORT_KEYS.values())).pack(side="left")
        self.btn_sort_dir = ttk.Button(bar, text="升序", width=6,
                                       command=self._toggle_sort_dir)
        self.btn_sort_dir.pack(side="left", padx=4)
        self.sort_var.trace_add("write", lambda *a: self._refresh_overview())
        self.sort_desc = False
        self.lbl_summary = ttk.Label(bar, text="")
        self.lbl_summary.pack(side="right")

        act = ttk.Frame(f)
        act.pack(fill="x", padx=10, pady=(2, 2))
        self.btn_fav = ttk.Button(act, text="收藏★", width=9, command=self._toggle_fav)
        self.btn_fav.pack(side="left")
        self.btn_save = ttk.Button(act, text="标记存档类", width=11,
                                   command=lambda: self._run_async(
                                       lambda: self._toggle_save(self._selected_mod_names())))
        self.btn_save.pack(side="left", padx=4)
        self.btn_backup = ttk.Button(act, text="移入备份", width=10,
                                     command=lambda: self._run_async(
                                         lambda: self._backup_mod(self._selected_mod_names())))
        self.btn_backup.pack(side="left", padx=4)
        self.btn_restore = ttk.Button(act, text="还原", width=8,
                                      command=lambda: self._run_async(
                                          lambda: self._restore_mod(self._selected_mod_names())))
        self.btn_restore.pack(side="left", padx=4)
        self.btn_merge = ttk.Button(act, text="合并为一个 Mod", width=12,
                                    command=lambda: self._run_async(
                                        lambda: self._group_mods(self._selected_mod_names())))
        self.btn_merge.pack(side="left", padx=4)
        self.btn_ungroup = ttk.Button(act, text="解除分组", width=9,
                                      command=lambda: self._run_async(
                                          lambda: self._ungroup_mod(self._selected_mod_names())))
        self.btn_ungroup.pack(side="left", padx=4)
        self.lbl_sel = ttk.Label(act, text="未选中")
        self.lbl_sel.pack(side="right")

        cols = ("name", "cat", "ver", "time", "state", "detail")
        self.tree = ttk.Treeview(f, columns=cols, show="tree headings", height=14,
                                 selectmode="extended")
        heads = {"name": ("Mod 名称", 200), "cat": ("分类", 60), "ver": ("版本", 64),
                 "time": ("安装时间", 100), "state": ("状态", 60), "detail": ("路径", 280)}
        for c, (t, w) in heads.items():
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=24, stretch=False)
        vsb = ttk.Scrollbar(f, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(10, 0), pady=4)
        vsb.pack(side="right", fill="y", pady=4)
        cat_colors = {"双端": "#b30000", "客户端": "#0066cc", "服务端": "#0a7a3d",
                      "汉化": "#8a2be2", "其他": "#555555"}
        for cat, color in cat_colors.items():
            self.tree.tag_configure("cat_" + cat, foreground=color)
        self.tree.tag_configure("path", foreground="#666666")
        self.tree.tag_configure("link", foreground="#c00000")
        self.tree.tag_configure("faved", foreground="#c9a100")
        self.tree.tag_configure("crit", foreground="#b30000")
        self.tree.tag_configure("backed", foreground="#777777")
        self.tree.tag_configure("group", foreground="#333333",
                                font=("Microsoft YaHei", 9, "bold"))
        self.tree.bind("<Double-1>", self._on_tree_dbl)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_sel_buttons())
        tk.Label(f, text="提示：双击条目打开位置；选中两个 Mod 可合并为一组（组节点可整体选中，再与其他 Mod 合并）；移入备份需游戏未运行",
                 fg="#888888").pack(pady=(0, 8))
        self._tree_mods = {}
        self._tree_groups = {}
        self._tree_paths = {}
        self._update_sel_buttons()

    def _build_settings_tab(self):
        f = self.tab_settings
        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=16, pady=16)
        grid.columnconfigure(1, weight=1)

        def row(r, label, key):
            ttk.Label(grid, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=6)
            var = tk.StringVar()
            ent = ttk.Entry(grid, textvariable=var)
            ent.grid(row=r, column=1, sticky="ew", padx=4, pady=6)
            self.cfg_vars[key] = var
            ttk.Button(grid, text="浏览…",
                       command=lambda k=key: self._browse(k)).grid(row=r, column=2, padx=4)

        self.cfg_vars = {}
        row(0, "客户端根目录（BepInEx 所在）：", "client_root")
        row(1, "服务端根目录（user/mods 所在）：", "server_root")
        row(2, "WinRAR 程序路径：", "winrar_path")
        row(3, "NanaZip 程序路径（可选，优先使用）：", "nanazip_path")
        row(4, "Mod 备份文件夹：", "backup_dir")
        ttk.Label(grid, text="留空 NanaZip 时回退 WinRAR；填了 NanaZip 则全格式优先用 NanaZip。",
                  foreground="#777").grid(row=5, column=0, columnspan=3, sticky="w", padx=4)

        mgr = ttk.Frame(f)
        mgr.pack(fill="x", padx=16)
        self.var_manager = tk.BooleanVar(value=True)
        ttk.Checkbutton(mgr, text="启用「禁用 Mod」功能（移入备份/还原）",
                        variable=self.var_manager).pack(side="left")
        ttk.Button(mgr, text="设为默认（游戏根\\ModBackup）",
                   command=self._set_default_backup).pack(side="left", padx=12)

        bar = ttk.Frame(f)
        bar.pack(padx=16, pady=8)
        ttk.Button(bar, text="自动检测全部", command=self._auto_detect_all).pack(side="left")
        ttk.Button(bar, text="保存设置", command=self._save_settings).pack(side="left", padx=8)
        tk.Label(f, text="设置保存在程序旁的 config.json 中。",
                 fg="#888888").pack(padx=16, pady=(0, 16))

    def _set_default_backup(self):
        self.cfg_vars["backup_dir"].set(
            os.path.join(self.cfg_vars["client_root"].get().strip() or ".",
                         "ModBackup"))

    def _load_cfg_to_ui(self):
        for k, var in self.cfg_vars.items():
            var.set(self.cfg.get(k, ""))
        self.var_manager.set(bool(self.cfg.get("mod_manager_enabled", True)))
        self._apply_manager_visibility()

    def _apply_manager_visibility(self):
        enabled = self.var_manager.get()
        if hasattr(self, "btn_fav"):
            state = "normal" if enabled else "disabled"
            self.btn_backup.configure(state=state)
            self.btn_restore.configure(state=state)

    def _first_run(self):
        cfg = self.cfg
        if not os.path.isdir(cfg.get("client_root", "")):
            msg = ("首次使用，请选择游戏根目录\n"
                   "（即包含 EscapeFromTarkov.exe / BepInEx 文件夹的目录）")
            self.log("首次运行：请配置游戏目录")
            root = self.root
            answer = [False]

            def ask():
                path = filedialog.askdirectory(parent=root, title="选择游戏根目录")
                if path:
                    cfg["client_root"] = path
                    cfg["server_root"] = detect_server_root(path) or path
                    cfg["winrar_path"] = cfg["winrar_path"] or find_winrar() or ""
                    cfg["nanazip_path"] = cfg.get("nanazip_path") or find_nanazip() or ""
                    if not cfg.get("backup_dir"):
                        cfg["backup_dir"] = os.path.join(path, "ModBackup")
                    save_config(cfg)
                    self._load_cfg_to_ui()
                    answer[0] = True
                    self.log("已配置：客户端根=%s" % path)
                    if cfg["server_root"] and cfg["server_root"] != path:
                        self.log("自动检测服务端根=%s" % cfg["server_root"])
                elif (not os.path.isdir(cfg.get("winrar_path", "")) and not find_winrar()
                      and not find_nanazip()):
                    messagebox.showwarning(
                        APP_TITLE, "未找到 NanaZip 或 WinRAR，请在设置中手动指定路径。")
            self._ui_call(ask)

    # ------------------------------------------------------- 通用工具
    def log(self, msg):
        ts = time.strftime("%H:%M:%S")

        def append():
            self.txt_log.configure(state="normal")
            self.txt_log.insert("end", "[%s] %s\n" % (ts, msg))
            self.txt_log.see("end")
            self.txt_log.configure(state="disabled")
        self._ui_call(append)
        if not getattr(sys, "frozen", False):
            print("[%s] %s" % (ts, msg))

    def _set_status(self, text):
        """更新状态栏标签（线程安全，交由 UI 线程执行）。"""
        self._ui_call(lambda: self.lbl_status.configure(text=text))

    def _ui_call(self, fn):
        self.ui_jobs.put(fn)

    def poll_ui(self):
        while True:
            try:
                fn = self.ui_jobs.get_nowait()
            except queue.Empty:
                break
            try:
                fn()
            except Exception:
                traceback.print_exc()
        self.root.after(100, self.poll_ui)

    def ask_choice(self, title, msg, buttons):
        ev = threading.Event()
        ref = {}

        def show():
            ref["dlg"] = ChoiceDialog(title, msg, buttons, ev)
        self._ui_call(show)
        # 等待对话框创建（窗口仍在时轮询），创建后等用户应答（最多 60s）。
        deadline = time.time() + 60
        while "dlg" not in ref and self.root.winfo_exists() and time.time() < deadline:
            ev.wait(0.05)
        ev.wait(max(0, deadline - time.time()))
        return ref["dlg"].result if "dlg" in ref else None

    def ask_name(self, title, msg, buttons, initial=""):
        ev = threading.Event()
        ref = {}

        def show():
            ref["dlg"] = NameDialog(title, msg, buttons, ev, initial)
        self._ui_call(show)
        deadline = time.time() + 60
        while "dlg" not in ref and self.root.winfo_exists() and time.time() < deadline:
            ev.wait(0.05)
        ev.wait(max(0, deadline - time.time()))
        return ref["dlg"].result if "dlg" in ref else None


    # ------------------------------------------------------- 拖拽/选择
    def _on_drop(self, event):
        paths = [p for p in parse_drop_data(event.data) if looks_like_archive(p)]
        self._enqueue(paths)

    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 mod 压缩包", filetypes=[("压缩包", "*.zip *.rar *.7z")])
        self._enqueue(paths)

    def _enqueue(self, paths):
        if not paths:
            return
        for p in paths:
            self.queue.put(p)
            self.log("已加入队列：%s" % p)
        if not self.worker_active:
            self.worker_active = True
            threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        while True:
            try:
                path = self.queue.get_nowait()
            except queue.Empty:
                break
            try:
                self._process_one(path)
            except Exception:
                err = traceback.format_exc()
                self.log("处理出错：\n%s" % err)
        self.worker_active = False

    def _resolve_backend(self):
        """解析解压后端，缓存结果。NanaZip 全格式优先，回退 WinRAR。
        返回 (backend, path) 或 None（找不到）。设置变更后通过 _clear_backend_cache 失效。"""
        if self._backend_cache is not None:
            backend, path = self._backend_cache
            if os.path.isfile(path):
                return backend, path
        cfg = self.cfg
        nanazip = cfg.get("nanazip_path") or find_nanazip()
        if nanazip and os.path.isfile(nanazip):
            backend, path = "nanazip", nanazip
        else:
            winrar = cfg.get("winrar_path") or find_winrar()
            if not winrar or not os.path.isfile(winrar):
                return None
            backend, path = "winrar", winrar
        self._backend_cache = (backend, path)
        # 探测到的路径回写 cfg 并持久化，避免下次重复探测
        if backend == "nanazip" and not cfg.get("nanazip_path"):
            cfg["nanazip_path"] = path
            save_config(cfg)
        elif backend == "winrar" and not cfg.get("winrar_path"):
            cfg["winrar_path"] = path
            save_config(cfg)
        return backend, path

    def _clear_backend_cache(self):
        self._backend_cache = None

    def _process_one(self, archive):
        cfg = self.cfg
        self.log("=" * 50)
        self.log("处理：%s" % os.path.basename(archive))
        self._set_status("分析中…")

        resolved = self._resolve_backend()
        if not resolved:
            self._show_error("未找到 NanaZip 或 WinRAR\n请在「设置」页配置解压程序路径。")
            return
        backend, backend_path = resolved
        nanazip = backend_path if backend == "nanazip" else None
        if not looks_like_archive(archive):
            self._show_error("「%s」不是受支持的压缩包（仅支持 zip / rar / 7z）。" % os.path.basename(archive))
            return

        try:
            a = md.analyze_archive(backend_path, archive,
                                   nanazip_path=(nanazip if backend == "nanazip" else None))
        except RuntimeError as exc:
            self._show_error("分析失败：%s" % exc)
            return
        except Exception:
            self._show_error("分析出错：\n%s" % traceback.format_exc())
            return

        staging = None
        try:
            if a.kind == md.KIND_NEEDS_STAGING:
                self.log("无法直接预览内容，先解压到临时目录检查…")
                self._set_status("临时解压检查中…")
                staging = tempfile.mkdtemp(prefix="mod_extract_")
                try:
                    ok = self._run_extract(backend, backend_path, archive, staging)
                    if not ok:
                        return
                    entries = md.walk_dir_entries(staging)
                    a = md.analyze_entries(md.Analysis(archive=archive), entries)
                    a.direct_extract = False
                    a.issue = (a.issue + "；" if a.issue else "") + "内容来自临时解压检查"
                    if not entries:
                        self._show_error("压缩包解压后没有找到任何文件，已中止。")
                        return
                except Exception:
                    self._show_error("临时解压检查出错：\n%s" % traceback.format_exc())
                    return

            self._show_analysis(a)

            if a.kind in KINDS_TO_REJECT:
                self._show_error("已中止：%s\n\n压缩包内容将不会被解压到任何位置。" %
                                 md.KIND_NAMES.get(a.kind, a.kind))
                return
            if a.kind == md.KIND_MIXED:
                self._show_error("已中止：压缩包结构无法自动判断，为避免解压到错误位置，请手动解压后确认。")
                return

            if a.kind == md.KIND_COMBO:
                missing = [r for r in ("client_root", "server_root") if not os.path.isdir(cfg.get(r, ""))]
                if missing:
                    self._show_error("游戏目录不存在：%s\n请在「设置」页重新配置。" %
                                     " / ".join(cfg.get(r, "") for r in missing))
                    return
            elif not a.target_root or not os.path.isdir(cfg.get(a.target_root + "_root", "")):
                self._show_error("游戏目录不存在：%s\n请在「设置」页重新配置。" %
                                 cfg.get(a.target_root + "_root", ""))
                return
            if a.needs_confirm and a.confirm_choices:
                choice = self.ask_choice("确认安装方式", self._confirm_msg(a), a.confirm_choices)
                if choice is None or choice == "取消" or choice.startswith("取消"):
                    self._notice("已取消。")
                    return
                self._apply_choice(a, choice)

            md.resolve_targets(cfg, a)
            self.log("目标目录：%s" % a.target_dir)
            self._set_status("检查目标…")

            keep_links = False
            target = a.target_dir
            links = []
            if a.internal_root and os.path.lexists(target):
                if md.is_link(target):
                    links = [target]
                else:
                    links = md.find_links_under(target)
            if links:
                self.log("发现软链接 %d 处：%s" % (len(links), links[0]))
                choice = self.ask_choice(
                    "软链接",
                    "「%s」是软链接 → %s\n\n转为常规 mod？"
                    % (os.path.basename(target), md.link_target(links[0])),
                    ["转常规 mod", "保留链接", "取消"])
                if choice is None or choice == "取消":
                    self._notice("已取消。")
                    return
                if choice == "转常规 mod":
                    for l in links:
                        md.remove_link(l)
                        self.log("已删除软链接：%s" % l)
                else:
                    keep_links = True
                    self.log("保留软链接，解压内容将写入其指向的目录。")

            # target 是共享 mod 容器目录（BepInEx/plugins）而非单个 mod 私有目录时，
            # 绝不能整体删除——里面装着其他 mod。直接按文件合并解压覆盖同名文件即可。
            _ir = os.path.normpath(a.internal_root).lower() if a.internal_root else ""
            shared_container = _ir == os.path.normpath(os.path.join("bepinex", "plugins")).lower()
            exists = a.internal_root and (os.path.exists(target) or md.is_link(target)) \
                and not keep_links and not shared_container
            if exists:
                choice = self.ask_choice("覆盖", "目标已存在：\n%s\n\n删除后重新安装？" % target,
                                         ["覆盖", "取消"])
                if choice is None or choice != "覆盖":
                    self._notice("已取消，未做任何修改。")
                    return
                self._delete_target(target)
            elif shared_container and a.internal_root and os.path.lexists(target):
                self.log("目标为共享插件目录，按文件覆盖（不删除其他插件）：%s" % target)

            self._set_status("解压中…")
            ok = self._extract(a, archive, backend, backend_path, keep_links, staging)
            if ok:
                self._set_status("完成")
                self.log("✓ 完成：%s → %s" % (os.path.basename(archive), a.target_dir))
                self._cleanup_storage(a)
                self._record_install(a, archive)
            else:
                self._set_status("失败")
        finally:
            if staging:
                shutil.rmtree(staging, ignore_errors=True)

    def _installed_side_names(self, a):
        """返回本次安装涉及的 (端, mod名) 列表，用于清理 storage。"""
        pairs = []
        lc = [e.lower() for e in a.entries]
        if a.kind in (md.KIND_SERVER, md.KIND_COMBO, md.KIND_SERVER_ROOT):
            for e, l in zip(a.entries, lc):
                p = e.split("/")
                if l.startswith("user/mods/") and len(p) >= 3:
                    pairs.append(("server", p[2]))
                elif l.startswith("spt/user/mods/") and len(p) >= 4:
                    pairs.append(("server", p[3]))
                elif l.startswith("spt_runtime/user/mods/") and len(p) >= 4:
                    pairs.append(("server", p[3]))
        if a.kind in (md.KIND_CLIENT, md.KIND_COMBO):
            for e, l in zip(a.entries, lc):
                p = e.split("/")
                if l.startswith("bepinex/plugins/") and len(p) >= 3 \
                        and not p[2].lower().endswith(".dll"):
                    pairs.append(("client", p[2]))
        if a.kind in (md.KIND_SERVER_NAMED, md.KIND_SERVER_LOOSE):
            pairs.append(("server", a.mod_name))
        if a.kind == md.KIND_CLIENT_FOLDER:
            pairs.append(("client", a.mod_name))
        seen, out = set(), []
        for side, name in pairs:
            if name and (side, name.lower()) not in seen:
                seen.add((side, name.lower()))
                out.append((side, name))
        return out

    def _cleanup_storage(self, a):
        """更新安装后，移除 mods_storage 中的旧副本，保留正常安装版本。
        若安装结果仍是软链接则保留（链接指向 storage）。"""
        cfg = self.cfg
        for side, name in self._installed_side_names(a):
            if side == "client":
                installed = os.path.join(cfg["client_root"], "BepInEx", "plugins", name)
                storage = os.path.join(cfg["client_root"], "BepInEx", "mods_storage",
                                       "plugins", name)
            else:
                installed = os.path.join(cfg["server_root"], "user", "mods", name)
                storage = os.path.join(cfg["server_root"], "user", "mods_storage", name)
            if not os.path.exists(storage):
                continue
            if md.is_link(installed):
                self.log("保留 storage 副本（安装结果仍是软链接）：%s" % storage)
                continue
            try:
                if os.path.isdir(storage):
                    shutil.rmtree(storage)
                else:
                    os.remove(storage)
                self.log("已移除 mods_storage 旧副本：%s" % storage)
            except OSError as exc:
                self.log("移除 storage 副本失败：%s：%s" % (storage, exc))

    def _installed_paths(self, a):
        """根据安装结果计算本次写入的路径列表。"""
        cfg = self.cfg
        lc = [e.lower() for e in a.entries]
        if a.kind == md.KIND_COMBO:
            paths = []
            for e, l in zip(a.entries, lc):
                if l.startswith("bepinex/plugins/"):
                    paths.append(os.path.join(cfg["client_root"], *e.split("/")[:3]))
                elif l.startswith("spt/user/mods/"):
                    paths.append(os.path.join(cfg["server_root"], "user", "mods", e.split("/")[3]))
                elif l.startswith("user/mods/"):
                    paths.append(os.path.join(cfg["server_root"], *e.split("/")[:3]))
                elif l.startswith("mods/"):
                    paths.append(os.path.join(cfg["server_root"], "mods", e.split("/")[1]))
            return sorted(set(paths))
        if a.kind == md.KIND_SERVER_ROOT:
            paths = []
            for e in a.entries:
                parts = e.split("/")
                if len(parts) >= 4 and parts[1].lower() == "user" and parts[2].lower() == "mods":
                    paths.append(os.path.join(cfg["server_root"], "user", "mods", parts[3]))
            return sorted(set(paths)) or [cfg["server_root"]]
        if a.kind == md.KIND_SERVER and not a.internal_root:
            paths = []
            for e in a.entries:
                parts = e.split("/")
                if len(parts) >= 3 and parts[1].lower() == "mods":
                    paths.append(os.path.join(cfg["server_root"], "user", "mods", parts[2]))
            return sorted(set(paths))
        if a.target_dir:
            return [a.target_dir]
        return [cfg.get(a.target_root + "_root", "")]

    def _record_install(self, a, archive):
        stem = os.path.splitext(os.path.basename(archive))[0]
        name = a.mod_name or re.sub(r"[._\- ]*v?\d+\.\d+(\.\d+)*$", "", stem).strip("._- ") or stem
        paths = self._installed_paths(a)
        version = moddb.version_from_text(stem)
        if not version:
            for p in paths:
                if os.path.isdir(p):
                    version = moddb.version_from_dir(p)
                    if version:
                        break
        self.moddb.upsert(name, paths, self.cfg, version=version, source="install")
        if self.moddb.last_error:
            self.log("⚠ 记录已更新但写入数据库失败：%s" % self.moddb.last_error)
        self._ui_call(self._refresh_overview)
        self.log("已记录到 mod 列表：%s（%s）" % (name, self.moddb.get(name)["category"]))

    def _show_analysis(self, a):
        lines = ["类型：%s" % md.KIND_NAMES.get(a.kind, a.kind)]
        if a.mod_name:
            lines.append("mod：%s" % a.mod_name)
        if a.issue:
            lines.append("注意：%s" % a.issue)
        if a.encrypted:
            lines.append("注意：可能已加密（解压时会要求输入密码）")
        lines += md.structure_brief(a.entries, a.kind, a.mod_name)
        self._show_info(lines)

    def _confirm_msg(self, a):
        if a.kind == md.KIND_COMBO:
            return "客户端 → BepInEx\n服务端 → user\\mods（去 SPT/ 或 SPT_Runtime/ 前缀）\n\n安装？"
        if a.kind == md.KIND_CLIENT_FOLDER:
            return "「%s」装到哪里？" % a.mod_name
        if a.kind == md.KIND_SERVER_LOOSE:
            return "%s.dll + config.json\n→ user\\mods\\%s\n\n安装？" % (a.mod_name, a.mod_name)
        return "请选择处理方式："

    def _apply_choice(self, a, choice):
        if a.kind == md.KIND_CLIENT_FOLDER and choice == "服务端":
            a.target_root = "server"
            a.internal_root = os.path.join("user", "mods", a.mod_name)
        elif a.kind == md.KIND_SERVER_LOOSE and choice == "改名":
            result = self.ask_name("改名", "新的 mod 名称：", ["确定", "取消"],
                                   initial=a.mod_name)
            if result and result[0] == "确定" and result[1]:
                a.mod_name = result[1]
            a.internal_root = os.path.join("user", "mods", a.mod_name)

    def _delete_target(self, target):
        try:
            if os.path.islink(target) or os.path.isjunction(target):
                md.remove_link(target)
                return
            if os.path.isfile(target) or os.path.isdir(target):
                if os.path.isdir(target):
                    shutil.rmtree(target)
                else:
                    os.remove(target)
            self.log("已删除旧内容：%s" % target)
        except OSError as exc:
            self.log("删除旧内容失败：%s" % exc)

    def _extract(self, a, archive, backend, backend_path, keep_links, staging=None):
        root = self.cfg.get(a.target_root + "_root", "")
        if a.direct_extract:
            return self._run_extract(backend, backend_path, archive, root)
        own = staging is None
        if own:
            staging = tempfile.mkdtemp(prefix="mod_extract_")
        try:
            ok = self._run_extract(backend, backend_path, archive, staging)
            if not ok:
                return False
            self._merge_staged(staging, a)
            return True
        finally:
            # 无论自建还是外部传入，异常/成功都清理 staging（幂等）。
            shutil.rmtree(staging, ignore_errors=True)

    def _run_extract(self, backend, backend_path, archive, dest_root):
        if backend == "nanazip":
            from nanazip import extract_to as nz_extract
            ok, msg = nz_extract(backend_path, archive, dest_root)
        else:
            ok, msg = extract_to(backend_path, archive, dest_root)
        if not ok:
            self._show_error("解压失败：%s" % msg)
            self.log("解压失败：%s" % msg)
        return ok

    def _merge_staged(self, staging, a):
        for top in getattr(a, "strip_prefixes", None) or []:
            staging = os.path.join(staging, top)
        if a.kind == md.KIND_COMBO:
            for top in os.listdir(staging):
                src = os.path.join(staging, top)
                low = top.lower()
                if low == "bepinex":
                    self._merge_tree(src, os.path.join(self.cfg["client_root"], top))
                elif low in md.SERVER_ROOT_PROXIES:
                    # SPT/ 或 SPT_Runtime/：内容整体并入服务端根（剥离该前缀）
                    self._merge_tree(src, self.cfg["server_root"])
                elif low in ("user", "mods"):
                    self._merge_tree(src, os.path.join(self.cfg["server_root"], top))
                elif low in md.CLIENT_ROOT_SIBLINGS:
                    # EscapeFromTarkov_Data 等：作为客户端根同级目录原样并入
                    self._merge_tree(src, os.path.join(self.cfg["client_root"], top))
                else:
                    self.log("忽略顶层非游戏文件：%s" % top)
            self.log("已移动 → 客户端根 + 服务端根")
            return
        if a.kind == md.KIND_SERVER_ROOT:
            for top in os.listdir(staging):
                if top.lower() in md.SERVER_ROOT_PROXIES:
                    self._merge_tree(os.path.join(staging, top), self.cfg["server_root"])
            self.log("已移动 → %s" % self.cfg["server_root"])
            return
        if not a.internal_root:
            srcs = [os.path.join(staging, t) for t in os.listdir(staging)]
        elif a.kind in (md.KIND_SERVER_NAMED, md.KIND_CLIENT_FOLDER):
            srcs = [os.path.join(staging, a.mod_name)]
        elif a.kind in (md.KIND_SERVER_LOOSE, md.KIND_CLIENT_LOOSE):
            srcs = [os.path.join(staging, t) for t in os.listdir(staging)]
        else:
            srcs = [os.path.join(staging, *a.internal_root.split("/"))]
        for src in srcs:
            if os.path.exists(src):
                self._merge_tree(src, a.target_dir)
        self.log("已移动 → %s" % a.target_dir)

    def _merge_tree(self, src, dst):
        if os.path.isdir(src):
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)

    # ------------------------------------------------------- 概览页
    def _scan_mods(self):
        if self.scan_busy:
            return
        self.scan_busy = True
        if hasattr(self, "lbl_summary"):
            self.lbl_summary.configure(text="扫描中…")

        def work():
            try:
                entries = md.scan_installed_mods(self.cfg)
                by_name = {}
                for e in entries:
                    by_name.setdefault(e.name, []).append(e)
                for name, group in by_name.items():
                    paths = [e.path for e in group]
                    rec = self.moddb.get(name)
                    if rec:
                        rec["paths"] = sorted(set(list(rec.get("paths", [])) + paths))
                        rec["category"] = moddb.categorize(name, rec["paths"], self.cfg)
                    else:
                        version = moddb.version_from_text(name)
                        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(
                            max((os.path.getmtime(p) for p in paths if os.path.exists(p)),
                                default=time.time())))
                        self.moddb.mods[name] = {
                            "name": name,
                            "category": moddb.categorize(name, paths, self.cfg),
                            "version": version,
                            "install_time": mtime,
                            "source": "scan",
                            "paths": sorted(set(paths)),
                        }
                # 仅当游戏根目录实际可达时才按"路径不存在"清理记录；
                # 根目录不可达（断网盘/未挂载）时保留记录，避免误删全部数据。
                roots_reachable = all(os.path.isdir(self.cfg.get(r, ""))
                                      for r in ("client_root", "server_root")
                                      if self.cfg.get(r))
                for name, rec in list(self.moddb.mods.items()):
                    if rec.get("state") != "backed_up":
                        kept = sorted(set(p for p in rec.get("paths", [])
                                          if os.path.exists(p)))
                        rec["paths"] = kept
                        if not kept and not rec.get("backup_paths") and roots_reachable:
                            del self.moddb.mods[name]
                self.moddb.save()
                if self.moddb.last_error:
                    self.log("⚠ 扫描结果已更新但写入数据库失败：%s" % self.moddb.last_error)
                self._ui_call(self._refresh_overview)
            except Exception:
                self.log("扫描出错：\n%s" % traceback.format_exc())
                if hasattr(self, "lbl_summary"):
                    self._ui_call(lambda: self.lbl_summary.configure(text="扫描出错"))
            finally:
                self.scan_busy = False

        threading.Thread(target=work, daemon=True).start()

    def _filter_records(self, flt):
        """按分类标签过滤记录（全部/已启用/已备份/不可操作）。"""
        if hasattr(self, "sort_var"):
            sort_by = self._sort_key_map.get(self.sort_var.get(), "name")
            sort_desc = self.sort_desc
        else:
            sort_by, sort_desc = "name", False
        out = []
        for name, rec in self.moddb.sorted_all(sort_by=sort_by, descending=sort_desc):
            state = rec.get("state", "active")
            crit = bool(rec.get("save_critical"))
            if flt == "已启用" and state == "backed_up":
                continue
            if flt == "已备份" and state != "backed_up":
                continue
            if flt == "不可操作" and not crit:
                continue
            out.append((name, rec))
        return out

    def _refresh_overview(self):
        self.tree.delete(*self.tree.get_children())
        self._tree_mods = {}
        self._tree_groups = {}
        self._tree_paths = {}
        c = self.moddb.counts()
        total = c["总数"]
        active = total - c["已备份"]
        crit = sum(1 for r in self.moddb.mods.values() if r.get("save_critical"))
        labels = {"全部": total, "已启用": active, "已备份": c["已备份"], "不可操作": crit}
        for label, num in labels.items():
            self._filter_radios[label].configure(text="%s (%d)" % (label, num))
        filtered = self._filter_records(self.filter_var.get())
        groups = {}
        ungrouped = []
        for name, rec in filtered:
            g = rec.get("group", "")
            if g:
                groups.setdefault(g, []).append((name, rec))
            else:
                ungrouped.append((name, rec))
        for gname in sorted(groups):
            members = groups[gname]
            all_paths = []
            for _n, r in members:
                all_paths.extend(r.get("paths", []))
            gcat = moddb.categorize(gname, all_paths, self.cfg)
            marks = []
            if any(r.get("favorite") for _n, r in members):
                marks.append("★")
            if any(r.get("save_critical") for _n, r in members):
                marks.append("🔒")
            backed_n = sum(1 for _n, r in members if r.get("state") == "backed_up")
            if backed_n:
                marks.append("已备份" if backed_n == len(members)
                             else "已备份 %d/%d" % (backed_n, len(members)))
            giid = self.tree.insert("", "end", open=True,
                                    values=(gname, gcat, "-", "-",
                                            " ".join(marks) if marks else "-",
                                            "组内 %d 个 Mod" % len(members)),
                                    tags=("cat_" + gcat, "group"))
            self._tree_groups[giid] = gname
            first_path = ""
            for name, rec in members:
                self._insert_mod_row(giid, name, rec)
                if not first_path and rec.get("paths"):
                    first_path = rec["paths"][0]
            self._tree_paths[giid] = (first_path, True)
        for name, rec in ungrouped:
            self._insert_mod_row("", name, rec)
        self.lbl_summary.configure(
            text="共 %d 个 mod · 双端 %d · 客户端 %d · 服务端 %d · 汉化 %d · 其他 %d · 已备份 %d · 分组 %d"
                 % (c["总数"], c["双端"], c["客户端"], c["服务端"], c["汉化"], c["其他"],
                    c["已备份"], len(groups)))
        self._update_sel_buttons()

    def _insert_mod_row(self, parent, name, rec):
        cat = rec.get("category", "其他")
        ver = rec.get("version", "") or "-"
        itime = rec.get("install_time", "") or "-"
        backed = rec.get("state") == "backed_up"
        marks = []
        if rec.get("favorite"):
            marks.append("★")
        if rec.get("save_critical"):
            marks.append("🔒")
        if backed:
            marks.append("已备份")
        state_txt = " ".join(marks) if marks else "-"
        tags = ["cat_" + cat]
        if rec.get("favorite"):
            tags.append("faved")
        if rec.get("save_critical"):
            tags.append("crit")
        if backed:
            tags.append("backed")
        npaths = len(rec.get("paths", []))
        iid = self.tree.insert(parent, "end", open=True,
                               values=(name, cat, ver, itime, state_txt,
                                       "%d 个位置" % npaths),
                               tags=tuple(tags))
        self._tree_mods[iid] = name
        show = rec.get("backup_paths") if backed else rec.get("paths", [])
        for item in show:
            if isinstance(item, dict):
                p = item.get("bak", "")
                src_txt = " ← %s" % item.get("src", "")
            else:
                p = item
                src_txt = ""
            is_link = md.is_link(p)
            loc = self._loc_label(p)
            detail = loc + p + src_txt + (("  → %s" % md.link_target(p)) if is_link else "")
            tags = ("link",) if is_link else ("path",)
            p_iid = self.tree.insert(iid, "end",
                                     values=("", "", "", "", "", detail), tags=tags)
            self._tree_paths[p_iid] = (p, os.path.isdir(p) if os.path.exists(p) else False)
        self._tree_paths[iid] = (rec.get("paths", [None])[0] or "", True)

    def _selected_mod_names(self):
        """返回选中的 Mod 名单；选中组节点时展开为组内全部成员。"""
        names = []
        for iid in self.tree.selection():
            if iid in self._tree_mods:
                names.append(self._tree_mods[iid])
            elif iid in self._tree_groups:
                for n in self._group_members(self._tree_groups[iid]):
                    if n not in names:
                        names.append(n)
        return names

    def _group_members(self, gname):
        return sorted(n for n, r in self.moddb.mods.items()
                      if r.get("group") == gname)

    def _update_sel_buttons(self):
        enabled = bool(self.cfg.get("mod_manager_enabled", True))
        names = self._selected_mod_names()
        sel = self.tree.selection()
        group_sel = bool(sel) and any(i in self._tree_groups for i in sel)
        n = len(names)
        if n == 1:
            rec = self.moddb.get(names[0])
            backed = rec and rec.get("state") == "backed_up"
            self.btn_backup.configure(state="disabled" if (backed or not enabled) else "normal")
            self.btn_restore.configure(state="normal" if (backed and enabled) else "disabled")
            self.btn_fav.configure(state="normal")
            self.btn_save.configure(state="normal")
            self.btn_ungroup.configure(
                state="normal" if self.moddb.get_group(names[0]) else "disabled")
            self.lbl_sel.configure(text=names[0])
        elif group_sel and n > 1:
            all_backed = all((self.moddb.get(x) or {}).get("state") == "backed_up"
                             for x in names)
            any_backed = any((self.moddb.get(x) or {}).get("state") == "backed_up"
                             for x in names)
            self.btn_backup.configure(
                state="disabled" if (all_backed or not enabled) else "normal")
            self.btn_restore.configure(
                state="normal" if (any_backed and enabled) else "disabled")
            self.btn_fav.configure(state="normal")
            self.btn_save.configure(state="normal")
            self.btn_ungroup.configure(state="disabled")
            gid = next((i for i in sel if i in self._tree_groups), None)
            self.lbl_sel.configure(text="组「%s」(%d 个)" % (self._tree_groups.get(gid, ""), n))
        else:
            self.btn_backup.configure(state="disabled")
            self.btn_restore.configure(state="disabled")
            self.btn_fav.configure(state="disabled")
            self.btn_save.configure(state="disabled")
            self.btn_ungroup.configure(state="disabled")
            self.lbl_sel.configure(text="%d 个选中" % n)
        self.btn_merge.configure(state="normal" if n >= 2 else "disabled")

    def _toggle_sort_dir(self):
        self.sort_desc = not self.sort_desc
        self.btn_sort_dir.configure(text="降序" if self.sort_desc else "升序")
        self._refresh_overview()

    def _toggle_fav(self, names=None):
        if not names:
            names = self._selected_mod_names()
        if not names:
            return
        all_fav = all((self.moddb.get(n) or {}).get("favorite") for n in names)
        self.moddb.begin_batch()
        try:
            for n in names:
                self.moddb.set_favorite(n, not all_fav)
        finally:
            self.moddb.flush()
        self._refresh_overview()

    def _toggle_save(self, names=None):
        if not names:
            names = self._selected_mod_names()
        if not names:
            return
        if len(names) == 1:
            name = names[0]
            rec = self.moddb.get(name)
            if not (rec and rec.get("save_critical")):
                choice = self.ask_choice("标记存档类",
                                         "标记「%s」为修改存档的 mod？\n标记后将无法移入备份（避免破坏存档）。"
                                         % name, ["标记", "取消"])
                if choice != "标记":
                    return
            self.moddb.set_save_critical(name, not (rec and rec.get("save_critical")))
        else:
            all_crit = all((self.moddb.get(n) or {}).get("save_critical") for n in names)
            if all_crit:
                choice = self.ask_choice("取消存档标记",
                                         "取消整组 %d 个 Mod 的存档标记？" % len(names),
                                         ["取消标记", "保留"])
                if choice != "取消标记":
                    return
            else:
                choice = self.ask_choice("标记存档类",
                                         "将整组 %d 个 Mod 标记为存档类？\n标记后将无法移入备份。"
                                         % len(names), ["整组标记", "取消"])
                if choice != "整组标记":
                    return
            self.moddb.begin_batch()
            try:
                for n in names:
                    self.moddb.set_save_critical(n, not all_crit)
            finally:
                self.moddb.flush()
        self._ui_call(self._refresh_overview)

    def _loc_label(self, path):
        roots = []
        for key, label in (("server_root", "[服务端] "), ("client_root", "[客户端] ")):
            root = (self.cfg.get(key) or "").lower().rstrip("\\/")
            if root:
                roots.append((root, label))
        roots.sort(key=lambda x: -len(x[0]))
        pl = path.lower()
        for root, label in roots:
            if pl.startswith(root):
                return label
        return "[其他] "

    def _on_tree_dbl(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        entry = self._tree_paths.get(iid)
        if not entry:
            return
        path, _ = entry
        if not path:
            return
        target = path if os.path.isdir(path) else os.path.dirname(path)
        if target and os.path.exists(target):
            try:
                os.startfile(target)
            except OSError:
                pass

    def _open_game_dir(self):
        path = self.cfg.get("client_root", "")
        if os.path.isdir(path):
            os.startfile(path)

    # ------------------------------------------------------- Mod 管理
    def _run_async(self, fn):
        """在工作线程执行管理操作（避免对话框阻塞主线程导致界面卡死）。"""
        if self.op_busy:
            self._notice("上一个操作正在执行，请稍候…")
            return
        self.op_busy = True

        def wrapped():
            try:
                fn()
            except Exception:
                self.log("操作出错：\n%s" % traceback.format_exc())
                self._show_error("操作出错，请查看日志。")
            finally:
                self.op_busy = False
        threading.Thread(target=wrapped, daemon=True).start()

    def _running_games(self):
        """返回正在运行的游戏相关进程名列表（空=未运行）。"""
        running = []
        try:
            r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True,
                               timeout=30, creationflags=_no_window())
            names = set()
            for line in r.stdout.decode("gbk", "replace").splitlines():
                if '"' in line:
                    names.add(line.split('"')[1].strip().lower())
        except Exception:
            return []
        for proc in ("EscapeFromTarkov.exe", "SPT.Server.exe"):
            if proc.lower() in names:
                running.append(proc)
        return running

    def _ensure_manager(self):
        if not self.cfg.get("mod_manager_enabled", True):
            self._notice("「禁用 Mod」功能未启用，请在「设置」页打开。")
            return False
        running = self._running_games()
        if running:
            self._show_error("游戏正在运行：%s\n\n请先完全退出游戏和服务端，再进行此操作。" %
                             "、".join(running))
            return False
        return True

    def _backup_dir(self, create=True):
        d = self.cfg.get("backup_dir") or os.path.join(self.cfg["client_root"], "ModBackup")
        if create:
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as exc:
                self._show_error("无法创建备份文件夹：%s\n%s" % (d, exc))
                return ""
        return d

    def _convert_link_to_real(self, path):
        """把软链接/联接转为常规文件（复制真实内容到目标位置后删除链接）。"""
        target = md.link_target(path)
        if not target or not os.path.exists(target):
            return False
        tmp = path + ".convert_tmp"
        try:
            if os.path.isdir(path):
                shutil.copytree(target, tmp)
            else:
                os.makedirs(os.path.dirname(tmp), exist_ok=True)
                shutil.copy2(target, tmp)
            md.remove_link(path)
            os.rename(tmp, path)
            self.log("已把软链接转为常规 mod：%s" % path)
            return True
        except OSError as exc:
            shutil.rmtree(tmp, ignore_errors=True)
            self.log("软链接转换失败：%s" % exc)
            return False

    def _group_choice(self, name, action):
        """选中的 Mod 属于分组时，询问整组操作还是仅此 Mod。返回名单或 None（取消）。"""
        g = self.moddb.get_group(name)
        if not g:
            return [name]
        members = self._group_members(g)
        if len(members) <= 1:
            return [name]
        choice = self.ask_choice(
            "%s" % action,
            "「%s」属于组「%s」（%d 个 Mod）：\n%s\n\n整组%s？"
            % (name, g, len(members), "、".join(members), action),
            ["整组%s" % action, "仅此 Mod", "取消"])
        if choice is None or choice == "取消":
            return None
        if choice.startswith("整组"):
            return members
        return [name]

    def _backup_mod(self, names=None):
        if not self._ensure_manager():
            return
        if not names:
            names = self._selected_mod_names()
        if not names:
            return
        if len(names) == 1:
            names = self._group_choice(names[0], "备份")
            if names is None:
                return
        blocked = [n for n in names
                   if (self.moddb.get(n) or {}).get("save_critical")]
        if blocked:
            self._show_error("「%s」已被标记为修改存档的 mod，移入备份会破坏存档，操作已阻止。\n"
                             "如确需移动，请先取消「存档类」标记。" % "、".join(blocked))
            return
        todo = [n for n in names if (self.moddb.get(n) or {}).get("state") != "backed_up"]
        if not todo:
            self._notice("选中的 Mod 都已处于备份状态。")
            return
        choice = self.ask_choice("移入备份",
                                 "将 %d 个 Mod 移入备份？\n备份后游戏将不再加载它们。" % len(todo),
                                 ["移入备份", "取消"])
        if choice != "移入备份":
            return
        self._set_status("移入备份…")
        failed = []
        self.moddb.begin_batch()
        try:
            for name in todo:
                if not self._backup_one(name):
                    failed.append(name)
        finally:
            self.moddb.flush()
        self._set_status("完成" if not failed else "部分失败")
        if failed:
            self._show_error("部分 Mod 备份失败（已各自回滚）：%s" % "、".join(failed))
        else:
            self.log("✓ 已备份：%s" % "、".join(todo))
        self._ui_call(self._refresh_overview)

    def _backup_one(self, name):
        """备份单个 Mod 的全部路径，失败自动回滚。返回成功与否。"""
        rec = self.moddb.get(name) or {}
        paths = [p for p in rec.get("paths", []) if os.path.exists(p)]
        if not paths:
            self.log("跳过：%s（路径不存在）" % name)
            return False
        bak_dir = self._backup_dir()
        if not bak_dir:
            return False
        records = []
        done = []
        try:
            for p in paths:
                root = self._root_of(p)
                rel = os.path.relpath(p, root) if root else os.path.basename(p)
                bak = os.path.join(bak_dir, rel)
                os.makedirs(os.path.dirname(bak), exist_ok=True)
                if md.is_link(p):
                    if not self._convert_link_to_real(p):
                        raise RuntimeError("软链接转换失败：%s" % p)
                if os.path.exists(bak):
                    shutil.rmtree(bak) if os.path.isdir(bak) else os.remove(bak)
                os.rename(p, bak)
                records.append({"src": p, "bak": bak})
                done.append((p, bak))
                self.log("已移入备份：%s → %s" % (p, bak))
        except Exception as exc:
            for src, bak in reversed(done):
                try:
                    if os.path.exists(bak) and not os.path.exists(src):
                        os.rename(bak, src)
                except OSError:
                    pass
            self.log("备份失败（已回滚）：%s：%s" % (name, exc))
            return False
        self.moddb.set_backed_up(name, True, records)
        if self.moddb.last_error:
            self.log("⚠ 备份已完成但写入数据库失败：%s" % self.moddb.last_error)
        return True

    def _restore_mod(self, names=None):
        if not self._ensure_manager():
            return
        if not names:
            names = self._selected_mod_names()
        if not names:
            return
        if len(names) == 1:
            names = self._group_choice(names[0], "还原")
            if names is None:
                return
        todo = [n for n in names if (self.moddb.get(n) or {}).get("state") == "backed_up"]
        if not todo:
            self._notice("选中的 Mod 都不在备份状态。")
            return
        choice = self.ask_choice("还原", "将 %d 个 Mod 从备份移回原位？" % len(todo),
                                 ["还原", "取消"])
        if choice != "还原":
            return
        self._set_status("还原中…")
        failed = []
        self.moddb.begin_batch()
        try:
            for name in todo:
                if not self._restore_one(name):
                    failed.append(name)
        finally:
            self.moddb.flush()
        self._set_status("完成" if not failed else "部分失败")
        if failed:
            self._show_error("部分 Mod 还原失败（已各自回滚）：%s" % "、".join(failed))
        else:
            self.log("✓ 已还原：%s" % "、".join(todo))
        self._ui_call(self._refresh_overview)

    def _restore_one(self, name):
        rec = self.moddb.get(name) or {}
        records = rec.get("backup_paths", [])
        if not records:
            self.log("跳过：%s（无备份记录）" % name)
            return False
        done = []
        try:
            for item in records:
                bak = item.get("bak", "")
                src = item.get("src", "")
                if not bak or not os.path.exists(bak):
                    continue
                os.makedirs(os.path.dirname(src), exist_ok=True)
                if os.path.exists(src) and not md.is_link(src):
                    shutil.rmtree(src) if os.path.isdir(src) else os.remove(src)
                os.rename(bak, src)
                done.append((bak, src))
                self.log("已还原：%s → %s" % (bak, src))
        except Exception as exc:
            for bak, src in reversed(done):
                try:
                    if os.path.exists(src) and not os.path.exists(bak):
                        os.rename(src, bak)
                except OSError:
                    pass
        self.moddb.set_backed_up(name, False)
        if self.moddb.last_error:
            self.log("⚠ 还原已完成但写入数据库失败：%s" % self.moddb.last_error)
        return True

    def _root_of(self, path):
        for key in ("server_root", "client_root"):
            root = self.cfg.get(key) or ""
            if root and os.path.normcase(path).startswith(os.path.normcase(root)):
                return root
        return ""

    def _group_mods(self, names=None):
        """管理层面的合并：把多个 Mod 归为一组（记录各自保留，仅显示为一组）。"""
        if not names:
            names = self._selected_mod_names()
        if len(names) < 2:
            self._notice("请先选中两个或更多 Mod 再合并。")
            return
        default = self.moddb.get_group(names[0]) or names[0]
        result = self.ask_name("合并为一个 Mod",
                               "将 %d 个 Mod 归为一组（管理层面，记录各自保留）：\n%s\n\n组名："
                               % (len(names), "、".join(names)), ["确定", "取消"],
                               initial=default)
        if not result or result[0] != "确定" or not result[1]:
            return
        self.moddb.begin_batch()
        try:
            for name in names:
                self.moddb.set_group(name, result[1])
        finally:
            self.moddb.flush()
        self.log("✓ 已合并为一组：%s（%d 个 Mod）" % (result[1], len(names)))
        self._ui_call(self._refresh_overview)

    def _ungroup_mod(self, names=None):
        if not names:
            names = self._selected_mod_names()
        if len(names) != 1:
            return
        group = self.moddb.get_group(names[0])
        if not group:
            return
        members = self._group_members(group)
        choice = self.ask_choice("解除分组", "把「%s」从组「%s」中移出？\n（组内还有 %d 个 Mod）"
                                 % (names[0], group, len(members) - 1),
                                 ["移出", "取消"])
        if choice != "移出":
            return
        self.moddb.set_group(names[0], "")
        self.log("✓ 已移出组：%s" % names[0])
        self._ui_call(self._refresh_overview)

    def _browse(self, key):
        var = self.cfg_vars[key]
        if key == "winrar_path":
            path = filedialog.askopenfilename(
                title="选择 WinRAR.exe", filetypes=[("WinRAR", "WinRAR.exe"), ("exe", "*.exe")])
        elif key == "nanazip_path":
            path = filedialog.askopenfilename(
                title="选择 NanaZip/7z 可执行文件",
                filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")])
        else:
            path = filedialog.askdirectory(title="选择目录")
        if path:
            var.set(path)

    def _auto_detect_all(self):
        self.cfg_vars["winrar_path"].set(find_winrar() or "")
        self.cfg_vars["nanazip_path"].set(find_nanazip() or "")
        self._ui_call(self._auto_detect_parts)

    def _auto_detect_parts(self):
        client = self.cfg_vars["client_root"].get()
        if os.path.isdir(client):
            server = detect_server_root(client)
            if server:
                self.cfg_vars["server_root"].set(server)
            else:
                self.log("未在客户端目录下检测到 user/mods，请在设置中手动指定服务端根目录。")
        else:
            path = filedialog.askdirectory(title="选择游戏根目录")
            if path:
                self.cfg_vars["client_root"].set(path)
                server = detect_server_root(path)
                self.cfg_vars["server_root"].set(server or path)

    def _save_settings(self):
        for k, var in self.cfg_vars.items():
            self.cfg[k] = var.get().strip()
        self.cfg["mod_manager_enabled"] = bool(self.var_manager.get())
        if not self.cfg["backup_dir"]:
            self.cfg["backup_dir"] = os.path.join(self.cfg["client_root"], "ModBackup")
        if not os.path.isdir(self.cfg["client_root"]):
            messagebox.showwarning(APP_TITLE, "客户端根目录不存在，请重新选择。")
            return
        save_config(self.cfg)
        self._clear_backend_cache()
        self._apply_manager_visibility()
        self.log("设置已保存 → %s" % CONFIG_PATH)

    # ------------------------------------------------------- 工具
    def _show_info(self, lines):
        text = "\n".join(lines)

        def render():
            self.txt_info.configure(state="normal")
            self.txt_info.delete("1.0", "end")
            self.txt_info.insert("end", text)
            self.txt_info.configure(state="disabled")
        self._ui_call(render)
        self.log("类型：%s" % (lines[0].split("：", 1)[-1] if lines else ""))

    def _notice(self, msg):
        self._set_status("已取消")
        self.log("» %s" % msg)

        def box():
            messagebox.showinfo(APP_TITLE, msg)
        self._ui_call(box)

    def _show_error(self, msg):
        self._set_status("失败")
        self.log("✗ %s" % msg)

        def box():
            messagebox.showerror(APP_TITLE, msg)
        self._ui_call(box)


def run_selftest():
    """命令行自检：写入 selftest_result.txt 后退出。"""
    import io
    report = io.StringIO()
    report.write("selftest start\n")
    winrar = find_winrar()
    report.write("winrar=%s\n" % winrar)
    nanazip = find_nanazip()
    report.write("nanazip=%s\n" % nanazip)
    if nanazip:
        report.write("backend=nanazip\n")
    elif winrar:
        report.write("backend=winrar\n")
    else:
        report.write("backend=NONE\n")
    cfg = load_config()
    client_root = cfg.get("client_root", "")
    if os.path.isdir(client_root):
        server_root = cfg.get("server_root") or detect_server_root(client_root)
        report.write("client_root=%s\nserver_root=%s\n" % (client_root, server_root))
        entries = md.scan_installed_mods({"client_root": client_root, "server_root": server_root,
                                          "backup_dir": cfg.get("backup_dir", "")})
        report.write("scan total=%d\n" % len(entries))
        for e in entries[:30]:
            report.write("  %s | %s | link=%s | %s\n" % (e.name, e.location, e.is_link, e.path))
        pairs = md.pair_mods(entries)
        report.write("pairs=%d\n" % len(pairs))
        for c, s, score in pairs:
            report.write("  %s <-> %s (%.2f)\n" % (c.name, s.name, score))
    report.write("selftest end\n")
    with open(os.path.join(app_dir(), "selftest_result.txt"), "w", encoding="utf-8") as f:
        f.write(report.getvalue())


def main():
    if "--selftest" in sys.argv:
        run_selftest()
        return
    root = TkinterDnD.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
