@echo off
chcp 65001 >nul
title 计算语义向量（GPU）
cd /d "%~dp0"
rem anaconda 的 MKL 与 PyTorch 各带一份 OpenMP 运行库，同时加载会报 OMP Error #15
set KMP_DUPLICATE_LIB_OK=TRUE
set PY=
if exist "%USERPROFILE%\anaconda3\python.exe" set PY="%USERPROFILE%\anaconda3\python.exe"
if not defined PY where python >nul 2>nul && set PY=python
if exist "data\work\venv-gpu\Scripts\python.exe" set PY="data\work\venv-gpu\Scripts\python.exe"
if not defined PY (
  echo 没有找到 Python。
  pause
  exit /b
)
echo 使用 Python：%PY%
%PY% -c "import torch; print('PyTorch', torch.__version__, '| CUDA 可用：', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0) if torch.cuda.is_available() else '无 GPU')" || (
  echo.
  echo 导入 PyTorch 失败，请把上面的报错发给 Claude。
  pause
  exit /b
)
%PY% -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" || (
  echo.
  echo 现在的 PyTorch 用不了显卡。请先双击「安装GPU版PyTorch.bat」，装完再运行本文件。
  pause
  exit /b
)
%PY% -c "import transformers" 2>nul || (
  echo 正在安装 transformers ……
  %PY% -m pip install -q "transformers>=4.40"
)
%PY% data\pipeline\embed_gpu.py
echo.
echo 完成后回到 Claude 对话里告诉我即可。
pause
