# -*- coding: utf-8 -*-
"""集成测试：驱动 App._process_one 全流程（自动应答对话框），覆盖 zip 直解与 7z 暂存。"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mod_detector as md
import moddb
from main import App

SANDBOX = os.path.join(tempfile.gettempdir(), "opencode", "flow_sandbox")
PASSED = []
FAILED = []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


class AutoApp(App):
    def __init__(self, rootless=True, db_path=None):
        self.cfg = {}
        self.moddb = moddb.ModDB(db_path or os.path.join(SANDBOX, "mods_db.json"))
        self.ui_jobs = __import__("queue").Queue()
        self.queue = __import__("queue").Queue()
        self.worker_active = False
        self.scan_busy = False
        self.choices = []
        self.logs = []

    def ask_choice(self, title, msg, buttons):
        self.logs.append(("choice", title))
        return buttons[0]

    def ask_name(self, title, msg, buttons, initial=""):
        return ("确定", initial)

    def _show_error(self, msg):
        self.logs.append(("error", msg))
        print("   ERROR:", msg)

    def _notice(self, msg):
        self.logs.append(("notice", msg))

    def _set_status(self, text):
        pass

    def log(self, msg):
        self.logs.append(("log", msg))

    def _show_info(self, lines):
        pass

    def _refresh_overview(self):
        pass

    def _ui_call(self, fn):
        fn()

    def _delete_target(self, target):
        if os.path.isdir(target):
            shutil.rmtree(target)
        elif os.path.exists(target):
            os.remove(target)


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f, "content:%s" % f)


def main():
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX)
    client = os.path.join(SANDBOX, "game")
    server = os.path.join(SANDBOX, "game", "SPT")
    for d in (client, os.path.join(client, "BepInEx", "plugins"),
              os.path.join(client, "BepInEx", "mods_storage", "plugins"),
              server, os.path.join(server, "user", "mods"),
              os.path.join(server, "user", "mods_storage")):
        os.makedirs(d)
    app = AutoApp()
    app.cfg = {"client_root": client, "server_root": server,
               "winrar_path": r"C:\Program Files\WinRAR\WinRAR.exe"}

    # 场景A：zip 客户端插件（1:1 直解）
    z_a = os.path.join(SANDBOX, "A.zip")
    make_zip(z_a, ["BepInEx/plugins/PluginA/PluginA.dll"])
    app._process_one(z_a)
    check("A zip 客户端插件已安装",
          os.path.isfile(os.path.join(client, "BepInEx", "plugins", "PluginA", "PluginA.dll")))

    # 场景B：7z 服务端 mod（暂存流程）
    import py7zr
    src = os.path.join(SANDBOX, "src", "ModB")
    os.makedirs(src)
    open(os.path.join(src, "config.json"), "w").write("x")
    open(os.path.join(src, "ModB.dll"), "w").write("x")
    z_b = os.path.join(SANDBOX, "B.7z")
    with py7zr.SevenZipFile(z_b, "w") as zf:
        zf.writeall(src, "ModB")
    app._process_one(z_b)
    check("B 7z 服务端 mod 已安装到 user/mods/ModB",
          os.path.isfile(os.path.join(server, "user", "mods", "ModB", "ModB.dll")))

    # 场景C：危险包（路径穿越）→ 拒绝且不写入
    z_c = os.path.join(SANDBOX, "C.zip")
    make_zip(z_c, ["../evil.txt"])
    app._process_one(z_c)
    check("C 路径穿越被拒绝", not os.path.isfile(os.path.join(SANDBOX, "evil.txt")))

    # 场景D：软链接目标 → 自动应答"转换为常规 mod"
    storage_mod = os.path.join(server, "user", "mods_storage", "ModD")
    link_path = os.path.join(server, "user", "mods", "ModD")
    os.makedirs(storage_mod)
    open(os.path.join(storage_mod, "old.txt"), "w").write("old")
    subprocess.run(["cmd", "/c", "mklink", "/J", link_path, storage_mod],
                   check=True, capture_output=True)
    z_d = os.path.join(SANDBOX, "D.zip")
    make_zip(z_d, ["user/mods/ModD/config.json"])
    app._process_one(z_d)
    check("D 软链接已转为常规 mod",
          not md.is_link(link_path)
          and os.path.isfile(os.path.join(link_path, "config.json"))
          and not os.path.exists(storage_mod))

    # 场景E：组合包（BepInEx + SPT/user/mods）→ 分别装到客户端和服务端
    z_e = os.path.join(SANDBOX, "E.zip")
    make_zip(z_e, ["BepInEx/plugins/ClientE/ClientE.dll",
                   "SPT/user/mods/ServerE/ServerE.dll",
                   "README.md"])
    app._process_one(z_e)
    check("E 组合包客户端部分",
          os.path.isfile(os.path.join(client, "BepInEx", "plugins", "ClientE", "ClientE.dll")))
    check("E 组合包服务端部分",
          os.path.isfile(os.path.join(server, "user", "mods", "ServerE", "ServerE.dll")))
    check("E 良性文件未写入", not os.path.isfile(os.path.join(client, "README.md")))

    # 场景F：外层包裹结构（KeyTips/BepInEx/...）→ 直接识别，无需确认
    z_f = os.path.join(SANDBOX, "KeyTips-1.0.1.zip")
    make_zip(z_f, ["KeyTips/BepInEx/plugins/KeyTips/KeyTips.dll"])
    app._process_one(z_f)
    check("F 包裹结构自动识别为客户端",
          os.path.isfile(os.path.join(client, "BepInEx", "plugins", "KeyTips", "KeyTips.dll")))
    rec_f = app.moddb.get("KeyTips")
    check("F DB 记录生成且版本识别", rec_f is not None and rec_f.get("version") == "1.0.1",
          str(rec_f)[:80] if rec_f else "")

    # 场景G：combo 安装后 DB 分类为双端
    rec_e = app.moddb.get("E") or app.moddb.get("ServerE")
    check("G DB 记录存在", rec_e is not None)
    check("G combo 分类为双端", rec_e is not None and rec_e.get("category") == "双端",
          str(rec_e.get("category")) if rec_e else "")

    # 场景H：更新安装后移除 mods_storage 旧副本（客户端）
    sto_h = os.path.join(client, "BepInEx", "mods_storage", "plugins", "PluginH")
    os.makedirs(sto_h)
    open(os.path.join(sto_h, "old.dll"), "w").write("x")
    z_h = os.path.join(SANDBOX, "PluginH.zip")
    make_zip(z_h, ["BepInEx/plugins/PluginH/PluginH.dll"])
    app._process_one(z_h)
    check("H 更新后客户端 storage 旧副本被移除",
          not os.path.exists(sto_h)
          and os.path.isfile(os.path.join(client, "BepInEx", "plugins", "PluginH", "PluginH.dll")))

    # 场景H2：服务端 storage 旧副本移除
    sto_s = os.path.join(server, "user", "mods_storage", "ServerH")
    os.makedirs(sto_s)
    open(os.path.join(sto_s, "old.dll"), "w").write("x")
    z_h2 = os.path.join(SANDBOX, "ServerH.zip")
    make_zip(z_h2, ["user/mods/ServerH/ServerH.dll"])
    app._process_one(z_h2)
    check("H2 更新后服务端 storage 旧副本被移除",
          not os.path.exists(sto_s)
          and os.path.isfile(os.path.join(server, "user", "mods", "ServerH", "ServerH.dll")))

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print("=" * 40)
    if FAILED:
        print("RESULT: FAILED", FAILED)
        sys.exit(1)
    print("RESULT: ALL PASS (%d)" % len(PASSED))


if __name__ == "__main__":
    main()
