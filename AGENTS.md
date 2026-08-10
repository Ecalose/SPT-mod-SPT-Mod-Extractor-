# Repository Guidelines

## Project Overview

A Windows desktop GUI tool for SPT (Single Player Tarkov) mod installation and management. Users drag mod archive packages (zip/rar/7z) into the window; the tool auto-detects the package structure and extracts to the correct game directory (client `BepInEx/plugins` or server `user/mods`). Includes a mod-management page with backup/restore, favorites, save-critical markers, grouping, filtering, and symlink handling.

Built with tkinter + tkinterdnd2. Requires **NanaZip or WinRAR** installed on the host for archive extraction (NanaZip preferred — handles all formats incl. 7z directly). Distributed as a single `.exe` via PyInstaller. MIT-licensed.

## Architecture & Data Flow

```
User drops archive
      │
      ▼
  main.py App._on_drop ──► queue.Queue ──► worker thread (_worker, daemon)
      │                                        │
      ▼                                        ▼
  looks_like_archive filter              _process_one(archive)
                                           │
                                           ├─ _resolve_backend  (NanaZip preferred, WinRAR fallback; cached)
                                           ├─ md.analyze_archive → Analysis dataclass
                                           │     ├─ .zip  → zipfile (native, in mod_detector)
                                           │     ├─ .rar  → nanazip.list_with_encrypt / winrar.list_archive
                                           │     └─ .7z  → nanazip.list_with_encrypt
                                           │                (no NanaZip → KIND_NEEDS_STAGING: extract to temp, re-analyze)
                                           ├─ KIND_NEEDS_STAGING → _run_extract to temp → walk_dir_entries → analyze_entries
                                           ├─ reject check (KINDS_TO_REJECT ∪ KIND_MIXED runtime abort)
                                           ├─ COMBO root-existence check; encrypted confirm
                                           ├─ needs_confirm → ask_choice → _apply_choice
                                           ├─ md.resolve_targets (placeholder → real paths)
                                           ├─ symlink check / convert-to-regular / keep-links
                                           ├─ overwrite confirm → _delete_target
                                           ├─ _run_extract (nanazip.extract_to | winrar.extract_to)
                                           ├─ _cleanup_storage (remove old mods_storage copies)
                                           └─ _record_install → moddb (upsert + save / batch)
```

**Core modules:**

| Module | Responsibility |
|---|---|
| `main.py` | GUI (tkinter), drag-drop, queue/worker threading, orchestration of detect→confirm→extract→record flow, settings page, mod-management page, entry point, `--selftest` |
| `mod_detector.py` | Archive structure classification (`KIND_*`), path-traversal protection, symlink detection/removal, `_maybe_unwrap` outer-wrapper stripping, installed-mod scanning, client↔server pairing, `resolve_targets` placeholder→real resolution |
| `archive_backend.py` | Shared archive-backend helpers: console-window suppression, output decode, `run()`, `looks_like_archive`, `is_zip` — imported by winrar/nanazip/main |
| `winrar.py` | WinRAR subprocess wrapper: path detection (registry + known paths), archive listing via UnRAR.exe, extraction, exit-code→message map |
| `nanazip.py` | NanaZip / 7-Zip CLI wrapper: MSIX/portable/PATH detection, `list_with_encrypt()` (single CLI call → entries + encrypted flag), extraction, exit-code mapping. Preferred backend when available. |
| `moddb.py` | JSON-persisted mod database: `ModDB` class — CRUD, categorization, favorites, save-critical flags, backup state, grouping, sorting, batch-save (`begin_batch`/`flush`), version extraction |

## Key Directories

Flat structure — no subdirectories for source. All Python files live at project root.

| File | Purpose |
|---|---|
| `main.py` | Entry point (~1613 lines). `App` class (line 140) builds 3 tabs: Mod解压 (extract), Mod管理 (management), 设置 (settings). `main()`+`--selftest` at line 1602. |
| `mod_detector.py` | Detection engine (~740 lines). `Analysis` dataclass (64), `KIND_*` constants (16-30), `analyze_archive()` (339), `analyze_entries()` (413), `_classify()` (167), `_maybe_unwrap()` (142), `resolve_targets()` (534), `scan_installed_mods()` (656), `pair_mods()` (705) |
| `archive_backend.py` | Shared backend helpers (~49 lines). `no_window()` (11), `decode()` (16), `run()` (28), `is_zip()` (42), `looks_like_archive()` (46) |
| `winrar.py` | WinRAR wrapper (~113 lines). `find_winrar()` (51), `find_unrar()` (62), `list_archive()` (74), `extract_to()` (96), `EXIT_MSG` (16, codes 0–12) |
| `nanazip.py` | NanaZip/7-Zip wrapper (~222 lines). `find_nanazip()` (92), `_find_msix_console()` (53), `_probe()` (83), `list_with_encrypt()` (166), `list_archive()` (191), `extract_to()` (206), `EXIT_MSG` (40) |
| `moddb.py` | Database (~258 lines). `ModDB` class (70), `CATEGORIES` (11), `SORT_KEYS` (13) |
| `test_*.py` | Test files (4 files, sandboxed) |
| `config.example.json` | Config template |
| `build.bat` | PyInstaller build script |

## Development Commands

### Install dependencies
```bat
pip install pyinstaller tkinterdnd2
```

### Build exe
```bat
build.bat
```
Runs `py -m PyInstaller --noconfirm --clean --onefile --windowed --name "塔科夫离线版mod解压" --collect-data tkinterdnd2 main.py`. Produces `dist\塔科夫离线版mod解压.exe`.

### Run from source
```bat
python main.py
```

### Run selftest (CLI diagnostic)
```bat
python main.py --selftest
```
Writes `selftest_result.txt` with NanaZip/WinRAR detection, config, mod-scan results, and pairing info — then exits. Does not launch GUI.

### Tests
```bat
py test_detector.py    :: Archive structure detection (20+ cases)
py test_sandbox.py     :: WinRAR/NanaZip extraction + symlink conversion (sandboxed)
py test_flow.py        :: Full installation flow integration test
py test_manager.py     :: Mod management: backup/restore/grouping/marking/sorting
```

Exit code 0 = all pass, 1 = failures. Tests print `[PASS]`/`[FAIL]` lines and a final `RESULT:` summary.

## Code Conventions & Common Patterns

### File encoding
All files start with `# -*- coding: utf-8 -*-`. UI text and comments are in **Chinese** (Simplified). No i18n framework — strings are hardcoded.

### Naming
- **Classes**: PascalCase (`App`, `ChoiceDialog`, `ModDB`, `Analysis`, `ModEntry`)
- **Functions/methods**: snake_case with leading underscore for private (`_classify`, `_process_one`, `_run`)
- **Constants**: UPPER_SNAKE (`KIND_SERVER`, `STRUCTURAL_ROOTS`, `EXIT_MSG`, `KNOWN_PATHS`, `CATEGORIES`)
- **Module alias**: `import mod_detector as md`; backends imported by name (`from winrar import ...`, `from nanazip import ...`)

### Error handling
- `try/except` with `traceback.print_exc()` / `traceback.format_exc()` for debugging
- User-facing errors via `self._show_error(msg)` → `self.log` + `_set_status("失败")` (marshalled to UI thread via `self._ui_call`)
- `RuntimeError` raised by `list_archive()` / `analyze_archive()` for backend failures; caught in `_process_one` (line 536)
- `OSError`/`ValueError` caught silently for config/DB load failures (graceful degradation to defaults — see `load_config`)
- No custom exception classes; no logging framework (uses `print` + `self.log()`)

### Threading model
- **Worker thread**: `threading.Thread(target=self._worker, daemon=True)` (line 473) processes archive queue via `self.queue` (`queue.Queue`, line 146)
- **UI thread marshalling**: `self._ui_call(fn)` (412) puts closures into `self.ui_jobs` queue (145); `poll_ui()` (415), scheduled via `root.after(100, …)` (425), drains and executes them on the main thread
- **Dialog blocking**: `ask_choice()` (427) / `ask_name()` (441) use `threading.Event` to block the worker thread until the user responds
- **Async management ops**: `_run_async(fn)` (1199) wraps backup/restore/toggle/group in a `daemon=True` worker thread with an `op_busy` guard to prevent concurrent management operations

### State management
- **Config**: `config.json` at `app_dir()` (line 31). Loaded via `load_config()` (45), saved via `save_config()` (58). `default_config()` (40) returns safe defaults. `app_dir()` checks `sys.frozen` for PyInstaller paths.
- **Mod database**: `mods_db.json` — `ModDB` (moddb.py:70) loads on init, saves after every mutation (`self.save()` in setters); bulk mutations wrap in `begin_batch()`/`flush()` to avoid redundant writes (see favorites/save-critical/backup/restore/group in main.py)
- **Backend cache**: `self._backend_cache` (149) caches `(backend, path)` per session; invalidated by `_clear_backend_cache()` (514) on settings save (1535). Detected path is written back to `cfg` and persisted.
- **App state**: instance attributes on `App` — `self.cfg`, `self.moddb`, `self.queue`, `self.ui_jobs`, `self.worker_active`, `self.scan_busy`, `self.op_busy`, `self._tree_mods`, `self._tree_groups`, `self._backend_cache`

### Subprocess patterns
- Backend CLI invoked via `archive_backend.run(exe, args)` → `subprocess.run(cmd, capture_output=True, timeout=3600, creationflags=no_window())`, list-form (no `shell=True` anywhere)
- `CREATE_NO_WINDOW` (via `no_window()`, non-Windows→0) suppresses console popups
- Output decoded with fallback chain `utf-8 → gbk → latin-1 → replace` (`decode()`)
- `mklink /J` junction creation in tests via `subprocess.run(["cmd", "/c", "mklink", "/J", dst, src], check=True, capture_output=True)`

### Path handling
- Mix of `os.path` and string operations. No `pathlib`.
- Forward-slash normalization in `md._norm_path()` (82) for archive entries
- `md._join_root()` (530) emits placeholder strings (`<客户端根>`, `<服务端根>`) resolved later by `resolve_targets()` (534) using `cfg["client_root"]`/`cfg["server_root"]`
- `md._maybe_unwrap()` (142) strips outer wrapper dirs (e.g. `KeyTips/BepInEx/…` → `BepInEx/…`) when the inner layout is structural

### Data persistence
- JSON files written with `ensure_ascii=False, indent=2`
- `ModDB` schema (moddb.py:71): `{name: {name, category, version, install_time, source, favorite, save_critical, state(active|backed_up), backup_paths, paths: []}}`
- `CATEGORIES = ("双端", "客户端", "服务端", "汉化", "其他")` (dual/client/server/translation/other)
- `SORT_KEYS = {"name", "category", "time", "version", "state"}`

### GUI patterns
- `ttk.Notebook` for 3 tabs; `ttk.Treeview` for mod list with tag-based coloring
- Widget construction via `_build_*_tab()` methods; overview refresh via `_refresh_overview()` (952)
- Config loaded to `tk.StringVar` vars in `self.cfg_vars` dict
- Fonts: `Microsoft YaHei` for labels, `Consolas` for log/info text
- Category colors: `{"双端": "#b30000", "客户端": "#0066cc", "服务端": "#0a7a3d", "汉化": "#8a2be2", "其他": "#555555"}`
- Filter tags on management page: 全部 / 已启用 / 已备份 / 不可操作 (`_filter_records`, 932)

### Detection constants (`mod_detector.py`, lines 16–30)
The `KIND_*` constants classify archive structures:
- `KIND_SERVER` — `user/mods/...` layout
- `KIND_CLIENT` — `BepInEx/plugins/...` layout
- `KIND_LEGACY` — `mods/<name>/...` (old SPT layout, → server root)
- `KIND_COMBO` — contains both BepInEx + server paths
- `KIND_SERVER_ROOT` — package is entire SPT root (strip `SPT/` prefix)
- `KIND_SERVER_NAMED` — single top folder with config.json → `user/mods/<name>`
- `KIND_CLIENT_FOLDER` — single top folder containing only a dll (needs confirmation)
- `KIND_SERVER_LOOSE` / `KIND_CLIENT_LOOSE` — top-level loose dll+config / loose dll
- `KIND_NEEDS_STAGING` — 7z (or unreadable rar) when no NanaZip backend → extract to temp first, re-analyze. With NanaZip, 7z is listed directly via `l -slt`.
- `KIND_MIXED` — multiple unrelated top dirs; **aborted at runtime** in `_process_one` (590) with an error (not in `KINDS_TO_REJECT`, but effectively rejected)
- `KIND_TRAVERSAL` / `KIND_EMPTY` / `KIND_NOT_ARCHIVE` / `KIND_UNKNOWN` — rejection types

`KINDS_TO_REJECT` (main.py:28) = `{KIND_TRAVERSAL, KIND_EMPTY, KIND_NOT_ARCHIVE, KIND_UNKNOWN}` — these abort before extraction (line 566). `KIND_MIXED` is aborted separately at line 590. `KIND_NAMES` (mod_detector.py:32) maps each kind to a Chinese display label.

Other detection constants: `STRUCTURAL_ROOTS = {"user","bepinex","mods","spt","game"}` (9), `STOP_TOKENS` (12, pairing ignore-words).

## Important Files

| File / Symbol | Role |
|---|---|
| `main.py` | Entry point (`if __name__ == "__main__": main()`, 1602). `App` class (140). `--selftest` → `run_selftest()` (1603). |
| `main.py:KINDS_TO_REJECT` (28) | Kinds that abort installation before extraction |
| `main.py:default_config` (40) / `load_config` (45) / `save_config` (58) | Config persistence |
| `main.py:app_dir` (31) / `CONFIG_PATH` (37) | Frozen-vs-source dir resolution; `config.json` path |
| `main.py:App._on_drop` (456) / `_worker` (475) / `_process_one` (517) | Drop → queue → worker → per-archive pipeline |
| `main.py:App._resolve_backend` (488) / `_clear_backend_cache` (514) | Cached backend resolution (NanaZip preferred, WinRAR fallback); invalidated on settings save |
| `main.py:App._run_extract` (820) | Dispatches `nanazip.extract_to` vs `winrar.extract_to` |
| `main.py:App._cleanup_storage` (681) / `_record_install` (646) | Post-install storage cleanup + DB record |
| `main.py:App._run_async` (1199) | Management-op worker-thread wrapper (`op_busy` guard) |
| `main.py:App.poll_ui` (415) / `_ui_call` (412) / `ask_choice` (427) / `ask_name` (441) | Threading + dialog primitives |
| `mod_detector.py:analyze_archive()` (339) | Main entry for archive analysis (backend-aware) |
| `mod_detector.py:analyze_entries()` (413) | Classification + target computation (owns `_maybe_unwrap` step) |
| `mod_detector.py:_classify()` (167) | Core classification logic (expects already-unwrapped entries) |
| `mod_detector.py:_maybe_unwrap()` (142) | Strip outer wrapper directories |
| `mod_detector.py:resolve_targets()` (534) | Placeholder→real path resolution |
| `mod_detector.py:scan_installed_mods()` (656) / `pair_mods()` (705) | Installed-mod scan + client↔server pairing |
| `archive_backend.py:run()` (28) | Shared subprocess helper (used by winrar/nanazip) |
| `archive_backend.py:looks_like_archive()` (46) / `is_zip()` (42) | Format checks (zip/rar/7z via `endswith`) |
| `winrar.py:list_archive()` (74) / `extract_to()` (96) | Rar listing via UnRAR.exe `lb`; extraction via WinRAR `x` |
| `winrar.py:find_winrar()` (51) / `find_unrar()` (62) | Registry + known-paths detection |
| `nanazip.py:list_with_encrypt()` (166) | Single `l -slt` CLI call → `(entries, encrypted)` for rar/7z/zip |
| `nanazip.py:find_nanazip()` (92) | MSIX → known paths → PATH detection with `_probe()` validation |
| `moddb.py:ModDB` (70) | Database class with `begin_batch`/`flush` batch-save |
| `config.example.json` | Config schema template |

### Config schema (`config.json`)
```json
{
  "client_root": "",       // Game root (contains EscapeFromTarkov.exe / BepInEx/)
  "server_root": "",       // SPT server root (contains user/mods)
  "winrar_path": "",       // Path to WinRAR.exe (fallback backend)
  "nanazip_path": "",      // Path to NanaZip/7z CLI (preferred when set / detected)
  "mod_manager_enabled": true,  // Enable backup/restore features
  "backup_dir": ""         // Directory for disabled (backed-up) mods
}
```
`config.json` and `mods_db.json` are gitignored. First run auto-detects: server root from client root, then NanaZip (MSIX/known paths/PATH) then WinRAR (registry + known paths). Backend choice is cached per session (`App._backend_cache`); detected path is written back to config. Leaving `nanazip_path` empty falls back to WinRAR.

## Runtime/Tooling Preferences

- **Runtime**: Python 3.12+ (Windows only — uses `winreg`, `subprocess.CREATE_NO_WINDOW`, `os.path.isjunction`)
- **External dependency**: NanaZip **or** WinRAR must be installed (NanaZip preferred; one suffices). NanaZip detected via MSIX `Get-AppxPackage` / portable names (`NanaZipC.exe`, `NanaZip.Universal.Console.exe`, `7z.exe`, `7za.exe`) / PATH, with a probe call validating the CLI. WinRAR detected via registry (`SOFTWARE\WinRAR`) + known install paths.
- **GUI framework**: tkinter (stdlib) + tkinterdnd2 (drag-and-drop, pip)
- **Package manager**: pip (no `requirements.txt` — deps listed in README and `build.bat`)
- **Build tool**: PyInstaller via `build.bat`
- **No linter/formatter config** — no `.flake8`, `.pylintrc`, `ruff.toml`, `pyproject.toml`, or `.editorconfig`
- **No CI/CD** — no `.github/workflows/`
- **No type checking** — no `mypy.ini` or pyright config
- **No `requirements.txt`/`setup.py`/`pyproject.toml`** — dependencies are `pyinstaller` and `tkinterdnd2`

## Testing & QA

### Framework
No test framework (no `unittest`, no `pytest`). Tests are standalone scripts with plain assert-style checks via a custom `check(name, cond, extra="")` function that appends to `PASSED`/`FAILED` lists (or `RESULTS` tuples in `test_sandbox.py`) and prints `[PASS]`/`[FAIL]` lines + a final `RESULT:` summary. Exit 0 = all pass, 1 = failures.

### Test isolation
All tests use temp sandboxes under `%TEMP%\opencode\` — they never touch real game directories:
- `test_detector.py`: `tempfile.mkdtemp(prefix="mod_detect_test_")` — pure zipfile-construction, no extraction
- `test_flow.py`: `SANDBOX = …/opencode/flow_sandbox`
- `test_manager.py`: `SANDBOX = …/opencode/mgr_sandbox`
- `test_sandbox.py`: `SANDBOX = …/opencode/spt_sandbox`

### Test harness pattern
`test_flow.py` defines `AutoApp(App)` — a headless subclass that overrides GUI/dialog methods to avoid tkinter: `ask_choice`, `ask_name`, `_show_error`, `_notice`, `_show_info`, `log`, `_set_status`, `_refresh_overview`, `_ui_call` (runs inline), `_delete_target` (real fs delete). Its `__init__` bypasses `App.__init__` and sets `cfg`/`moddb`/`logs` directly. `test_manager.py` imports `AutoApp` from `test_flow` and adds `app_with(cfg)`.

### What each test covers
| File | Coverage |
|---|---|
| `test_detector.py` | 20+ cases: every `KIND_*` classification (server/client/combo/server-root/server-named/loose/legacy/mixed/traversal/empty/not-archive), wrapped directories, multi-mod packages, readme-tolerant classification |
| `test_flow.py` | Full `_process_one` pipeline: server/client/combo installation, 7z staging, symlink conversion, overwrite, storage cleanup (requires a backend) |
| `test_manager.py` | Backup/restore, symlink→regular conversion on backup, save-critical blocking, group/ungroup/merge-pairs, sorting, favorites, process detection (`_running_games`), batched DB ops |
| `test_sandbox.py` | Backend integration: real extraction via WinRAR **and** NanaZip, junction creation/conversion (`mklink /J`), archive listing, 7z creation (`py7zr` or CLI fallback) |

### Coverage expectations
No formal coverage tooling. Tests are run manually before releases. `test_sandbox.py` and `test_flow.py` require NanaZip or WinRAR installed on the host.
