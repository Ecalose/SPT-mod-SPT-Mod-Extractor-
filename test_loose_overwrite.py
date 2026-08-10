# -*- coding: utf-8 -*-
"""回归测试：散装客户端 dll 安装不得删除 BepInEx\\plugins 下其他 mod。

复现场景：BepInEx\\plugins 已装有 OtherMod.dll，再安装一个单文件
BepInEx/plugins/7Bpencil.BrighterInteriors.dll 的包（KIND_CLIENT 的 __loose__
子情况，internal_root=BepInEx/plugins）。修复前会整体 rmtree plugins 目录。
"""
import os
import shutil
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_flow import AutoApp, make_zip
from nanazip import find_nanazip
from winrar import find_winrar

SANDBOX = os.path.join(tempfile.gettempdir(), "opencode", "loose_overwrite_sandbox")
PASSED, FAILED = [], []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def main():
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX)
    client = os.path.join(SANDBOX, "game")
    server = os.path.join(SANDBOX, "game", "SPT")
    plugins = os.path.join(client, "BepInEx", "plugins")
    for d in (plugins, server, os.path.join(server, "user", "mods")):
        os.makedirs(d)

    # 预先存在的其他 mod
    other_dll = os.path.join(plugins, "OtherMod.dll")
    with open(other_dll, "w") as f:
        f.write("other")
    other_sub = os.path.join(plugins, "OtherFolder", "OtherFolder.dll")
    os.makedirs(os.path.dirname(other_sub))
    with open(other_sub, "w") as f:
        f.write("other-sub")

    app = AutoApp(db_path=os.path.join(SANDBOX, "mods_db.json"))
    nz = find_nanazip()
    wr = find_winrar()
    app.cfg = {"client_root": client, "server_root": server,
               "winrar_path": wr or "", "nanazip_path": nz or ""}

    # 场景1：BepInEx/plugins/<single>.dll（KIND_CLIENT __loose__）
    z1 = os.path.join(SANDBOX, "BrighterInteriors.zip")
    make_zip(z1, ["BepInEx/plugins/7Bpencil.BrighterInteriors.dll"])
    app._process_one(z1)
    installed = os.path.join(plugins, "7Bpencil.BrighterInteriors.dll")
    check("1 散装 dll 已安装", os.path.isfile(installed))
    check("1 其他散装 dll 未被删除", os.path.isfile(other_dll),
          "其他 mod 被误删！")
    check("1 其他子目录 mod 未被删除", os.path.isfile(other_sub),
          "其他子目录 mod 被误删！")

    # 场景2：KIND_CLIENT_LOOSE（多顶层散装：dll + 良性说明文件）
    other2 = os.path.join(plugins, "Preserved2.dll")
    with open(other2, "w") as f:
        f.write("p2")
    z2 = os.path.join(SANDBOX, "LooseClient.zip")
    make_zip(z2, ["LooseClient.dll", "config/my.json"])
    app._process_one(z2)
    check("2 loose dll 已安装", os.path.isfile(os.path.join(plugins, "LooseClient.dll")))
    check("2 其他 dll 未被删除", os.path.isfile(other_dll) and os.path.isfile(other2),
          "其他 mod 被误删！")


if __name__ == "__main__":
    main()
