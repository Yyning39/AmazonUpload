@echo off
setlocal
cd /d "%~dp0"
chcp 65001 >nul

echo 关闭正在运行的旧版本...
taskkill /F /IM AosomImageAplus.exe >nul 2>&1

echo 安装依赖...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  echo 依赖安装失败，旧版本未删除。
  pause
  exit /b 1
)

echo 删除旧版本...
if exist "dist\AosomImageAplus" (
  attrib -r "dist\AosomImageAplus\*" /s /d >nul 2>&1
  rmdir /s /q "dist\AosomImageAplus"
)
if exist "dist\AosomImageAplus" (
  echo 旧版本删不掉。请先关闭正在使用 dist\AosomImageAplus 的程序和编辑器标签，再重新运行本脚本。
  pause
  exit /b 1
)

echo 打包新版本...
python -m PyInstaller --noconfirm --console --name AosomImageAplus --collect-all tkinterdnd2 --add-data "config;config" main.py
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)

echo 新版本已生成：dist\AosomImageAplus\AosomImageAplus.exe
pause
exit /b 0
