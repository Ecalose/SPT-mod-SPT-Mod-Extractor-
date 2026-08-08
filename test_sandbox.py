# -*- coding: utf-8 -*-
"""沙箱端到端测试：WinRAR 解压 + 软链接/联接转换（不触碰真实游戏目录）。"""
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mod_detector as md
from winrar import find_winrar, list_archive, extract_to

SANDBOX = os.path.join(tempfile.gettempdir(), "opencode", "spt_sandbox")
RESULTS = []


def check(name, cond, extra=""):
    RESULTS.append((name, cond))
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.writestr(f, "content:%s" % f)


def make_junction(src, dst):
    subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], check=True,
                   capture_output=True)


def setup():
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX)
    client = os.path.join(SANDBOX, "game")
    server = os.path.join(SANDBOX, "game", "SPT")
    for d in (client, os.path.join(client, "BepInEx", "plugins"),
              server, os.path.join(server, "user", "mods"),
              os.path.join(server, "user", "mods_storage")):
        os.makedirs(d)
    return {"client_root": client, "server_root": server,
            "winrar_path": find_winrar()}


def main():
    cfg = setup()
    winrar = cfg["winrar_path"]
    check("找到 WinRAR", bool(winrar), str(winrar))

    # ---------- 场景1：服务端 mod（单顶层文件夹）端到端 ----------
    z1 = os.path.join(SANDBOX, "TestMod.zip")
    make_zip(z1, ["TestMod/config.json", "TestMod/TestMod.dll", "TestMod/sub/data.bin"])
    a = md.analyze_archive(winrar, z1)
    check("场景1 识别为 server_named", a.kind == md.KIND_SERVER_NAMED and a.mod_name == "TestMod",
          "%s/%s" % (a.kind, a.mod_name))
    md.resolve_targets(cfg, a)
    check("场景1 目标目录", a.target_dir == os.path.join(cfg["server_root"], "user", "mods", "TestMod"),
          a.target_dir)

    # 模拟 _process_one 的暂存移动流程
    staging = tempfile.mkdtemp(prefix="mod_extract_")
    ok, msg = extract_to(winrar, z1, staging)
    check("场景1 WinRAR 解压到暂存", ok, msg)
    shutil.copytree(os.path.join(staging, "TestMod"), a.target_dir)
    shutil.rmtree(staging, ignore_errors=True)
    check("场景1 文件到位", os.path.isfile(os.path.join(a.target_dir, "config.json"))
          and os.path.isfile(os.path.join(a.target_dir, "TestMod.dll")))
    shutil.rmtree(a.target_dir)

    # ---------- 场景2：客户端插件（1:1 直接解压） ----------
    z2 = os.path.join(SANDBOX, "ClientMod.zip")
    make_zip(z2, ["BepInEx/plugins/ClientMod/ClientMod.dll"])
    a2 = md.analyze_archive(winrar, z2)
    check("场景2 识别为 client", a2.kind == md.KIND_CLIENT and a2.mod_name == "ClientMod",
          "%s/%s" % (a2.kind, a2.mod_name))
    md.resolve_targets(cfg, a2)
    ok, msg = extract_to(winrar, z2, cfg["client_root"])
    check("场景2 直接解压到客户端根", ok and os.path.isfile(
        os.path.join(cfg["client_root"], "BepInEx", "plugins", "ClientMod", "ClientMod.dll")), msg)

    # ---------- 场景3：软链接转换（junction） ----------
    storage_mod = os.path.join(cfg["server_root"], "user", "mods_storage", "LinkedMod")
    link_path = os.path.join(cfg["server_root"], "user", "mods", "LinkedMod")
    os.makedirs(storage_mod)
    with open(os.path.join(storage_mod, "old.txt"), "w") as f:
        f.write("old")
    make_junction(storage_mod, link_path)
    check("场景3 联接创建并被识别", md.is_link(link_path), md.link_target(link_path))

    links = md.find_links_under(os.path.join(cfg["server_root"], "user", "mods"))
    check("场景3 递归扫描发现软链接", link_path in links)

    z3 = os.path.join(SANDBOX, "LinkedMod.zip")
    make_zip(z3, ["LinkedMod/config.json", "LinkedMod/LinkedMod.dll"])
    a3 = md.analyze_archive(winrar, z3)
    md.resolve_targets(cfg, a3)
    target3 = a3.target_dir
    check("场景3 目标指向联接路径", md.is_link(target3))

    # 选择"转换为常规 mod"
    md.remove_link(target3)
    check("场景3 删除联接（指向目录内容保留）",
          not md.is_link(target3) and os.path.isfile(os.path.join(storage_mod, "old.txt"))
          and not os.path.isfile(link_path))
    # 解压新版本
    staging = tempfile.mkdtemp(prefix="mod_extract_")
    extract_to(winrar, z3, staging)
    shutil.copytree(os.path.join(staging, "LinkedMod"), link_path)
    shutil.rmtree(staging, ignore_errors=True)
    check("场景3 转换为常规 mod 完成",
          os.path.isfile(os.path.join(link_path, "config.json"))
          and os.path.isfile(os.path.join(link_path, "LinkedMod.dll"))
          and os.path.isfile(os.path.join(storage_mod, "old.txt")))

    # ---------- 场景4：rar 压缩包（UnRAR 列表 + WinRAR 解压） ----------
    src_mod = os.path.join(SANDBOX, "src", "TestMod")
    os.makedirs(os.path.join(src_mod, "sub"))
    open(os.path.join(src_mod, "config.json"), "w").write("x")
    open(os.path.join(src_mod, "TestMod.dll"), "w").write("x")
    open(os.path.join(src_mod, "sub", "data.bin"), "w").write("x")
    rar_out = os.path.join(SANDBOX, "srv_mod.rar")
    winrar_exe = winrar
    subprocess.run([winrar_exe, "a", "-r", "-ibck", "-ep1", rar_out, src_mod],
                   check=True, capture_output=True)
    names = list_archive(winrar, rar_out)
    check("场景4 rar 列表", any(n.replace("\\", "/").endswith("config.json") for n in names),
          str(names[:5]))
    a4 = md.analyze_archive(winrar, rar_out)
    check("场景4 rar 识别为 server_named",
          a4.kind == md.KIND_SERVER_NAMED and a4.mod_name == "TestMod",
          "%s/%s" % (a4.kind, a4.mod_name))

    # ---------- 场景5：7z 压缩包（暂存检查流程） ----------
    import py7zr
    z5 = os.path.join(SANDBOX, "srv7z.7z")
    with py7zr.SevenZipFile(z5, "w") as zf:
        zf.writeall(src_mod, "TestMod")
    a5 = md.analyze_archive(winrar, z5)
    check("场景5 7z 标记为 needs_staging", a5.kind == md.KIND_NEEDS_STAGING,
          "%s/%s" % (a5.kind, a5.issue))
    staging = tempfile.mkdtemp(prefix="mod_extract_")
    ok, msg = extract_to(winrar, z5, staging)
    check("场景5 暂存解压", ok, msg)
    entries = md.walk_dir_entries(staging)
    check("场景5 暂存文件遍历", any(e.endswith("config.json") for e in entries), str(entries[:5]))
    a5 = md.analyze_entries(md.Analysis(archive=z5), entries)
    check("场景5 暂存后识别为 server_named",
          a5.kind == md.KIND_SERVER_NAMED and a5.mod_name == "TestMod",
          "%s/%s" % (a5.kind, a5.mod_name))
    md.resolve_targets(cfg, a5)
    shutil.copytree(os.path.join(staging, "TestMod"), a5.target_dir)
    shutil.rmtree(staging, ignore_errors=True)
    check("场景5 安装到 user/mods/TestMod",
          os.path.isfile(os.path.join(a5.target_dir, "TestMod.dll")))

    # ---------- 清理 ----------
    shutil.rmtree(SANDBOX, ignore_errors=True)
    failed = [n for n, c in RESULTS if not c]
    print("=" * 40)
    print("RESULT:", "ALL PASS (%d)" % len(RESULTS) if not failed else "FAILED: %s" % failed)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
