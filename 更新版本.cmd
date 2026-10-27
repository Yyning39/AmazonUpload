@echo off
setlocal
cd /d "%~dp0"

echo �ر��������еľɰ汾...
taskkill /F /IM AosomImageAplus.exe >nul 2>&1

echo ��װ����...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo ������װʧ�ܣ��ɰ汾δɾ����
  pause
  exit /b 1
)

echo ɾ���ɰ汾...
if exist "dist\AosomImageAplus.exe" del /f /q "dist\AosomImageAplus.exe"
if exist "dist\AosomImageAplus\" (
  attrib -r "dist\AosomImageAplus\*" /s /d >nul 2>&1
  rmdir /s /q "dist\AosomImageAplus"
)
if exist "dist\AosomImageAplus\" (
  echo �ɰ汾ɾ���������ȹر�����ʹ�� dist\AosomImageAplus �ĳ���ͱ༭����ǩ�����������б��ű���
  pause
  exit /b 1
)

echo ����°汾...
python -m PyInstaller --noconfirm AosomImageAplus.spec
if errorlevel 1 (
  echo ���ʧ�ܡ�
  pause
  exit /b 1
)

echo �°汾�����ɣ�dist\AosomImageAplus.exe
echo ����� exe ��������ļ��м��ɡ�ͬĿ¼���� config\settings.local.json����ֱ�ӷ� settings.local.json��
pause
exit /b 0
