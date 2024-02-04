@echo off
cd "%~dp0"

call .venv\Scripts\activate.bat

pip install --upgrade ^
    diffusers ^
    gradio ^
    torch ^
    transformers

pip freeze > requirements-windows.txt
