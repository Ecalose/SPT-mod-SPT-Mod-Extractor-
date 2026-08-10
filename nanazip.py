# -*- coding: utf-8 -*-
"""调用本机 NanaZip 的封装：自动检测路径、列出压缩包内容、解压。

NanaZip 是 7-Zip 的现代分支，命令行（NanaZipC / NanaZip.Universal.Console）
与 7-Zip 的 7z.exe 完全兼容。因此本模块可处理 zip / rar / 7z 等所有格式，
作为 WinRAR 的可选替代后端（全格式优先）。

检测顺序：
  1. MSIX 安装版：Get-AppxPackage 读 InstallLocation，拼 NanaZip.Universal.Console.exe
  2. 便携版 / scoop / 解压版：常量路径 + PATH 查找（NanaZipC.exe / 7z.exe 等）
  3. 对候选做一次试运行（无参调用），输出含 "NanaZip" 或 "7-Zip" 即认可
"""
import os
import re
import subprocess

from archive_backend import decode as _decode, run as _run, no_window as _no_window
from archive_backend import looks_like_archive, is_zip  # 重新导出，保持外部导入兼容

# 便携版 / 解压版的常见可执行文件名与路径。
# MSIX 版的可执行文件名是 NanaZip.Universal.Console.exe（在受保护的 WindowsApps 下）。
PORTABLE_NAMES = ("NanaZipC.exe", "NanaZip.Universal.Console.exe",
                  "7z.exe", "7za.exe")

KNOWN_PATHS = [
    # 便携版常见解压位置
    r"C:\Program Files\NanaZip\NanaZipC.exe",
    r"C:\Program Files\NanaZip\NanaZip.Universal.Console.exe",
    r"C:\Program Files (x86)\NanaZip\NanaZipC.exe",
    r"D:\Program Files\NanaZip\NanaZipC.exe",
    r"D:\Program Files\NanaZip\NanaZip.Universal.Console.exe",
    # 7-Zip 经典路径（NanaZip CLI 兼容，可作为同名后端）
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
    r"D:\Program Files\7-Zip\7z.exe",
    r"D:\Program Files (x86)\7-Zip\7z.exe",
]

# 7-Zip / NanaZip 退出码 → 中文提示（与 WinRAR 的语义对齐）
EXIT_MSG = {
    0: "成功",
    1: "警告：部分文件未能解压",
    2: "致命错误",
    7: "命令行参数错误",
    8: "内存不足",
    255: "操作被用户中止",
}

# 试运行输出中用以确认是 7-Zip 家族 CLI 的标识
_ID_MARKS = ("nanazip", "7-zip", "7zip")


def _find_msix_console():
    """通过 Get-AppxPackage 定位 MSIX 版 NanaZip 的控制台可执行文件。

    MSIX 执行别名 NanaZip.exe 在子进程中不可用（依赖 shell 激活，会卡死），
    必须用包内真实的 NanaZip.Universal.Console.exe。
    返回 None 表示未安装或无法读取。
    """
    # 优先查 PackageFamilyName 前缀 40174MouriNaruto.NanaZip（官方 MSIX）
    ps = (
        "$ErrorActionPreference='SilentlyContinue';"
        "$p = Get-AppxPackage -Name '40174MouriNaruto.NanaZip';"
        "if (-not $p) { $p = Get-AppxPackage -Name '*NanaZip*' | Select-Object -First 1; }"
        "if ($p -and $p.InstallLocation) {"
        "  $f = Join-Path $p.InstallLocation 'NanaZip.Universal.Console.exe';"
        "  if (Test-Path $f) { Write-Output $f }"
        "}"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, timeout=15, creationflags=_no_window())
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = _decode(proc.stdout).strip()
    # 只接受盘符开头的真实路径
    if out and re.match(r"^[A-Za-z]:[\\/]", out) and os.path.isfile(out):
        return out
    return None


def _probe(exe):
    """对候选 exe 做一次无参调用，确认是 7-Zip 家族 CLI。"""
    if not exe or not os.path.isfile(exe):
        return False
    code, out = _run(exe, [])
    # 无参调用通常返回非 0（用法提示），但会打印 banner
    return any(m in out.lower() for m in _ID_MARKS)


def find_nanazip():
    """自动检测 NanaZip CLI 路径，找不到返回 None。

    顺序：MSIX 包内 console → 已知路径 → PATH。每个候选都做试运行验证，
    避免 PATH 里的执行别名（MSIX alias）在子进程中卡死。
    """
    # 1. MSIX 安装版
    cand = _find_msix_console()
    if cand and _probe(cand):
        return cand

    # 2. 已知路径
    for path in KNOWN_PATHS:
        if os.path.isfile(path) and _probe(path):
            return path

    # 3. PATH 查找（where），逐个试运行——MSIX 执行别名会被试运行排除
    try:
        proc = subprocess.run(
            ["where", *PORTABLE_NAMES],
            capture_output=True, timeout=10, creationflags=_no_window())
    except (subprocess.TimeoutExpired, OSError):
        proc = None
    if proc:
        for line in _decode(proc.stdout).splitlines():
            line = line.strip()
            if line and os.path.isfile(line) and _probe(line):
                return line
    return None


# ---------------------------------------------------------------------------
# 列出 / 解压
# ---------------------------------------------------------------------------

# l -ba 的列输出在"压缩大小"为空时会漏行（已存储文件压缩大小列缺失），
# 改用 `l -slt`（show technical info）：每条目是 `键 = 值` 键值对块，块间以空行
# 分隔（archive 头块前有一行 ----）。Path = 给路径；目录标记因格式而异——
# zip/rar 用 Folder = +，7z 用 Attributes = D——两者都识别。Encrypted = + 标记加密。
_KV_RE = re.compile(r"^\s*(\w[\w ]*?)\s*=\s*(.*)$")


def _parse_slt(out):
    """解析 l -slt 输出：返回 [(path, is_dir, encrypted)] 列表。
    按空行切块；含 'Type =' 的块是 archive 头，跳过。"""
    items = []
    # 以空行（仅空白）切块
    blocks = re.split(r"\n\s*\n", out)
    for blk in blocks:
        path = ""
        is_dir = False
        encrypted = False
        is_header = False
        for line in blk.splitlines():
            m = _KV_RE.match(line)
            if not m:
                continue
            k, v = m.group(1).strip().lower(), m.group(2).strip()
            if k == "path":
                path = v
            elif k == "type":
                is_header = True           # archive 头块（Type = 7z/zip/...）
            elif k == "folder" and v == "+":
                is_dir = True
            elif k == "attributes" and "D" in v:
                is_dir = True              # 7z/rar 用 Attributes = D 标目录
            elif k == "encrypted" and v == "+":
                encrypted = True
        if is_header or not path:
            continue
        items.append((path, is_dir, encrypted))
    return items


def list_with_encrypt(nanazip, archive):
    """一次 CLI 调用同时返回 (文件路径列表, 是否加密)。

    使用 `l -slt`（show technical information）输出：每条目一个键值对块，
    取 Path（跳过 Folder=+ 的目录），路径归一化为正斜杠；
    Encrypted = + 标记加密（任一条目加密即整包视为加密）。
    失败时抛 RuntimeError。
    """
    code, out = _run(nanazip, ["l", "-slt", archive])
    if code != 0:
        raise RuntimeError("列出压缩包失败（退出码 %d）：%s" %
                           (code, EXIT_MSG.get(code, "未知")))
    names = []
    encrypted = False
    for path, is_dir, enc in _parse_slt(out):
        if enc:
            encrypted = True
        if is_dir:
            continue
        name = path.strip().replace("\\", "/").lstrip("./").strip()
        if name and not name.startswith("/"):
            names.append(name)
    return names, encrypted


def list_archive(nanazip, archive):
    """列出压缩包内全部文件路径（不含目录条目），返回 list[str]。失败抛 RuntimeError。"""
    names, _enc = list_with_encrypt(nanazip, archive)
    return names


def is_encrypted(nanazip, archive):
    """检查压缩包是否加密（任意文件条目 Encrypted = +）。失败返回 False。"""
    try:
        _names, enc = list_with_encrypt(nanazip, archive)
        return enc
    except RuntimeError:
        return False


def extract_to(nanazip, archive, dest_dir, progress_cb=None):
    """将压缩包解压到 dest_dir（保留包内相对路径）。
    返回 (ok, message)。"""
    os.makedirs(dest_dir, exist_ok=True)
    # 7-Zip 的 -o 紧跟目录、-y 假定 yes
    cmd = ["x", archive, "-o" + dest_dir, "-y"]
    code, out = _run(nanazip, cmd)
    if code == 0:
        return True, "解压成功"
    return False, EXIT_MSG.get(code, "解压失败（退出码 %d）" % code)


def run_selftest(nanazip=None):
    """命令行自检：检测 NanaZip 并列出示例压缩包（如提供）。"""
    if nanazip is None:
        nanazip = find_nanazip()
    return "NanaZip: %s" % nanazip
