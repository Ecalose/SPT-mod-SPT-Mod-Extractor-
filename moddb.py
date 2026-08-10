# -*- coding: utf-8 -*-
"""mod 记录数据库：持久化安装记录，支持分类、版本、时间、收藏、存档类标记、备份状态。"""
import json
import os
import re
import time

LOCALIZE_KEYWORDS = ("zh-cn", "zh_cn", "chinese", "汉化", "翻译", "translation",
                     "locale", "localization", "language", "lang", "spellcheck")

CATEGORIES = ("双端", "客户端", "服务端", "汉化", "其他")

SORT_KEYS = {"name": "名称", "category": "分类", "time": "安装时间",
             "version": "版本", "state": "状态"}


def version_from_text(text):
    m = re.findall(r"(?<![A-Za-z0-9])v?(\d+\.\d+(?:\.\d+)*)", text)
    return m[-1] if m else ""


def version_from_dir(directory):
    """从已安装目录读取版本（package.json / config.json / config.jsonc）。"""
    for fname in ("package.json", "config.json", "config.jsonc"):
        path = os.path.join(directory, fname)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                v = data.get("version") or data.get("Version")
                if v:
                    return str(v)
        except (OSError, ValueError):
            pass
    return ""


def categorize(name, paths, cfg):
    """按名称与路径判定分类（服务端根优先匹配，因其可能位于客户端根之下）。"""
    low = name.lower()
    if any(k in low for k in LOCALIZE_KEYWORDS):
        return "汉化"
    roots = []
    for key, side in (("client_root", "client"), ("server_root", "server")):
        root = (cfg.get(key) or "").lower().rstrip("\\/")
        if root:
            roots.append((root, side))
    roots.sort(key=lambda x: -len(x[0]))
    on_client = on_server = False
    for p in paths or []:
        pl = p.lower()
        for root, side in roots:
            if pl.startswith(root):
                if side == "client":
                    on_client = True
                else:
                    on_server = True
                break
    if on_client and on_server:
        return "双端"
    if on_client:
        return "客户端"
    if on_server:
        return "服务端"
    return "其他"


class ModDB:
    """{name: {name, category, version, install_time, source, favorite, save_critical,
              state(active/backed_up), backup_paths, paths: []}}"""

    def __init__(self, path):
        self.path = path
        self.mods = {}
        self.last_error = ""
        self._batch = False  # 批量模式：抑制每次 setter 的全量写盘，由 flush() 统一提交
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.mods = data.get("mods", {}) or {}
        except (OSError, ValueError):
            self.mods = {}

    def save(self):
        """全量写盘。批量模式下（_batch=True）为空操作，改由 flush() 统一提交。"""
        if self._batch:
            return True
        return self.flush()

    def begin_batch(self):
        """进入批量模式：之后的 setter 不立即写盘，直到 flush()。"""
        self._batch = True

    def flush(self):
        """提交积攒的变更并退出批量模式。返回是否写盘成功。"""
        self._batch = False
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump({"mods": self.mods}, f, ensure_ascii=False, indent=2)
            self.last_error = ""
            return True
        except OSError as exc:
            # 写入失败不抛异常（避免拖垮整个操作），但记录错误供上层提示。
            self.last_error = str(exc)
            import sys
            print("ModDB 保存失败：%s" % exc, file=sys.stderr)
            return False

    def upsert(self, name, paths, cfg, version="", source="install"):
        if not name:
            return
        now = time.strftime("%Y-%m-%d %H:%M")
        rec = self.mods.get(name)
        if rec:
            rec["version"] = version or rec.get("version", "")
            rec["category"] = categorize(name, paths, cfg)
            rec["source"] = source
            rec["install_time"] = now
            rec["paths"] = sorted(set(list(rec.get("paths", [])) + list(paths)))
        else:
            self.mods[name] = {
                "name": name,
                "category": categorize(name, paths, cfg),
                "version": version,
                "install_time": now,
                "source": source,
                "favorite": False,
                "save_critical": False,
                "state": "active",
                "backup_paths": [],
                "paths": sorted(set(paths)),
            }
        self.save()

    def remove(self, name):
        self.mods.pop(name, None)
        self.save()

    def get(self, name):
        return self.mods.get(name)

    def _ensure_flags(self, name):
        rec = self.mods.setdefault(name, {"name": name})
        rec.setdefault("favorite", False)
        rec.setdefault("save_critical", False)
        rec.setdefault("state", "active")
        rec.setdefault("backup_paths", [])
        rec.setdefault("paths", [])
        rec.setdefault("group", "")
        return rec

    def set_favorite(self, name, value):
        rec = self._ensure_flags(name)
        rec["favorite"] = bool(value)
        self.save()

    def set_group(self, name, group_name):
        """管理层面的分组（不合并记录，仅标记归属）。group_name 为空表示解除。"""
        rec = self._ensure_flags(name)
        rec["group"] = group_name or ""
        self.save()

    def get_group(self, name):
        rec = self.mods.get(name, {})
        return rec.get("group", "")

    def set_save_critical(self, name, value):
        rec = self._ensure_flags(name)
        rec["save_critical"] = bool(value)
        self.save()

    def set_backed_up(self, name, backed_up, backup_paths=None):
        """记录移入/移出备份状态。backup_paths: [{src, bak}, ...]"""
        rec = self._ensure_flags(name)
        if backed_up:
            rec["state"] = "backed_up"
            seen = {}
            for item in backup_paths or []:
                seen[json.dumps(item, sort_keys=True, ensure_ascii=False)] = item
            rec["backup_paths"] = sorted(seen.values(), key=lambda d: d.get("src", ""))
        else:
            rec["state"] = "active"
            rec["backup_paths"] = []
        self.save()

    def is_backed_up(self, name):
        rec = self.mods.get(name, {})
        return rec.get("state") == "backed_up"

    def merge_pair(self, name_a, name_b, new_name, cfg):
        """把两条记录合并为一条（用于手动合并双端）。返回新记录。"""
        if not new_name or new_name in (name_a, name_b):
            new_name = name_a
        ra = self.mods.get(name_a) or {}
        rb = self.mods.get(name_b) or {}
        paths = sorted(set(list(ra.get("paths", [])) + list(rb.get("paths", []))))
        if not paths:
            return None
        rec = {
            "name": new_name,
            "category": categorize(new_name, paths, cfg),
            "version": ra.get("version") or rb.get("version") or "",
            "install_time": max(ra.get("install_time", ""), rb.get("install_time", "")),
            "source": "install",
            "favorite": bool(ra.get("favorite") or rb.get("favorite")),
            "save_critical": bool(ra.get("save_critical") or rb.get("save_critical")),
            "state": "active",
            "backup_paths": [],
            "paths": paths,
        }
        self.mods.pop(name_a, None)
        self.mods.pop(name_b, None)
        self.mods[new_name] = rec
        self.save()
        return rec

    def sorted_all(self, sort_by="name", descending=False):
        order = {c: i for i, c in enumerate(CATEGORIES)}
        state_order = {"active": 0, "backed_up": 1, "": 0}

        def value(rec):
            if sort_by == "category":
                return order.get(rec.get("category", "其他"), 99)
            if sort_by == "time":
                return rec.get("install_time", "")
            if sort_by == "version":
                return _version_key(rec.get("version", ""))
            if sort_by == "state":
                return state_order.get(rec.get("state", "active"), 0)
            return rec["name"].lower()

        items = sorted(self.mods.items(),
                       key=lambda t: (0 if t[1].get("favorite") else 1, value(t[1])))
        fav = [it for it in items if it[1].get("favorite")]
        rest = [it for it in items if not it[1].get("favorite")]
        if descending:
            fav, rest = fav[::-1], rest[::-1]
        return fav + rest

    def counts(self):
        c = {cat: 0 for cat in CATEGORIES}
        for rec in self.mods.values():
            cat = rec.get("category", "其他")
            c[cat] = c.get(cat, 0) + 1
        c["总数"] = len(self.mods)
        c["已备份"] = sum(1 for r in self.mods.values() if r.get("state") == "backed_up")
        return c


def _version_key(v):
    parts = re.findall(r"\d+", str(v))
    return [int(p) for p in parts] or [0]
