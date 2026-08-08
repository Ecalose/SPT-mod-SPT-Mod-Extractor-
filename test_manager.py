# -*- coding: utf-8 -*-
"""1.1 Mod 管理功能测试：备份/还原/软链接转换/存档标记/进程阻止/合并双端/排序收藏。"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mod_detector as md
import moddb
from test_flow import AutoApp

SANDBOX = os.path.join(tempfile.gettempdir(), "opencode", "mgr_sandbox")
PASSED = []
FAILED = []


def check(name, cond, extra=""):
    (PASSED if cond else FAILED).append(name)
    print("[%s] %s %s" % ("PASS" if cond else "FAIL", name, extra))


def make_junction(src, dst):
    subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], check=True,
                   capture_output=True)


def setup():
    if os.path.isdir(SANDBOX):
        shutil.rmtree(SANDBOX)
    client = os.path.join(SANDBOX, "game")
    server = os.path.join(SANDBOX, "game", "SPT")
    backup = os.path.join(client, "ModBackup")
    for d in (client, os.path.join(client, "BepInEx", "plugins"),
              os.path.join(client, "BepInEx", "mods_storage", "plugins"),
              server, os.path.join(server, "user", "mods"),
              os.path.join(server, "user", "mods_storage"), backup):
        os.makedirs(d)
    cfg = {"client_root": client, "server_root": server,
           "winrar_path": r"C:\Program Files\WinRAR\WinRAR.exe",
           "mod_manager_enabled": True, "backup_dir": backup}
    return cfg


def app_with(cfg):
    app = AutoApp(db_path=os.path.join(SANDBOX, "mods_db.json"))
    app.cfg = cfg
    return app


def main():
    cfg = setup()
    client, server, backup = cfg["client_root"], cfg["server_root"], cfg["backup_dir"]

    # ---------- 1. 普通 mod 移入备份 + 还原 ----------
    moda = os.path.join(client, "BepInEx", "plugins", "ModA")
    os.makedirs(moda)
    open(os.path.join(moda, "ModA.dll"), "w").write("x")
    app = app_with(cfg)
    app.moddb.upsert("ModA", [moda], cfg, version="1.0.0")
    app._selected_mod_names = lambda: ["ModA"]
    app._backup_mod()
    bak_a = os.path.join(backup, "BepInEx", "plugins", "ModA")
    check("1 移入备份后原位置消失", not os.path.exists(moda))
    check("1 备份位置存在", os.path.isfile(os.path.join(bak_a, "ModA.dll")), bak_a)
    rec = app.moddb.get("ModA")
    check("1 DB 状态 backed_up", rec and rec.get("state") == "backed_up")
    check("1 备份记录含 src/bak",
          rec and rec.get("backup_paths") and rec["backup_paths"][0]["src"] == moda)
    app._restore_mod()
    check("1 还原后回到原位置", os.path.isfile(os.path.join(moda, "ModA.dll")))
    check("1 还原后备份位置消失", not os.path.exists(bak_a))
    check("1 DB 状态 active", app.moddb.get("ModA").get("state") == "active")

    # ---------- 2. 双端 mod 两条路径一起备份 ----------
    modc = os.path.join(client, "BepInEx", "plugins", "ModC")
    mods = os.path.join(server, "user", "mods", "ModC")
    os.makedirs(modc)
    os.makedirs(mods)
    open(os.path.join(modc, "C.dll"), "w").write("x")
    open(os.path.join(mods, "config.json"), "w").write("x")
    app.moddb.upsert("ModC", [modc, mods], cfg, version="2.0.0")
    app._selected_mod_names = lambda: ["ModC"]
    app._backup_mod()
    check("2 双端两处均已备份",
          not os.path.exists(modc) and not os.path.exists(mods)
          and os.path.isfile(os.path.join(backup, "BepInEx", "plugins", "ModC", "C.dll"))
          and os.path.isfile(os.path.join(backup, "user", "mods", "ModC", "config.json")))
    app._restore_mod()
    check("2 双端两处均已还原",
          os.path.isfile(os.path.join(modc, "C.dll"))
          and os.path.isfile(os.path.join(mods, "config.json")))

    # ---------- 3. 软链接（junction）转常规后移入备份 ----------
    storage_l = os.path.join(client, "BepInEx", "mods_storage", "plugins", "LinkMod")
    link_l = os.path.join(client, "BepInEx", "plugins", "LinkMod")
    os.makedirs(storage_l)
    open(os.path.join(storage_l, "real.dll"), "w").write("x")
    make_junction(storage_l, link_l)
    app.moddb.upsert("LinkMod", [link_l], cfg, version="3.0.0")
    app._selected_mod_names = lambda: ["LinkMod"]
    app._backup_mod()
    bak_l = os.path.join(backup, "BepInEx", "plugins", "LinkMod")
    check("3 链接已转换并移入备份（真实文件）",
          not os.path.exists(link_l) and os.path.isfile(os.path.join(bak_l, "real.dll")))
    check("3 nekobox storage 保留", os.path.isfile(os.path.join(storage_l, "real.dll")))
    app._restore_mod()
    check("3 还原为常规文件夹", os.path.isfile(os.path.join(link_l, "real.dll"))
          and not md.is_link(link_l))

    # ---------- 4. 存档类标记阻止 ----------
    modd = os.path.join(client, "BepInEx", "plugins", "ModD")
    os.makedirs(modd)
    open(os.path.join(modd, "D.dll"), "w").write("x")
    app.moddb.upsert("ModD", [modd], cfg, version="4.0.0")
    app.moddb.set_save_critical("ModD", True)
    app._selected_mod_names = lambda: ["ModD"]
    app.logs.clear()
    app._backup_mod()
    check("4 存档类标记阻止移动",
          any(k == "error" for k, m in app.logs) and os.path.exists(modd))

    # ---------- 5. 进程运行阻止 ----------
    app.moddb.set_save_critical("ModD", False)
    app._running_games = lambda: ["EscapeFromTarkov.exe"]
    app.logs.clear()
    app._backup_mod()
    check("5 游戏运行中阻止操作",
          any(k == "error" for k, m in app.logs) and os.path.exists(modd))
    app._running_games = lambda: []

    # ---------- 6. 跨侧合并 = 管理层面分组（记录各自保留） ----------
    rec_e = {"name": "ManMod", "category": "客户端", "version": "5.0.0",
             "install_time": "2026-01-01 00:00", "source": "install",
             "favorite": True, "save_critical": True, "state": "active",
             "backup_paths": [], "paths": [modc]}
    rec_f = {"name": "ManModS", "category": "服务端", "version": "5.0.0",
             "install_time": "2026-01-01 00:00", "source": "install",
             "favorite": False, "save_critical": False, "state": "active",
             "backup_paths": [], "paths": [mods]}
    app.moddb.mods.update({rec_e["name"]: rec_e, rec_f["name"]: rec_f})
    app.moddb.save()
    app._selected_mod_names = lambda: ["ManMod", "ManModS"]
    app._group_mods()
    check("6 分组后两条记录都保留",
          app.moddb.get("ManMod") is not None and app.moddb.get("ManModS") is not None)
    check("6 分组标记正确", app.moddb.get_group("ManMod") == "ManMod"
          and app.moddb.get_group("ManModS") == "ManMod")
    check("6 分组后收藏/存档标记各自保留",
          app.moddb.get("ManMod").get("favorite")
          and app.moddb.get("ManMod").get("save_critical")
          and not app.moddb.get("ManModS").get("favorite"))

    # ---------- 7. 排序与收藏（DB 层） ----------
    app.moddb.mods["ZZZ"] = {"name": "ZZZ", "category": "客户端", "version": "1",
                             "install_time": "2026-02-01", "source": "scan",
                             "favorite": True, "save_critical": False,
                             "state": "active", "backup_paths": [], "paths": [moda]}
    items = app.moddb.sorted_all(sort_by="name")
    check("7 收藏置顶（收藏项全部在前）",
          all(items[i][1].get("favorite") for i in range(2))
          and not items[2][1].get("favorite"))
    items_time = app.moddb.sorted_all(sort_by="time", descending=True)
    non_fav = [r for _, r in items_time if not r.get("favorite")]
    times = [r.get("install_time") for r in non_fav]
    check("7 按时间降序（收藏仍置顶）",
          all(items_time[i][1].get("favorite") for i in range(2))
          and times == sorted(times, reverse=True), str(times))

    # ---------- 8. 扫描排除备份目录 ----------
    stray = os.path.join(backup, "BepInEx", "plugins", "GhostMod")
    os.makedirs(stray)
    entries = md.scan_installed_mods(cfg)
    check("8 扫描排除备份目录", all("ModBackup" not in e.path for e in entries))

    # ---------- 9. 关闭「禁用 Mod」开关：备份被阻止，收藏/标记不受影响 ----------
    app.cfg["mod_manager_enabled"] = False
    app._selected_mod_names = lambda: ["ModD"]
    app.logs.clear()
    app._backup_mod()
    check("9 开关关闭时备份被阻止",
          any(k == "notice" for k, m in app.logs) and os.path.exists(modd))
    app.logs.clear()
    app._toggle_fav()
    check("9 开关关闭时收藏仍可用", app.moddb.get("ModD").get("favorite"))
    app.logs.clear()
    app._toggle_save()
    check("9 开关关闭时存档标记仍可用", app.moddb.get("ModD").get("save_critical"))

    # ---------- 10. 同侧合并：两个客户端组件归为一组（记录各自保留） ----------
    rec_g = {"name": "HollyFX", "category": "客户端", "version": "2.0.0",
             "install_time": "2026-01-01", "source": "install", "favorite": False,
             "save_critical": False, "state": "active", "backup_paths": [],
             "paths": [modc]}
    rec_h = {"name": "HollyGfx", "category": "客户端", "version": "2.0.0",
             "install_time": "2026-01-01", "source": "install", "favorite": False,
             "save_critical": False, "state": "active", "backup_paths": [],
             "paths": [moda]}
    app.moddb.mods.update({rec_g["name"]: rec_g, rec_h["name"]: rec_h})
    app.moddb.save()
    app._selected_mod_names = lambda: ["HollyFX", "HollyGfx"]
    app._group_mods()
    check("10 同侧分组后记录保留且组标记一致",
          app.moddb.get("HollyFX") is not None and app.moddb.get("HollyGfx") is not None
          and app.moddb.get_group("HollyFX") == "HollyFX"
          and app.moddb.get_group("HollyGfx") == "HollyFX")

    # ---------- 10b. 已备份的 Mod 也可以分组 ----------
    rec_i = {"name": "BackedMod", "category": "客户端", "version": "1.0.0",
             "install_time": "2026-01-01", "source": "install", "favorite": False,
             "save_critical": False, "state": "backed_up",
             "backup_paths": [{"src": moda, "bak": bak_a}], "paths": [moda]}
    app.moddb.mods["BackedMod"] = rec_i
    app.moddb.save()
    app._selected_mod_names = lambda: ["BackedMod", "HollyGfx"]
    app._group_mods()
    check("10b 已备份 Mod 可正常分组",
          app.moddb.get_group("BackedMod") == "BackedMod"
          and app.moddb.get_group("HollyGfx") == "BackedMod")
    app._selected_mod_names = lambda: ["BackedMod"]
    app._ungroup_mod()
    check("10b 解除分组成功", app.moddb.get_group("BackedMod") == "")

    # ---------- 11. 分类过滤标签（全部/已启用/已备份/不可操作） ----------
    app.moddb.mods["CritOnly"] = {"name": "CritOnly", "category": "服务端",
                                  "version": "1", "install_time": "2026-01-01",
                                  "source": "scan", "favorite": False,
                                  "save_critical": True, "state": "active",
                                  "backup_paths": [], "paths": [mods]}
    app.moddb.mods["BakOnly"] = {"name": "BakOnly", "category": "客户端",
                                 "version": "1", "install_time": "2026-01-01",
                                 "source": "scan", "favorite": False,
                                 "save_critical": False, "state": "backed_up",
                                 "backup_paths": [{"src": moda, "bak": bak_a}],
                                 "paths": [moda]}
    app.moddb.save()
    names_all = [n for n, _ in app._filter_records("全部")]
    check("11 全部包含所有记录", "CritOnly" in names_all and "BakOnly" in names_all)
    names_en = [n for n, _ in app._filter_records("已启用")]
    check("11 已启用排除已备份", "BakOnly" not in names_en and "CritOnly" in names_en)
    names_bak = [n for n, _ in app._filter_records("已备份")]
    check("11 已备份只含备份记录",
          "BakOnly" in names_bak and "BackedMod" in names_bak
          and all(app.moddb.get(n).get("state") == "backed_up" for n in names_bak))
    names_crit = [n for n, _ in app._filter_records("不可操作")]
    check("11 不可操作只含存档类标记",
          "CritOnly" in names_crit
          and all(app.moddb.get(n).get("save_critical") for n in names_crit))

    # ---------- 12. 整组备份/还原 ----------
    app.cfg["mod_manager_enabled"] = True
    grp_a = os.path.join(client, "BepInEx", "plugins", "GrpA")
    grp_b = os.path.join(client, "BepInEx", "plugins", "GrpB")
    os.makedirs(grp_a)
    os.makedirs(grp_b)
    open(os.path.join(grp_a, "A.dll"), "w").write("x")
    open(os.path.join(grp_b, "B.dll"), "w").write("x")
    app.moddb.upsert("GrpA", [grp_a], cfg, version="1.0.0")
    app.moddb.upsert("GrpB", [grp_b], cfg, version="1.0.0")
    app.moddb.set_group("GrpA", "MyGroup")
    app.moddb.set_group("GrpB", "MyGroup")
    app._selected_mod_names = lambda: ["GrpA"]
    app._backup_mod()  # AutoApp 自动选第一个按钮 = 整组备份
    check("12 选中一个成员整组备份两个都移入",
          not os.path.exists(grp_a) and not os.path.exists(grp_b)
          and app.moddb.get("GrpA").get("state") == "backed_up"
          and app.moddb.get("GrpB").get("state") == "backed_up")
    app._selected_mod_names = lambda: ["GrpB"]
    app._restore_mod()  # 整组还原
    check("12 整组还原两个都回到原位",
          os.path.isfile(os.path.join(grp_a, "A.dll"))
          and os.path.isfile(os.path.join(grp_b, "B.dll"))
          and app.moddb.get("GrpA").get("state") == "active"
          and app.moddb.get("GrpB").get("state") == "active")

    # ---------- 13. 整组备份时存档类成员阻止 ----------
    app.moddb.set_save_critical("GrpB", True)
    app.logs.clear()
    app._backup_mod()
    check("13 组内含存档类成员时整组被阻止",
          any(k == "error" for k, m in app.logs)
          and os.path.exists(grp_a) and os.path.exists(grp_b))
    app.moddb.set_save_critical("GrpB", False)

    # ---------- 14. 扫描剔除已消失的路径（storage 被清理后） ----------
    stale = os.path.join(client, "BepInEx", "plugins", "StaleMod")
    os.makedirs(stale)
    open(os.path.join(stale, "S.dll"), "w").write("x")
    app.moddb.upsert("StaleMod", [stale, os.path.join(client, "BepInEx", "plugins", "GoneMod")],
                     cfg, version="1.0.0")
    shutil.rmtree(stale)
    os.makedirs(os.path.join(client, "BepInEx", "plugins", "FreshMod"))
    open(os.path.join(client, "BepInEx", "plugins", "FreshMod", "F.dll"), "w").write("x")
    app._scan_mods()  # 需要等待扫描线程
    import time
    deadline = time.time() + 10
    while app.scan_busy and time.time() < deadline:
        time.sleep(0.1)
    rec_stale = app.moddb.get("StaleMod")
    check("14 扫描剔除已消失路径（空记录被移除）", rec_stale is None)
    check("14 正常 mod 路径保留",
          all(os.path.exists(p) for _, r in app.moddb.mods.items()
              if r.get("state") != "backed_up" for p in r.get("paths", [])))
    check("14 已备份记录路径不被剔除",
          app.moddb.get("BakOnly") is not None
          and "BakOnly" in [n for n, _ in app._filter_records("已备份")])

    # ---------- 15. 一个 mod 分裂为双客户端+服务端：先分组客户端，再并入服务端 ----------
    ca2 = os.path.join(client, "BepInEx", "plugins", "SplitA")
    cb2 = os.path.join(client, "BepInEx", "plugins", "SplitB")
    sr2 = os.path.join(server, "user", "mods", "SplitSrv")
    for d, fn in ((ca2, "A.dll"), (cb2, "B.dll"), (sr2, "S.dll")):
        os.makedirs(d)
        open(os.path.join(d, fn), "w").write("x")
    app.moddb.upsert("SplitA", [ca2], cfg, version="1.0.0")
    app.moddb.upsert("SplitB", [cb2], cfg, version="1.0.0")
    app.moddb.upsert("SplitSrv", [sr2], cfg, version="1.0.0")
    app._group_mods(["SplitA", "SplitB"])  # 第一步：两个客户端归为一组
    g15 = app.moddb.get_group("SplitA")
    check("15 双客户端已分为一组", g15 == app.moddb.get_group("SplitB") and bool(g15))
    app._group_mods(["SplitA", "SplitSrv"])  # 第二步：成员+服务端并入同一组
    check("15 服务端并入既有组", app.moddb.get_group("SplitSrv") == g15
          and app.moddb.get_group("SplitB") == g15)
    check("15 组节点展开为全部成员",
          set(app._group_members(g15)) == {"SplitA", "SplitB", "SplitSrv"})

    # ---------- 16. 组节点整组收藏 / 整组存档标记 ----------
    app._toggle_save(["SplitA", "SplitB", "SplitSrv"])  # 整组标记
    check("16 整组标记存档类",
          all(app.moddb.get(n).get("save_critical") for n in ("SplitA", "SplitB", "SplitSrv")))
    app._toggle_save(["SplitA", "SplitB", "SplitSrv"])  # 整组取消
    check("16 整组取消存档标记",
          not any(app.moddb.get(n).get("save_critical") for n in ("SplitA", "SplitB", "SplitSrv")))
    app._toggle_fav(["SplitA", "SplitB", "SplitSrv"])  # 整组收藏
    check("16 整组收藏",
          all(app.moddb.get(n).get("favorite") for n in ("SplitA", "SplitB", "SplitSrv")))
    app._toggle_fav(["SplitA", "SplitB", "SplitSrv"])  # 整组取消收藏
    check("16 整组取消收藏",
          not any(app.moddb.get(n).get("favorite") for n in ("SplitA", "SplitB", "SplitSrv")))
    app._toggle_save(["SplitSrv"])  # 单个成员仍可单独操作
    check("16 单个成员独立标记不受影响",
          app.moddb.get("SplitSrv").get("save_critical")
          and not app.moddb.get("SplitA").get("save_critical"))

    shutil.rmtree(SANDBOX, ignore_errors=True)
    print("=" * 40)
    if FAILED:
        print("RESULT: FAILED", FAILED)
        sys.exit(1)
    print("RESULT: ALL PASS (%d)" % len(PASSED))


if __name__ == "__main__":
    main()
