@echo off
chcp 65001 >nul
cd /d "%~dp0"
set MUSEUM_MOCK=1
python -c "import fastapi, multipart, PIL, uvicorn, cryptography, zhconv" 2>nul || goto install
goto run
:install
echo 第一次运行，正在安装需要的包...
python -m pip install fastapi "uvicorn[standard]" python-multipart pillow edge-tts cryptography zhconv -i https://pypi.tuna.tsinghua.edu.cn/simple
:run
echo [模拟模式] 不加载模型，讲解是假的，只用来看界面、联调眼镜
python run.py
pause
