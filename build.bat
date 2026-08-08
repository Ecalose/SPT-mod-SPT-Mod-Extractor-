@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === 安装依赖 ===
py -m pip install --upgrade pyinstaller tkinterdnd2
echo === 打包 ===
py -m PyInstaller --noconfirm --clean --onefile --windowed ^
  --name "塔科夫离线版mod解压" ^
  --collect-data tkinterdnd2 ^
  main.py
echo === 完成：dist\塔科夫离线版mod解压.exe ===
pause
