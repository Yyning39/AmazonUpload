@echo off
setlocal
cd /d "%~dp0"

echo 关闭正在运行的旧版本...
taskkill /F /IM AosomImageAplus.exe >nul 2>&1

echo 安装依赖...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo 依赖安装失败，旧版本未删除。
  pause
  exit /b 1
)

echo 删除旧版本...
if exist "dist\AosomImageAplus.exe" del /f /q "dist\AosomImageAplus.exe"
if exist "dist\AosomImageAplus\" (
  attrib -r "dist\AosomImageAplus\*" /s /d >nul 2>&1
  rmdir /s /q "dist\AosomImageAplus"
)
if exist "dist\AosomImageAplus\" (
  echo 旧版本删不掉。请先关闭正在使用 dist\AosomImageAplus 的程序和编辑器标签，再重新运行本脚本。
  pause
  exit /b 1
)

echo 打包新版本...
python -m PyInstaller --noconfirm AosomImageAplus.spec
if errorlevel 1 (
  echo 打包失败。
  pause
  exit /b 1
)

echo 新版本已生成：dist\AosomImageAplus.exe
echo 把这个 exe 拷到别的文件夹即可。同目录放上 config\settings.local.json，或直接放 settings.local.json。
pause
exit /b 0
