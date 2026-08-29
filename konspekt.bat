@echo off
rem Запуск Konspekt двойным щелчком. Окно консоли прячется, работает только приложение.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
start "" /b uv run pythonw -m app
