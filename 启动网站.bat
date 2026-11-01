@echo off
chcp 65001 >nul
title AI Research Graph
cd /d "%~dp0"
set PY=
where python >nul 2>nul && set PY=python
if not defined PY where py >nul 2>nul && set PY=py
if not defined PY (
  echo 没有找到 Python。请先安装：https://www.python.org/downloads/
  echo 安装时勾选 "Add python.exe to PATH"，装好后再双击本文件。
  pause
  exit /b
)
echo 正在启动 AI Research Graph，浏览器会自动打开……
%PY% serve_local.py --open "#/ask?connect=1"
pause
