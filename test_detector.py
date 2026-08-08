# -*- coding: utf-8 -*-
"""识别逻辑测试：构造各类示例压缩包并验证判定结果。"""
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mod_detector as md


def make_zip(path, files):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in files:
            zf.writestr(name, "test content for %s" % name)


def run():
    tmp = tempfile.mkdtemp(prefix="mod_detect_test_")
    cases = [
        ("server_std.zip",
         ["user/mods/TestMod/config.json", "user/mods/TestMod/TestMod.dll"],
         md.KIND_SERVER, "TestMod", "server", os.path.join("user", "mods", "TestMod")),
        ("server_folder.zip",
         ["TestMod/config.json", "TestMod/TestMod.dll"],
         md.KIND_SERVER_NAMED, "TestMod", "server", os.path.join("user", "mods", "TestMod")),
        ("server_folder_cfgdir.zip",
         ["MyMod/config/settings.json", "MyMod/MyMod.dll"],
         md.KIND_SERVER_NAMED, "MyMod", "server", os.path.join("user", "mods", "MyMod")),
        ("client_plugin.zip",
         ["BepInEx/plugins/SomePlugin/SomePlugin.dll", "BepInEx/plugins/SomePlugin/readme.txt"],
         md.KIND_CLIENT, "SomePlugin", "client",
         os.path.join("BepInEx", "plugins", "SomePlugin")),
        ("client_loose.zip",
         ["MyPlugin.dll", "config/my.json"],
         md.KIND_CLIENT_LOOSE, "", "client", os.path.join("BepInEx", "plugins")),
        ("server_loose.zip",
         ["Mod.dll", "config.json"],
         md.KIND_SERVER_LOOSE, "Mod", "server", os.path.join("user", "mods", "Mod")),
        ("mixed.zip",
         ["FolderA/a.txt", "FolderB/b.txt"],
         md.KIND_MIXED, "", "", None),
        ("traversal.zip",
         ["../evil.txt"],
         md.KIND_TRAVERSAL, "", "", None),
        ("empty.zip", [], md.KIND_EMPTY, "", "", None),
        ("legacy.zip",
         ["mods/OldMod/config.json"],
         md.KIND_LEGACY, "OldMod", "server", os.path.join("mods", "OldMod")),
        ("two_server.zip",
         ["user/mods/A/a.dll", "user/mods/B/b.dll"],
         md.KIND_SERVER, "", "server", ""),
        ("multi_client.zip",
         ["BepInEx/plugins/A/a.dll", "BepInEx/plugins/B/b.dll"],
         md.KIND_CLIENT, "", "client", ""),
        ("bepinex_loose.zip",
         ["BepInEx/plugins/LoosePlugin.dll"],
         md.KIND_CLIENT, "", "client", os.path.join("BepInEx", "plugins")),
        ("combo.zip",
         ["BepInEx/plugins/ClientA/ClientA.dll",
          "SPT/user/mods/ServerB/ServerB.dll"],
         md.KIND_COMBO, "", "combo", None),
        ("combo_readme.zip",
         ["BepInEx/plugins/ClientA/ClientA.dll",
          "SPT/user/mods/ServerB/ServerB.dll",
          "README.md", "LICENSE.txt"],
         md.KIND_COMBO, "", "combo", None),
        ("server_root.zip",
         ["SPT/user/mods/fika-server/FikaServer.dll",
          "SPT/user/mods/fika-server/config.jsonc"],
         md.KIND_SERVER_ROOT, "fika-server", "server", ""),
        ("client_with_readme.zip",
         ["BepInEx/plugins/SomePlugin/SomePlugin.dll", "README.md"],
         md.KIND_CLIENT, "SomePlugin", "client",
         os.path.join("BepInEx", "plugins", "SomePlugin")),
        ("server_with_readme.zip",
         ["user/mods/TestMod/config.json", "user/mods/TestMod/TestMod.dll", "README.md"],
         md.KIND_SERVER, "TestMod", "server", os.path.join("user", "mods", "TestMod")),
        ("wrapped_client.zip",
         ["KeyTips/BepInEx/plugins/KeyTips/KeyTips.dll"],
         md.KIND_CLIENT, "KeyTips", "client", os.path.join("BepInEx", "plugins", "KeyTips")),
        ("wrapped_server.zip",
         ["FikaServer/SPT/user/mods/fika-server/FikaServer.dll"],
         md.KIND_SERVER_ROOT, "fika-server", "server", ""),
        ("not_archive.txt", [], md.KIND_NOT_ARCHIVE, "", "", None),
    ]
    ok = True
    for fname, files, expect_kind, expect_name, expect_root, expect_internal in cases:
        path = os.path.join(tmp, fname)
        if fname.endswith(".zip"):
            make_zip(path, files)
        else:
            with open(path, "w") as f:
                f.write("hi")
        a = md.analyze_archive(None, path)
        status = "PASS"
        if a.kind != expect_kind or (expect_name and a.mod_name != expect_name):
            status = "FAIL"
            ok = False
        if a.target_root != expect_root:
            status = "FAIL"
            ok = False
        if expect_internal is not None and a.internal_root != expect_internal:
            status = "FAIL"
            ok = False
            print("   internal_root: got=%s expect=%s" % (a.internal_root, expect_internal))
        print("[%s] %-20s kind=%-16s name=%-12s target=%s/%s expect=(%s,%s,%s)" % (
            status, fname, a.kind, a.mod_name, a.target_root, a.internal_root,
            expect_kind, expect_root, expect_internal))
        if status == "FAIL":
            print("   got kind=%s name=%s issue=%s" % (a.kind, a.mod_name, a.issue))
            print("   entries=%s" % a.entries[:5])
    print("RESULT:", "ALL PASS" if ok else "HAS FAILURES")
    return ok


if __name__ == "__main__":
    sys.exit(0 if run() else 1)
