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
from winrar import find_winrar, extract_to
from nanazip import find_nanazip, extract_to as nz_extract

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
            "winrar_path": find_winrar(), "nanazip_path": find_nanazip()}


def _make_7z(path, src_dir, arcname):
    """创建 7z：优先 py7zr，未安装则用 NanaZip/7z CLI 打包。"""
    try:
        import py7zr
        with py7zr.SevenZipFile(path, "w") as zf:
            zf.writeall(src_dir, arcname)
        return True
    except ImportError:
        pass
    # 回退：用 NanaZip/7z CLI 把 src_dir 内容打包成 arcname 下的结构
    import nanazip as nz
    nz_exe = find_nanazip()
    if not nz_exe:
        return False
    # 在临时父目录里打包，使包内顶层为 arcname
    import tempfile as _t, shutil as _sh
    tmp = _t.mkdtemp(prefix="nz7z_")
    dst = os.path.join(tmp, arcname)
    _sh.copytree(src_dir, dst)
    code, _ = nz._run(nz_exe, ["a", "-t7z", path, dst])
    _sh.rmtree(tmp, ignore_errors=True)
    return code == 0


def main():
    cfg = setup()
    winrar = cfg["winrar_path"]
    nanazip = cfg["nanazip_path"]
    # 后端：NanaZip 全格式优先，回退 WinRAR
    if nanazip:
        backend, backend_path = "nanazip", nanazip
        extract_fn = nz_extract
    else:
        backend, backend_path = "winrar", winrar
        extract_fn = extract_to
    check("找到解压后端（%s）" % backend, bool(backend_path), str(backend_path))
    if not backend_path:
        print("RESULT: FAILED — 无可用解压后端")
        sys.exit(1)
    nz_arg = nanazip if backend == "nanazip" else None

    # ---------- 场景1：服务端 mod（单顶层文件夹）端到端 ----------
    z1 = os.path.join(SANDBOX, "TestMod.zip")
    make_zip(z1, ["TestMod/config.json", "TestMod/TestMod.dll", "TestMod/sub/data.bin"])
    a = md.analyze_archive(backend_path, z1, nanazip_path=nz_arg)
    check("场景1 识别为 server_named", a.kind == md.KIND_SERVER_NAMED and a.mod_name == "TestMod",
          "%s/%s" % (a.kind, a.mod_name))
    md.resolve_targets(cfg, a)
    check("场景1 目标目录", a.target_dir == os.path.join(cfg["server_root"], "user", "mods", "TestMod"),
          a.target_dir)

    staging = tempfile.mkdtemp(prefix="mod_extract_")
    ok, msg = extract_fn(backend_path, z1, staging)
    check("场景1 解压到暂存", ok, msg)
    shutil.copytree(os.path.join(staging, "TestMod"), a.target_dir)
    shutil.rmtree(staging, ignore_errors=True)
    check("场景1 文件到位", os.path.isfile(os.path.join(a.target_dir, "config.json"))
          and os.path.isfile(os.path.join(a.target_dir, "TestMod.dll")))
    shutil.rmtree(a.target_dir)

    # ---------- 场景2：客户端插件（1:1 直接解压） ----------
    z2 = os.path.join(SANDBOX, "ClientMod.zip")
    make_zip(z2, ["BepInEx/plugins/ClientMod/ClientMod.dll"])
    a2 = md.analyze_archive(backend_path, z2, nanazip_path=nz_arg)
    check("场景2 识别为 client", a2.kind == md.KIND_CLIENT and a2.mod_name == "ClientMod",
          "%s/%s" % (a2.kind, a2.mod_name))
    md.resolve_targets(cfg, a2)
    ok, msg = extract_fn(backend_path, z2, cfg["client_root"])
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
    a3 = md.analyze_archive(backend_path, z3, nanazip_path=nz_arg)
    md.resolve_targets(cfg, a3)
    target3 = a3.target_dir
    check("场景3 目标指向联接路径", md.is_link(target3))

    md.remove_link(target3)
    check("场景3 删除联接（指向目录内容保留）",
          not md.is_link(target3) and os.path.isfile(os.path.join(storage_mod, "old.txt"))
          and not os.path.isfile(link_path))
    staging = tempfile.mkdtemp(prefix="mod_extract_")
    extract_fn(backend_path, z3, staging)
    shutil.copytree(os.path.join(staging, "LinkedMod"), link_path)
    shutil.rmtree(staging, ignore_errors=True)
    check("场景3 转换为常规 mod 完成",
          os.path.isfile(os.path.join(link_path, "config.json"))
          and os.path.isfile(os.path.join(link_path, "LinkedMod.dll"))
          and os.path.isfile(os.path.join(storage_mod, "old.txt")))

    # ---------- 场景4：rar 压缩包 ----------
    # rar 创建必须用 WinRAR（NanaZip 的 rar 只读），无 WinRAR 则跳过
    src_mod = os.path.join(SANDBOX, "src", "TestMod")
    os.makedirs(os.path.join(src_mod, "sub"))
    open(os.path.join(src_mod, "config.json"), "w").write("x")
    open(os.path.join(src_mod, "TestMod.dll"), "w").write("x")
    open(os.path.join(src_mod, "sub", "data.bin"), "w").write("x")
    if winrar:
        rar_out = os.path.join(SANDBOX, "srv_mod.rar")
        subprocess.run([winrar, "a", "-r", "-ibck", "-ep1", rar_out, src_mod],
                       check=True, capture_output=True)
        a4 = md.analyze_archive(backend_path, rar_out, nanazip_path=nz_arg)
        check("场景4 rar 识别为 server_named",
              a4.kind == md.KIND_SERVER_NAMED and a4.mod_name == "TestMod",
              "%s/%s" % (a4.kind, a4.mod_name))
    else:
        print("[SKIP] 场景4 rar 创建（无 WinRAR，NanaZip rar 只读不能创建）")

    # ---------- 场景5：7z 压缩包 ----------
    z5 = os.path.join(SANDBOX, "srv7z.7z")
    made = _make_7z(z5, src_mod, "TestMod")
    check("场景5 创建 7z 测试包", made)
    if made:
        a5 = md.analyze_archive(backend_path, z5, nanazip_path=nz_arg)
        if backend == "nanazip":
            # NanaZip 能直接 list 7z → 不再 needs_staging
            check("场景5 NanaZip 直接识别 7z（不再暂存）",
                  a5.kind == md.KIND_SERVER_NAMED and a5.mod_name == "TestMod",
                  "%s/%s" % (a5.kind, a5.mod_name))
            md.resolve_targets(cfg, a5)
            staging = tempfile.mkdtemp(prefix="mod_extract_")
            ok, msg = extract_fn(backend_path, z5, staging)
            check("场景5 解压", ok, msg)
            shutil.copytree(os.path.join(staging, "TestMod"), a5.target_dir)
            shutil.rmtree(staging, ignore_errors=True)
            check("场景5 安装到 user/mods/TestMod",
                  os.path.isfile(os.path.join(a5.target_dir, "TestMod.dll")))
        else:
            # WinRAR 后端：7z 走暂存流程
            check("场景5 WinRAR 7z 标记为 needs_staging", a5.kind == md.KIND_NEEDS_STAGING,
                  "%s/%s" % (a5.kind, a5.issue))
            staging = tempfile.mkdtemp(prefix="mod_extract_")
            ok, msg = extract_fn(backend_path, z5, staging)
            check("场景5 暂存解压", ok, msg)
            entries = md.walk_dir_entries(staging)
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
