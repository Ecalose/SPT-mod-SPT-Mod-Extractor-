# 塔科夫离线版 Mod 解压与管理工具


~~~~


为 SPT（Single Player Tarkov，塔科夫离线版）设计的 Mod 安装与管理系统。把 Mod 压缩包拖进窗口，自动识别结构并解压到正确位置；内置 Mod 管理页，支持备份/禁用、收藏、存档类标记与分组。

**下载即用的 exe 请到 [Releases](https://github.com/Nekofur/SPT-mod-SPT-Mod-Extractor-/releases) 页面获取。**

## 功能

### Mod 解压
- 拖入/选择 **zip / rar / 7z** 压缩包，自动分析包内结构后安装到正确位置（调用本机 WinRAR）
- 自动识别多种包结构：
  - SPT 服务端 mod（`user/mods/...`、单文件夹、散装 `dll + config.json`）
  - 客户端插件（`BepInEx/plugins/...`、散装 dll）
  - **客户端+服务端组合包**（一个包内含 `BepInEx` 与 `SPT/user/mods`，自动分流安装）
  - 服务端根目录包（顶层 `SPT/`，自动剥离前缀）
  - 外层包裹目录（如 `ModName/BepInEx/...`，自动剥离）
- 7z 无法预览时：**先解压到临时目录检查**，确认安全才安装
- 安全防护：路径穿越（`../`）包拒绝、空包/异常包提醒、加密包提示
- **软链接处理**（nekobox 等管理器创建的链接）：更新时询问转为常规 Mod / 保留链接
- 更新安装后自动**移除 `mods_storage` 中的旧副本**，保留正常安装版本

### Mod 管理（Mod 管理页）
- 首次打开自动扫描，建立 mod 记录；每次安装自动更新（名称/分类/版本/安装时间/路径）
- 分类：双端 / 客户端 / 服务端 / 汉化 / 其他，可手动将客户端+服务端**归为一组**（记录各自保留）
- 排序（名称/分类/时间/版本/状态 + 升降序）与横向过滤标签：**全部 / 已启用 / 已备份 / 不可操作**
- **收藏置顶**、**存档类标记**（🔒 标记的 Mod 无法移入备份，避免破坏存档）
- **移入备份 / 还原**：把 Mod 移到备份文件夹使其不被加载（操作前检查游戏与服务端是否运行；软链接自动先转常规；失败自动回滚）；分组可整组操作
- 双击任意条目可在资源管理器中打开位置

## 环境要求
- Windows（SPT 客户端 + 服务端）
- 本机安装 [WinRAR](https://www.win-rar.com/)（用于解压 zip/rar/7z）
- Python 3.12+（仅编译源码时需要）

## 使用
1. 从 Releases 下载 exe，放到任意有写权限的文件夹（首次运行会生成 `config.json`）
2. 首次运行：选择游戏根目录（含 `EscapeFromTarkov.exe` 的文件夹），程序自动检测服务端目录（`user/mods` 所在）与 WinRAR，可在「设置」页修改
3. 「Mod 解压」页：把 mod 压缩包拖入窗口即可
4. 「Mod 管理」页：查看/操作全部 mod

## 从源码构建
```bat
pip install pyinstaller tkinterdnd2
build.bat
```
产物位于 `dist\塔科夫离线版mod解压.exe`。

## 目录结构
```
SPT-Mod-Extractor/
├── main.py            # GUI 主程序（tkinter + tkinterdnd2 拖拽）
├── mod_detector.py    # 压缩包结构识别 / 软链接 / 扫描
├── winrar.py          # WinRAR 调用封装
├── moddb.py           # Mod 记录数据库（收藏/标记/分组/备份状态）
├── config.example.json
├── build.bat
└── test_*.py          # 测试（沙箱化，不触碰真实游戏目录）
```

## 测试
```bat
py test_detector.py    # 识别逻辑
py test_sandbox.py     # WinRAR 解压 + 软链接转换（沙箱）
py test_flow.py        # 安装全流程
py test_manager.py     # 管理功能（备份/分组/标记等）
```

## 免责声明
- 本工具仅供个人学习使用，请支持正版《Escape from Tarkov》。
- 使用前建议备份游戏与存档；移入备份/删除操作不可完全撤销，程序会尽量提示与回滚。

## 许可证
[MIT](LICENSE)
