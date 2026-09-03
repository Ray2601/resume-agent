@echo off
chcp 65001 >nul
cd /d "E:\vscode\coding\cv_agent"

echo ========================================
echo   简历优化器 - 后台服务启动
echo ========================================

:: 杀掉旧进程
taskkill /F /IM natapp.exe >nul 2>&1
taskkill /F /IM python.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: 启动 Gradio (使用 pythonw 无窗口运行)
echo [1/2] 启动 Gradio 服务...
start "" pythonw.exe scripts\gradio_app.py
timeout /t 5 /nobreak >nul

:: 启动 natapp 隧道
echo [2/2] 启动 natapp 隧道...
start "" /B natapp.exe -log=stdout -config=config.ini
timeout /t 3 /nobreak >nul

echo.
echo ========================================
echo   服务已启动!
echo   公网访问: http://ma5da774.natappfree.cc
echo   本地访问: http://127.0.0.1:7860
echo ========================================
echo.
echo 关闭此窗口不影响后台服务运行。
pause
