@echo off
chcp 65001 >nul
cd /d "%~dp0"
python scripts\review_server.py --normalize
pause
