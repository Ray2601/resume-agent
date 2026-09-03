@echo off
chcp 65001 >nul
echo ========================================
echo   简历优化器 - 停止服务
echo ========================================
taskkill /F /IM natapp.exe >nul 2>&1 && echo [✓] natapp 已停止 || echo [-] natapp 未运行
taskkill /F /IM python.exe >nul 2>&1 && echo [✓] Gradio 已停止 || echo [-] Gradio 未运行
echo ========================================
pause
