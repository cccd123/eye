@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

REM ============================================================
REM  正式模式启动。模型需要 torch / llama_cpp，这些装在你的 conda 环境里，
REM  不在系统自带的 Python 里。本脚本会自动找到那个环境。
REM  找到后路径记在 python_path.txt，下次直接用；想换环境就删掉这个文件。
REM ============================================================

set "PY="
if exist python_path.txt set /p PY=<python_path.txt
if defined PY if not exist "!PY!" set "PY="
if defined PY goto found

echo 正在查找装了 torch 的 Python 环境，第一次需要几十秒...
for /f "delims=" %%C in ('where conda 2^>nul') do (
  for %%D in ("%%~dpC..") do call :scan "%%~fD"
)
call :scan "%USERPROFILE%\anaconda3"
call :scan "%USERPROFILE%\miniconda3"
call :scan "%USERPROFILE%\.conda"
call :scan "%LOCALAPPDATA%\anaconda3"
call :scan "%LOCALAPPDATA%\miniconda3"
call :scan "C:\ProgramData\anaconda3"
call :scan "C:\ProgramData\miniconda3"
call :scan "C:\anaconda3"
call :scan "C:\miniconda3"
call :scan "D:\anaconda3"
call :scan "D:\miniconda3"
call :scan "D:\Anaconda"
call :scan "D:\ProgramData\anaconda3"
call :scan "E:\anaconda3"
if not defined PY goto notfound
> python_path.txt echo !PY!

:found
for %%P in ("!PY!") do set "ENVDIR=%%~dpP"
set "PATH=!ENVDIR!;!ENVDIR!Library\bin;!ENVDIR!Scripts;%PATH%"
echo 使用的 Python：!PY!

"!PY!" -c "import fastapi, uvicorn, multipart, faster_whisper, imageio_ffmpeg, cryptography, zhconv" >nul 2>nul
if not errorlevel 1 goto run
echo 第一次运行，正在给这个环境安装新增的包（语音识别等）...
"!PY!" -m pip install fastapi "uvicorn[standard]" python-multipart edge-tts faster-whisper imageio-ffmpeg cryptography zhconv -i https://pypi.tuna.tsinghua.edu.cn/simple

:run
set HF_ENDPOINT=https://hf-mirror.com
"!PY!" run.py
pause
exit /b

:notfound
echo.
echo [!] 没有自动找到装了 torch 的 Python 环境。请任选一种：
echo   1. 在 VS Code 里按 Ctrl+Shift+P，输入 Python: Select Interpreter，
echo      看你平时选的那个环境的路径，把那个 python.exe 的完整路径
echo      写进本文件夹的 python_path.txt（只写一行），再双击本文件。
echo   2. 开始菜单打开 Anaconda Prompt，运行  conda env list
echo      把结果截图发给 Claude。
pause
exit /b

REM ── 在一个 conda 根目录里检查 base 和 envs\* ──
:scan
if defined PY exit /b
if not exist "%~1" exit /b
call :try "%~1"
for /d %%E in ("%~1\envs\*") do call :try "%%~fE"
exit /b

:try
if defined PY exit /b
if not exist "%~1\python.exe" exit /b
setlocal
set "PATH=%~1;%~1\Library\bin;%~1\Scripts;%PATH%"
"%~1\python.exe" -c "import torch, transformers, llama_cpp" >nul 2>nul
if errorlevel 1 (endlocal & exit /b)
endlocal & set "PY=%~1\python.exe"
echo   找到：%~1
exit /b
