# -*- coding: utf-8 -*-
"""调用本机 WinRAR 的封装：自动检测路径、列出压缩包内容、解压。"""
import os
import winreg

from archive_backend import decode as _decode, run as _run
from archive_backend import looks_like_archive, is_zip  # 重新导出，保持外部导入兼容

KNOWN_PATHS = [
    r"C:\Program Files\WinRAR\WinRAR.exe",
    r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
    r"D:\Program Files\WinRAR\WinRAR.exe",
    r"D:\Program Files (x86)\WinRAR\WinRAR.exe",
]

EXIT_MSG = {
    0: "成功",
    1: "警告：部分文件未能解压",
    2: "致命错误",
    3: "压缩包头损坏",
    4: "CRC 校验失败（文件损坏）",
    5: "拒绝访问",
    6: "内存不足",
    7: "操作被用户中止",
    8: "密码不正确或压缩包已加密",
    9: "缺少分卷",
    10: "无法创建目标文件夹",
    11: "磁盘空间不足",
    12: "压缩包头 CRC 错误",
}


def _find_winrar_in_registry():
    for hive, sub in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinRAR"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\WinRAR"),
                      (winreg.HKEY_CURRENT_USER, r"Software\WinRAR")):
        try:
            with winreg.OpenKey(hive, sub) as key:
                for name in ("exe64", "exe"):
                    try:
                        value, _ = winreg.QueryValueEx(key, name)
                    except OSError:
                        continue
                    if value and os.path.isfile(value):
                        return value
        except OSError:
            continue
    return None


def find_winrar():
    """自动检测 WinRAR 路径，找不到返回 None。"""
    found = _find_winrar_in_registry()
    if found:
        return found
    for path in KNOWN_PATHS:
        if os.path.isfile(path):
            return path
    return None


def find_unrar(winrar):
    """在 WinRAR 目录下找控制台工具 UnRAR.exe（Rar.exe 兜底）。"""
    if not winrar:
        return None
    d = os.path.dirname(winrar)
    for name in ("UnRAR.exe", "Rar.exe"):
        path = os.path.join(d, name)
        if os.path.isfile(path):
            return path
    return None


def list_archive(winrar, archive):
    """列出压缩包内全部文件路径（不含目录条目），返回 list[str]。
    使用控制台工具 UnRAR/Rar 输出（WinRAR.exe 不支持 lb 命令）。
    失败时抛 RuntimeError。"""
    base = os.path.basename(winrar).lower()
    if base in ("unrar.exe", "rar.exe"):
        exe = winrar
    else:
        exe = find_unrar(winrar)
    if not exe:
        raise RuntimeError("找不到可用的列表工具（UnRAR.exe），无法预览压缩包内容")
    code, out = _run(exe, ["lb", archive])
    if code != 0:
        raise RuntimeError("列出压缩包失败（退出码 %d）：%s" % (code, EXIT_MSG.get(code, "未知")))
    names = []
    for line in out.splitlines():
        name = line.strip().replace("\\", "/").lstrip("./").strip()
        if name and not name.startswith("/"):
            names.append(name)
    return names


def extract_to(winrar, archive, dest_dir, progress_cb=None):
    """将压缩包解压到 dest_dir（保留包内相对路径）。
    返回 (ok, message)。"""
    os.makedirs(dest_dir, exist_ok=True)
    dest = dest_dir if dest_dir.endswith(("\\", "/")) else dest_dir + os.sep
    cmd = ["x", "-y", "-ibck", "-o+", "-ai", "-cfg-", archive, dest]
    code, out = _run(winrar, cmd)
    if code == 0:
        return True, "解压成功"
    return False, EXIT_MSG.get(code, "解压失败（退出码 %d）" % code)


def run_selftest(winrar=None):
    """命令行自检：检测 WinRAR 并列出示例压缩包（如提供）。"""
    winrar = winrar or find_winrar()
    if not winrar:
        return "未找到 WinRAR"
    return "WinRAR: %s" % winrar
