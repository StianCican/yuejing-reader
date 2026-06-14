"""Write launch.bat to portable directory — binary mode, no CR translation"""
import os

path = r'D:\AI\programs\阅境\launch.bat'

lines = [
    '@echo off',
    'chcp 936 >nul',
    'title YueJing Reader',
    '',
    'set "PY=%~dp0runtime\\python\\python.exe"',
    'set "NODE=%~dp0runtime\\bin\\node.exe"',
    '',
    'if not exist "%PY%" (',
    '    echo [ERROR] Python not found - please re-extract',
    '    pause',
    '    exit /b 1',
    ')',
    '',
    'if not exist "%NODE%" (',
    '    echo [ERROR] Node.js not found - please re-extract',
    '    pause',
    '    exit /b 1',
    ')',
    '',
    'set "PATH=%~dp0runtime\\bin;%PATH%"',
    '',
    'echo ============================================',
    'echo   YueJing v1.0',
    'echo   Starting server...',
    'echo   Open: http://127.0.0.1:5000',
    'echo   Ctrl+C to stop',
    'echo ============================================',
    'echo.',
    '',
    '"%PY%" app.py',
    '',
    'pause',
]

data = '\r\n'.join(lines).encode('ascii')
with open(path, 'wb') as f:
    f.write(data)

# Verify content
with open(path, 'rb') as f:
    content = f.read()

lines_count = content.count(b'\r\n')
double_cr = content.count(b'\r\r')
has_backspace = b'\x08' in content

print(f'Written {len(content)} bytes to launch.bat')
print(f'CRLF lines: {lines_count}')
print(f'Double CR: {double_cr}')
print(f'Backspace (bad): {has_backspace}')
print(f'Starts with: {content[:60]}')
