@echo off
chcp 65001 >nul
title 安装 GPU 版 PyTorch
cd /d "%~dp0"
echo ===== 1/4 检查显卡 =====
where nvidia-smi >nul 2>nul || (
  echo 没有找到 nvidia-smi：这台电脑可能没有 NVIDIA 显卡，或者没有安装显卡驱动。
  echo 请把这个窗口的内容发给 Claude。
  pause
  exit /b
)
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
echo.

set BASEPY=
if exist "%USERPROFILE%\anaconda3\python.exe" set BASEPY="%USERPROFILE%\anaconda3\python.exe"
if not defined BASEPY where python >nul 2>nul && set BASEPY=python
if not defined BASEPY (
  echo 没有找到 Python。
  pause
  exit /b
)
set VPY="data\work\venv-gpu\Scripts\python.exe"
echo ===== 2/4 创建独立环境（不会改动 anaconda） =====
if not exist data\work\venv-gpu\Scripts\python.exe (
  %BASEPY% -m venv data\work\venv-gpu || (
    echo 创建环境失败。
    pause
    exit /b
  )
)
%VPY% -m pip install -q --upgrade pip

echo.
echo ===== 3/4 安装 GPU 版 PyTorch（约 2-3 GB，请耐心等待） =====
set OK=
rem 已经装好且显卡可用时跳过，不重复下载
%VPY% -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)" 2>nul && set OK=installed && echo 已安装 GPU 版 PyTorch，跳过。
call :try cu130
call :try cu126
call :try cu124
if not defined OK (
  echo.
  echo 三个 CUDA 版本都没有让显卡可用。可能是显卡驱动太旧，请把上面 nvidia-smi 的输出和这里的信息发给 Claude。
  pause
  exit /b
)

echo.
echo ===== 4/4 安装其他依赖 =====
%VPY% -m pip install -q transformers numpy scikit-learn
echo.
%VPY% -c "import torch; print('PyTorch', torch.__version__, '| CUDA 可用：', torch.cuda.is_available(), '|', torch.cuda.get_device_name(0))"
echo.
echo 安装完成。现在可以双击「计算语义向量(GPU).bat」开始计算。
pause
exit /b

:try
if defined OK exit /b
echo.
echo --- 尝试 CUDA 版本 %1 ---
%VPY% -m pip install --upgrade --force-reinstall torch --index-url https://download.pytorch.org/whl/%1
if errorlevel 1 exit /b
%VPY% -c "import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)"
if errorlevel 1 (
  echo %1 安装成功，但显卡不可用（可能驱动版本不够新），换下一个版本……
  exit /b
)
set OK=%1
echo %1 可用。
exit /b
