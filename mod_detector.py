# -*- coding: utf-8 -*-
"""压缩包结构识别、异常检测、软链接处理、mod 扫描与配对。"""
import os
import re
import zipfile
from dataclasses import dataclass, field

# 结构根目录：出现这些顶层目录说明压缩包自带游戏目录结构
STRUCTURAL_ROOTS = {"user", "bepinex", "mods", "spt", "spt_runtime", "game"}

# 配对时忽略的通用词
STOP_TOKENS = {"server", "client", "backend", "mod", "mods", "spt", "aki",
               "bepinex", "plugin", "plugins", "storage", "config", "data"}

# 判定类型
KIND_SERVER = "server"              # user/mods/... → 服务端根
KIND_CLIENT = "client"              # BepInEx/... → 客户端根
KIND_LEGACY = "legacy"              # mods/<name>/... → 服务端根
KIND_SERVER_NAMED = "server_named"  # 单顶层文件夹(含 config.json/config) → user/mods/<name>
KIND_CLIENT_FOLDER = "client_folder"  # 单顶层文件夹(仅 dll) → 需确认
KIND_SERVER_LOOSE = "server_loose"  # 顶层散装 dll+config.json → user/mods/<dll名>
KIND_CLIENT_LOOSE = "client_loose"  # 顶层散装 dll → plugins
KIND_MIXED = "mixed"                # 多顶层目录 → 需确认
KIND_UNKNOWN = "unknown"
KIND_TRAVERSAL = "traversal"
KIND_EMPTY = "empty"
KIND_NOT_ARCHIVE = "not_archive"
KIND_NEEDS_STAGING = "needs_staging"  # 7z 等无法预览 → 先解压到临时目录再分析
KIND_COMBO = "combo"                # 客户端+服务端组合包（BepInEx + SPT/user/mods）
KIND_SERVER_ROOT = "server_root"    # 整个包是 SPT 根目录内容（去掉 SPT/ 前缀）

KIND_NAMES = {
    KIND_SERVER: "SPT 服务端 mod",
    KIND_CLIENT: "客户端插件 (BepInEx)",
    KIND_LEGACY: "旧版 SPT mod",
    KIND_SERVER_NAMED: "SPT 服务端 mod（单文件夹）",
    KIND_CLIENT_FOLDER: "插件/服务端 mod（待确认）",
    KIND_SERVER_LOOSE: "服务端散装 mod",
    KIND_CLIENT_LOOSE: "客户端散装插件",
    KIND_MIXED: "多个 mod 混装（待确认）",
    KIND_UNKNOWN: "无法识别",
    KIND_TRAVERSAL: "危险：路径穿越",
    KIND_EMPTY: "空压缩包",
    KIND_NOT_ARCHIVE: "不是支持的压缩包",
    KIND_NEEDS_STAGING: "需临时解压检查",
    KIND_COMBO: "客户端+服务端组合包",
    KIND_SERVER_ROOT: "SPT 服务端根目录内容",
}

SERVER_PREFIXES = {"spt", "user", "mods"}
SERVER_ROOT_PROXIES = {"spt", "spt_runtime"}  # 内容整体解压到服务端根（含 SPT/ 与 SPT_Runtime/）
CLIENT_ROOT_SIBLINGS = {"escapefromtarkov_data"}  # 作为客户端根同级目录原样并入
BENIGN_NAMES = {"readme", "license", "changelog", "donate", "install", "installation",
                "requirements", "notice", "upgrade"}
BENIGN_EXTS = (".txt", ".md", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".ico",
               ".html", ".htm", ".url", ".desktop")


def _benign_top(name):
    """顶层条目是否为可忽略的说明性文件（readme/license/图片等）。"""
    if "/" in name:
        return False
    return name in BENIGN_NAMES or name.endswith(BENIGN_EXTS)


@dataclass
class Analysis:
    archive: str
    entries: list = field(default_factory=list)
    kind: str = KIND_UNKNOWN
    mod_name: str = ""
    target_root: str = ""           # 'client' / 'server'
    needs_confirm: bool = False
    confirm_choices: list = field(default_factory=list)  # 确认对话框可选项
    issue: str = ""                 # 需要提醒用户的问题
    encrypted: bool = False
    summary: list = field(default_factory=list)  # 结构摘要行
    target_dir: str = ""            # 计算出的最终解压目录
    direct_extract: bool = True     # True=直接解压到目标根；False=先解压到临时再移动
    internal_root: str = ""         # 1:1 结构时，mod 在目标根下的相对目录
    strip_prefixes: list = field(default_factory=list)  # 需从压缩包顶层剥离的目录名


def _norm_path(name):
    name = name.strip().replace("\\", "/")
    while name.startswith("./"):
        name = name[2:]
    return name.rstrip("/")


def _is_dir_entry(name):
    return name.endswith("/") or name.endswith("\\")


def _check_traversal(entries):
    bad = [e for e in entries if ".." in e.split("/") or e.startswith("/")]
    return bad


def _zip_entries(archive):
    names = []
    try:
        with zipfile.ZipFile(archive) as zf:
            infos = zf.infolist()
            for info in infos:
                name = _norm_path(info.filename)
                if not name:
                    continue
                if info.is_dir():
                    continue
                names.append(name)
    except zipfile.BadZipFile:
        raise RuntimeError("不是有效的 zip 压缩包")
    except OSError as exc:
        raise RuntimeError("无法读取压缩包：%s" % exc)
    return names


def _top_folders(entries):
    tops = set()
    for e in entries:
        head = e.split("/")[0]
        if head:
            tops.add(head)
    return tops


def _single_folder(entries):
    tops = _top_folders(entries)
    if len(tops) == 1:
        return next(iter(tops))
    return None


def _files_under(entries, folder):
    prefix = folder.rstrip("/") + "/"
    return [e for e in entries if e.lower().startswith(prefix)]


def _has_dll(entries):
    return any(e.lower().endswith(".dll") for e in entries)


def _maybe_unwrap(entries, _depth=0, _stripped=None):
    """剥离外层包裹目录：KeyTips/BepInEx/... → BepInEx/...（仅当内层是结构布局时）。
    返回 (新条目, 剥离的顶层目录名列表)。"""
    if _depth >= 3:
        return entries, (_stripped or [])
    lc = [e.lower() for e in entries]
    tops = _top_folders(lc)
    effective = {t for t in tops if not _benign_top(t)}
    if len(effective) != 1:
        return entries, (_stripped or [])
    outer = next(iter(effective))
    if outer in STRUCTURAL_ROOTS or outer in SERVER_PREFIXES:
        return entries, (_stripped or [])
    inner = [e for e, l in zip(entries, lc) if l.startswith(outer + "/")]
    if not inner:
        return entries, (_stripped or [])
    unwrapped = [e[len(outer) + 1:] for e in inner]
    inner_eff = {t for t in _top_folders([x.lower() for x in unwrapped]) if not _benign_top(t)}
    if inner_eff & STRUCTURAL_ROOTS:
        stripped = list(_stripped or [])
        stripped.append(outer)
        return _maybe_unwrap(unwrapped, _depth + 1, stripped)
    return entries, (_stripped or [])


def _classify(entries):
    """返回 (kind, mod_name, issue)。
    entries 应已由 analyze_entries 调用 _maybe_unwrap 剥过包裹目录。"""
    if not entries:
        return KIND_EMPTY, "", ""
    bad = _check_traversal(entries)
    if bad:
        return KIND_TRAVERSAL, "", "发现 %d 个包含路径穿越的文件" % len(bad)

    lc = [e.lower() for e in entries]
    tops = _top_folders(lc)
    effective = {t for t in tops if not _benign_top(t)}
    structural = effective & STRUCTURAL_ROOTS

    def under(prefix):
        return [e for e, l in zip(entries, lc) if l.startswith(prefix)]

    # 组合包：BepInEx + 服务端部分（spt/user/mods 或 SPT_Runtime 等代理根），
    # 可附带客户端根同级目录（EscapeFromTarkov_Data 等），其余仅良性文件
    server_proxy = effective & (SERVER_PREFIXES | SERVER_ROOT_PROXIES)
    client_sib = effective & CLIENT_ROOT_SIBLINGS
    if "bepinex" in effective and server_proxy and \
            (effective - {"bepinex"} - client_sib) <= (server_proxy | client_sib):
        return KIND_COMBO, "", "包含客户端（BepInEx）与服务端（%s）两部分" % "/".join(sorted(server_proxy))

    # 整个包是 SPT 根目录内容（顶层只有 SPT/ 或 SPT_Runtime/）
    if effective <= SERVER_ROOT_PROXIES and effective:
        proxy = next(iter(effective))
        spt_files = under(proxy + "/")
        if not spt_files:
            return KIND_UNKNOWN, "", "顶层为 %s/ 但内部无文件" % proxy
        # 收集 <proxy>/user/mods/<name> 下出现的全部 mod 名
        names = set()
        for e in spt_files:
            parts = e.split("/")
            if len(parts) >= 4 and parts[1].lower() == "user" and parts[2].lower() == "mods":
                names.add(parts[3])
        if len(names) == 1:
            name = next(iter(names))
            return KIND_SERVER_ROOT, name, "顶层为 %s/ 目录，将去除该前缀安装到服务端根目录" % proxy
        # 多 mod 或无 user/mods 结构：交由 analyze_entries 走整体解压+确认
        return KIND_SERVER_ROOT, "", (("顶层为 %s/ 目录，含 %d 个 mod，将整体解压到服务端根目录" % (proxy, len(names)))
                                      if names else "顶层为 %s/ 目录，将去除该前缀安装到服务端根目录" % proxy)

    if "user" in structural:
        mods_files = under("user/mods/")
        other = [e for e, l in zip(entries, lc) if not l.startswith("user/mods/") and not _benign_top(l.split("/")[0])]
        if not mods_files:
            return KIND_UNKNOWN, "", "包含 user/ 目录但未找到 user/mods/ 结构"
        name = ""
        if mods_files:
            parts = mods_files[0].split("/")
            if len(parts) >= 3 and parts[1].lower() == "mods":
                name = parts[2]
        if other:
            return KIND_MIXED, name or "user/mods", "user/mods 之外还有 %d 个文件，结构异常" % len(other)
        return KIND_SERVER, name, ""

    if "bepinex" in structural:
        plugin_files = under("bepinex/plugins/")
        other = [e for e, l in zip(entries, lc) if not l.startswith("bepinex/") and not _benign_top(l.split("/")[0])]
        if not plugin_files and not _has_dll(under("bepinex/plugins/")):
            return KIND_UNKNOWN, "", "包含 BepInEx/ 但未找到 plugins/ 结构"
        name = ""
        if plugin_files:
            parts = plugin_files[0].split("/")
            if len(parts) >= 3:
                name = parts[2]
        if other:
            return KIND_MIXED, name or "bepinex", "BepInEx 之外还有 %d 个文件，结构异常" % len(other)
        return KIND_CLIENT, name, ""

    if "mods" in structural:
        name = ""
        mfiles = under("mods/")
        if mfiles:
            parts = mfiles[0].split("/")
            if len(parts) >= 2:
                name = parts[1]
        other = [e for e, l in zip(entries, lc) if not l.startswith("mods/") and not _benign_top(l.split("/")[0])]
        if other:
            return KIND_MIXED, name or "mods", "mods/ 之外还有 %d 个文件" % len(other)
        return KIND_LEGACY, name, ""

    if len(effective) == 1:
        folder = next(iter(effective))
        orig = entries[0].split("/")[0]
        sub = [e[len(folder) + 1:] for e, l in zip(entries, lc) if l.startswith(folder + "/")]
        root_config = any(n.lower() == "config.json" for n in sub)
        has_cfg_folder = any(n.lower().startswith("config/") for n in sub)
        has_pkg = any(n.lower() == "package.json" for n in sub)
        if root_config or has_pkg:
            return KIND_SERVER_NAMED, orig, ""
        if has_cfg_folder and _has_dll(sub):
            return KIND_SERVER_NAMED, orig, ""
        if _has_dll(sub):
            return KIND_CLIENT_FOLDER, orig, ""
        return KIND_UNKNOWN, orig, "顶层文件夹内容无法识别为 mod"
    if len(effective) == 0:
        return KIND_UNKNOWN, "", "压缩包内没有可识别的文件"

    has_dll = _has_dll(entries)
    root_config = any(e.lower() == "config.json" for e in entries)
    if has_dll and root_config:
        dlls = [os.path.splitext(os.path.basename(e))[0] for e in entries
                if e.lower().endswith(".dll")]
        return KIND_SERVER_LOOSE, (dlls[0] if dlls else "mod"), ""
    if has_dll:
        return KIND_CLIENT_LOOSE, "", ""
    return KIND_MIXED, "", "存在 %d 个互不相关的顶层目录，无法自动判断" % len(effective)


def _summarize(entries, limit=20):
    lines = []
    for e in entries[:limit]:
        lines.append("  " + e)
    if len(entries) > limit:
        lines.append("  …共 %d 个文件" % len(entries))
    return lines


def structure_brief(entries, kind, mod_name):
    """紧凑结构摘要：顶层文件夹 → 目标位置（不逐个列出文件）。"""
    lc = [e.lower() for e in entries]

    def items_under(prefix):
        tops = set()
        for e, l in zip(entries, lc):
            if not l.startswith(prefix):
                continue
            rest = e[len(prefix):]
            if not rest:
                continue
            tops.add(rest.split("/")[0])
        return sorted(tops)

    def fmt(names, limit=6):
        if not names:
            return "（无）"
        shown = names[:limit]
        tail = " 等 %d 项" % len(names) if len(names) > limit else ""
        return "、".join(shown) + tail

    rows = []
    if kind == KIND_COMBO:
        cli = items_under("bepinex/plugins/")
        srv = (items_under("spt/user/mods/") or items_under("spt_runtime/user/mods/")
               or items_under("user/mods/"))
        rows.append("客户端 %d 项 → BepInEx\\plugins（%s）" % (len(cli), fmt(cli)))
        rows.append("服务端 %d 项 → user\\mods（%s）" % (len(srv), fmt(srv)))
    elif kind == KIND_SERVER:
        mods = items_under("user/mods/")
        rows.append("服务端 %d 个 mod → user\\mods（%s）" % (len(mods), fmt(mods)))
    elif kind == KIND_CLIENT:
        plugs = items_under("bepinex/plugins/")
        rows.append("客户端 %d 项 → BepInEx\\plugins（%s）" % (len(plugs), fmt(plugs)))
    elif kind == KIND_LEGACY:
        mods = items_under("mods/")
        rows.append("旧版 %d 个 mod → mods\\（%s）" % (len(mods), fmt(mods)))
    elif kind == KIND_SERVER_ROOT:
        mods = items_under("spt/user/mods/") or items_under("spt_runtime/user/mods/")
        rows.append("服务端 %d 个 mod → user\\mods（%s）" % (len(mods), fmt(mods)))
    elif kind == KIND_SERVER_NAMED:
        rows.append("1 个文件夹（%s）→ user\\mods\\%s" % (mod_name, mod_name))
    elif kind == KIND_CLIENT_FOLDER:
        rows.append("1 个文件夹（%s）→ 安装位置待确认" % mod_name)
    elif kind == KIND_SERVER_LOOSE:
        rows.append("%s.dll + config.json → user\\mods\\%s" % (mod_name, mod_name))
    elif kind == KIND_CLIENT_LOOSE:
        dlls = [os.path.basename(e) for e in entries if e.lower().endswith(".dll")]
        rows.append("散装插件 %s → BepInEx\\plugins" % "、".join(dlls))
    else:
        rows.append("结构：%s" % "、".join(e for e in entries[:8]))
    rows.append("共 %d 个文件" % len(entries))
    return rows


def analyze_archive(winrar_path, archive, list_fn=None, nanazip_path=None):
    """分析压缩包结构。返回 Analysis。

    后端选择（NanaZip 全格式优先）：
      - nanazip_path 给定且有效时，rar/7z 都用 NanaZip 直接 list（7z 不再暂存）。
      - 否则维持原行为：rar 用 WinRAR list，7z 走 KIND_NEEDS_STAGING 暂存。
      - zip 一律用 zipfile 原生（更快、加密检测直接）。
    list_fn 用于注入（测试），覆盖 rar 的 list 后端。
    """
    a = Analysis(archive=archive)
    ext = os.path.splitext(archive)[1].lower()
    if ext not in (".zip", ".rar", ".7z"):
        a.kind = KIND_NOT_ARCHIVE
        a.issue = "仅支持 zip / rar / 7z 格式（需要本机安装 WinRAR 或 NanaZip）"
        return a

    if ext == ".zip":
        try:
            a.entries = _zip_entries(archive)
        except RuntimeError as exc:
            a.kind = KIND_UNKNOWN
            a.issue = str(exc)
            return a
        try:
            with zipfile.ZipFile(archive) as zf:
                infos = zf.infolist()
                a.encrypted = any((i.flag_bits & 0x1) for i in infos)
        except (zipfile.BadZipFile, OSError):
            pass
        return analyze_entries(a, a.entries)
    if ext == ".rar":
        # 优先 NanaZip，回退 WinRAR
        if nanazip_path:
            try:
                from nanazip import list_with_encrypt as _nz_list_enc
                a.entries, a.encrypted = _nz_list_enc(nanazip_path, archive)
                return analyze_entries(a, a.entries)
            except RuntimeError:
                pass  # 回退 WinRAR / 暂存
        try:
            a.entries = list_fn(archive) if list_fn else _winrar_list(winrar_path, archive)
        except RuntimeError as exc:
            if not list_fn:
                a.kind = KIND_NEEDS_STAGING
                a.issue = "无法预览压缩包内容（%s），将先解压到临时目录检查后再安装" % exc
                return a
            a.kind = KIND_UNKNOWN
            a.issue = str(exc)
            return a
        return analyze_entries(a, a.entries)

    # .7z：NanaZip 可直接 list，否则暂存
    if nanazip_path:
        try:
            from nanazip import list_with_encrypt as _nz_list_enc
            a.entries, a.encrypted = _nz_list_enc(nanazip_path, archive)
            return analyze_entries(a, a.entries)
        except RuntimeError:
            pass  # 回退暂存
    a.kind = KIND_NEEDS_STAGING
    a.issue = "7z 格式无法直接预览内容，将先解压到临时目录检查，确认安全后才安装到游戏目录"
    return a


def walk_dir_entries(root):
    """遍历目录返回相对路径列表（模拟压缩包条目）。"""
    entries = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            entries.append(rel)
    return sorted(entries)


def analyze_entries(a, entries):
    """根据文件条目列表填充 Analysis（分类 + 目标计算）。"""
    unwrapped, stripped = _maybe_unwrap(entries)
    a.entries = unwrapped
    a.strip_prefixes = stripped
    a.kind, a.mod_name, a.issue = _classify(a.entries)
    a.summary = structure_brief(a.entries, a.kind, a.mod_name)
    if stripped:
        a.direct_extract = False

    if a.kind == KIND_SERVER:
        a.target_root = "server"
        mods = set()
        for e in a.entries:
            parts = e.split("/")
            if len(parts) >= 3 and parts[1].lower() == "mods":
                mods.add(parts[2])
        if len(mods) == 1:
            mod = next(iter(mods))
            a.mod_name = mod
            a.internal_root = os.path.join("user", "mods", mod)
            a.target_dir = _join_root("server", a.internal_root)
        else:
            a.internal_root = ""
            a.target_dir = _join_root("server", "")
            a.needs_confirm = True
            a.confirm_choices = ["整体解压到服务端根目录", "取消"]
            a.issue = "压缩包内含 %d 个 mod（%s）" % (len(mods), ", ".join(sorted(mods)))
    elif a.kind == KIND_CLIENT:
        a.target_root = "client"
        tops = set()
        for e in a.entries:
            parts = e.split("/")
            if len(parts) < 2 or parts[0].lower() != "bepinex":
                continue
            if parts[1].lower() == "plugins":
                if len(parts) > 2 and not parts[2].lower().endswith(".dll"):
                    tops.add(parts[2])
                else:
                    tops.add("__loose__")
            else:
                tops.add(parts[1])
        if tops == {"__loose__"}:
            a.internal_root = os.path.join("BepInEx", "plugins")
            a.target_dir = _join_root("client", a.internal_root)
        elif len(tops) == 1 and "__loose__" not in tops:
            mod = next(iter(tops))
            a.internal_root = os.path.join("BepInEx", "plugins", mod)
            a.target_dir = _join_root("client", a.internal_root)
        else:
            a.internal_root = ""
            a.target_dir = _join_root("client", "")
            a.needs_confirm = True
            a.confirm_choices = ["整体解压到客户端根目录", "取消"]
            a.issue = "压缩包内含多个插件组件，将整体解压到客户端根目录"
    elif a.kind == KIND_LEGACY:
        a.target_root = "server"
        a.internal_root = os.path.join("mods", a.mod_name)
        a.target_dir = _join_root("server", a.internal_root)
    elif a.kind == KIND_SERVER_NAMED:
        a.target_root = "server"
        a.internal_root = os.path.join("user", "mods", a.mod_name)
        a.target_dir = _join_root("server", a.internal_root)
        a.direct_extract = False
    elif a.kind == KIND_CLIENT_FOLDER:
        a.target_root = "client"
        a.internal_root = os.path.join("BepInEx", "plugins", a.mod_name)
        a.target_dir = _join_root("client", a.internal_root)
        a.needs_confirm = True
        a.confirm_choices = ["客户端", "服务端", "取消"]
        a.direct_extract = False
    elif a.kind == KIND_SERVER_LOOSE:
        a.target_root = "server"
        a.internal_root = os.path.join("user", "mods", a.mod_name)
        a.target_dir = _join_root("server", a.internal_root)
        a.needs_confirm = True
        a.confirm_choices = ["安装", "改名", "取消"]
        a.direct_extract = False
    elif a.kind == KIND_CLIENT_LOOSE:
        a.target_root = "client"
        a.internal_root = os.path.join("BepInEx", "plugins")
        a.target_dir = _join_root("client", a.internal_root)
        a.needs_confirm = True
        a.confirm_choices = ["解压到 BepInEx/plugins"]
        a.direct_extract = False
    elif a.kind == KIND_MIXED:
        a.needs_confirm = True
        a.confirm_choices = ["取消"]
        a.direct_extract = False
    elif a.kind == KIND_COMBO:
        a.target_root = "combo"
        a.internal_root = ""
        a.target_dir = ""
        a.needs_confirm = True
        a.confirm_choices = ["安装", "取消"]
        a.direct_extract = False
        cli = sum(1 for e in a.entries if e.lower().startswith("bepinex/"))
        srv = sum(1 for e in a.entries
                  if e.lower().startswith(tuple(p + "/" for p in (SERVER_PREFIXES | SERVER_ROOT_PROXIES))))
        a.issue = "客户端 %d 个文件 → BepInEx；服务端 %d 个文件 → SPT" % (cli, srv)
    elif a.kind == KIND_SERVER_ROOT:
        a.target_root = "server"
        a.internal_root = ""
        a.target_dir = _join_root("server", "")
        a.direct_extract = False
        if not a.mod_name:
            # 多 mod 或无标准 user/mods 结构：整体解压到服务端根，需用户确认
            a.needs_confirm = True
            a.confirm_choices = ["整体解压到服务端根目录", "取消"]
    return a


def _winrar_list(winrar_path, archive):
    from winrar import list_archive
    return list_archive(winrar_path, archive)


def _join_root(root, rel):
    return {"client": "<客户端根>", "server": "<服务端根>"}[root]


def resolve_targets(cfg, analysis):
    """把 <客户端根>/<服务端根> 占位符换成真实路径，填充分析结果的目标目录。"""
    root_map = {"client": cfg["client_root"], "server": cfg["server_root"]}
    base = root_map.get(analysis.target_root, "")
    if analysis.internal_root:
        analysis.target_dir = os.path.join(base, *analysis.internal_root.split(os.sep))
    else:
        analysis.target_dir = base
    return analysis


# ---------------------------------------------------------------------------
# 软链接处理
# ---------------------------------------------------------------------------

def is_link(path):
    try:
        if os.path.isjunction(path):
            return True
        if os.path.islink(path):
            return True
    except OSError:
        return False
    return False


def link_target(path):
    try:
        if os.path.isjunction(path):
            return os.path.realpath(path)
        target = os.readlink(path)
        return target[len("\\\\?\\"):] if target.startswith("\\\\?\\") else target
    except OSError:
        return ""


def find_links_under(root, max_depth=4):
    """递归查找 root 下的软链接/联接（最多 max_depth 层）。"""
    found = []
    root = root.rstrip("\\/")
    base_depth = root.count(os.sep)

    def walk(current, depth):
        if depth > max_depth:
            return
        try:
            with os.scandir(current) as it:
                for entry in it:
                    full = os.path.join(current, entry.name)
                    if is_link(full):
                        found.append(full)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        walk(full, depth + 1)
        except OSError:
            pass

    if os.path.isdir(root):
        walk(root, 0)
    return found


def remove_link(path):
    """删除软链接/联接本身（不删除链接指向的内容）。"""
    if not is_link(path):
        return
    try:
        if os.path.islink(path):
            if os.path.isdir(path):
                os.rmdir(path)
            else:
                os.remove(path)
        elif os.path.isjunction(path):
            os.rmdir(path)
    except OSError:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 扫描已安装 mod 与配对
# ---------------------------------------------------------------------------

@dataclass
class ModEntry:
    name: str
    location: str          # 客户端 / 服务端 / 仓库(客户端) / 仓库(服务端)
    path: str
    is_link: bool = False
    link_target: str = ""
    kind: str = "folder"   # folder / plugin(dll)
    pair: str = ""         # 配对关系描述


def _scan_dir(base, location, only_folders=False, only_dlls=False, skip_prefix=""):
    out = []
    if not base or not os.path.isdir(base):
        return out
    try:
        with os.scandir(base) as it:
            for entry in it:
                name = entry.name
                if skip_prefix and os.path.normcase(os.path.join(base, name)).startswith(
                        os.path.normcase(skip_prefix)):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    out.append(ModEntry(name=name, location=location, path=entry.path,
                                        is_link=is_link(entry.path),
                                        link_target=link_target(entry.path), kind="folder"))
                elif only_folders:
                    continue
                elif name.lower().endswith(".dll"):
                    out.append(ModEntry(name=os.path.splitext(name)[0], location=location,
                                        path=entry.path, is_link=is_link(entry.path),
                                        link_target=link_target(entry.path), kind="plugin"))
    except OSError:
        pass
    return out


def scan_installed_mods(cfg):
    """扫描四个位置，返回 ModEntry 列表（自动排除备份目录）。"""
    client_root = cfg["client_root"]
    server_root = cfg["server_root"]
    backup_dir = cfg.get("backup_dir", "")
    entries = []
    entries += _scan_dir(os.path.join(client_root, "BepInEx", "plugins"), "客户端",
                         skip_prefix=backup_dir)
    entries += _scan_dir(os.path.join(client_root, "BepInEx", "mods_storage", "plugins"),
                         "仓库(客户端)", only_folders=True, skip_prefix=backup_dir)
    entries += _scan_dir(os.path.join(server_root, "user", "mods"), "服务端",
                         skip_prefix=backup_dir)
    entries += _scan_dir(os.path.join(server_root, "user", "mods_storage"),
                         "仓库(服务端)", only_folders=True, skip_prefix=backup_dir)
    return entries


def _normalize(name):
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def _tokenize(name):
    parts = re.split(r"[^a-z0-9]+", name.lower())
    return [t for t in set(parts) if t and t not in STOP_TOKENS]


def _pair_score(a, b, freq):
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        return 0.85
    ta, tb = set(_tokenize(a)), set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if small <= large:
        return 0.9
    common = (ta & tb) - {t for t in (ta & tb) if freq.get(t, 0) >= 3}
    if common:
        return 0.65
    for k in range(min(len(na), len(nb)), 7, -1):
        if na[-k:] == nb[-k:]:
            return 0.7
    return 0.0


def pair_mods(entries):
    """自动配对服务端↔客户端，返回配对列表 [(client_entry, server_entry, score)]。"""
    servers = [e for e in entries if e.location in ("服务端", "仓库(服务端)")]
    clients = [e for e in entries if e.location in ("客户端", "仓库(客户端)")]
    freq = {}
    for e in servers + clients:
        for t in _tokenize(e.name):
            freq[t] = freq.get(t, 0) + 1
    pairs = []
    used_c, used_s = set(), set()
    candidates = []
    for c in clients:
        for s in servers:
            score = _pair_score(c.name, s.name, freq)
            if score >= 0.65:
                candidates.append((score, c, s))
    candidates.sort(key=lambda t: -t[0])
    for score, c, s in candidates:
        if c.path in used_c or s.path in used_s:
            continue
        used_c.add(c.path)
        used_s.add(s.path)
        pairs.append((c, s, score))
    return pairs


def _conf_label(score):
    if score >= 0.95:
        return "匹配"
    if score >= 0.8:
        return "高"
    return "中"
