@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 安装依赖并打包（通过 uv） ===
REM Clean PyInstaller bincache and old artifacts to prevent onefile _tcl_data loss
rmdir /s /q "%LOCALAPPDATA%\pyinstaller\bincache01py31264bit" 2>nul
rmdir /s /q build 2>nul
rmdir /s /q dist 2>nul
uv run --python 3.12 --with pyinstaller --with tkinterdnd2 ^
  pyinstaller --noconfirm --onefile --windowed ^
  --name "塔科夫离线版mod解压" ^
  --collect-data tkinterdnd2 ^
  main.py
echo === 完成：dist\塔科夫离线版mod解压.exe ===
pause
