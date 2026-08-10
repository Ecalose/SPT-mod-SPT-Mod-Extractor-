# -*- coding: utf-8 -*-
"""解压后端公共工具：控制台隐藏、输出解码、子进程执行、格式识别。

winrar.py / nanazip.py 共用的底层 helper 集中在此，避免三处重复定义。
各后端特有的路径检测、列表/解压命令构造仍保留在各自模块。
"""
import re
import subprocess


def no_window():
    """隐藏子进程控制台窗口（黑框）。非 Windows 平台返回 0。"""
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def decode(raw):
    """按 utf-8 → gbk → latin-1 链解码子进程输出，失败兜底 replace。"""
    if not raw:
        return ""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return raw.decode("utf-8", errors="replace")


def run(exe, args):
    """执行 exe + args，返回 (returncode, decoded_stdout+stderr)。隐藏控制台。"""
    cmd = [exe] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=3600,
                              creationflags=no_window())
    except subprocess.TimeoutExpired:
        return -1, "超时"
    except OSError as exc:
        return -1, str(exc)
    out = decode(proc.stdout) + decode(proc.stderr)
    return proc.returncode, out


def is_zip(path):
    return path.lower().endswith(".zip")


def looks_like_archive(path):
    """判断路径是否为受支持的压缩包（zip/rar/7z，大小写不敏感）。"""
    return path.lower().endswith((".zip", ".rar", ".7z"))
