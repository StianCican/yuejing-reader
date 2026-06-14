"""
build.py — 阅境 一键打包脚本

自动完成：
  1. 下载 Node.js 便携版（若缺失）→ runtime/bin/node.exe
  2. 调用 PyInstaller 打包为单文件 .exe

输出：dist/阅境.exe（约 60-80 MB，含 Python、Node、前端资源，完全自包含）

依赖：pip install pyinstaller requests
"""
from __future__ import annotations
import sys, os, io, zipfile, shutil, stat
from pathlib import Path

# Windows 控制台 UTF-8
if sys.platform == 'win32':
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parent
RUNTIME_BIN = ROOT / 'runtime' / 'bin'
NODE_EXE = RUNTIME_BIN / 'node.exe'

# ── Node.js 便携版下载 ──────────────────────────────────────────
# 使用 Node.js 官方分发（Windows 64-bit 便携版）
# LTS 版本：从 https://nodejs.org/dist/ 获取
NODE_VERSION = '22.20.0'  # LTS
NODE_DOWNLOAD_URLS = [
    f'https://nodejs.org/dist/v{NODE_VERSION}/win-x64/node.exe',
    f'https://npmmirror.com/mirrors/node/v{NODE_VERSION}/win-x64/node.exe',
]


def download_node():
    """如果 runtime/bin/node.exe 不存在，自动下载 Node.js 便携版"""
    if NODE_EXE.exists() and NODE_EXE.stat().st_size > 10_000_000:
        size_mb = NODE_EXE.stat().st_size / 1024 / 1024
        print(f'  ✓ Node.js 便携版已存在 ({size_mb:.1f} MB)')
        return

    RUNTIME_BIN.mkdir(parents=True, exist_ok=True)
    import requests
    sess = requests.Session()
    sess.headers['User-Agent'] = 'Mozilla/5.0'

    for url in NODE_DOWNLOAD_URLS:
        print(f'  ↓ 下载 Node.js 便携版: {url}')
        try:
            resp = sess.get(url, timeout=120, stream=True)
            resp.raise_for_status()
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            with open(NODE_EXE, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1_048_576):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = downloaded / total * 100
                        print(f'\r  {downloaded // 1024 // 1024}/{total // 1024 // 1024} MB ({pct:.0f}%)', end='', flush=True)
            print()
            size_mb = NODE_EXE.stat().st_size / 1024 / 1024
            print(f'  ✓ Node.js 下载完成 ({size_mb:.1f} MB)')
            return
        except Exception as e:
            print(f'  ⚠ 下载失败: {e}')
            # 清理失败的文件
            if NODE_EXE.exists():
                NODE_EXE.unlink()
            continue
    raise RuntimeError('所有下载源均失败，请检查网络连接后重试')


# ── PyInstaller 打包 ─────────────────────────────────────────────

def build():
    print('═' * 50)
    print('  阅境 · 打包构建')
    print('═' * 50)

    # 1. 确保 Node.js 便携版存在
    print('\n[1/3] Node.js 便携版')
    download_node()

    # 2. 校验关键文件
    print('\n[2/3] 校验文件')
    required = {
        ROOT / 'app.py': '主程序',
        ROOT / 'templates' / 'index.html': 'HTML 模板',
        ROOT / 'static' / 'vendor' / 'alpinejs.min.js': 'Alpine.js (离线)',
        ROOT / 'static' / 'vendor' / 'motion.min.js': 'Motion One (离线)',
        ROOT / 'static' / 'vendor' / 'fonts.css': '字体 CSS (离线)',
        ROOT / 'static' / 'vendor' / 'fonts' / 'NotoSerifSC-Regular.woff2': '思源宋体',
        ROOT / 'static' / 'vendor' / 'fonts' / 'NotoSansSC-Regular.woff2': '思源黑体',
        ROOT / 'static' / 'vendor' / 'fonts' / 'MaShanZheng-Regular.woff2': '马山正楷',
        ROOT / 'runtime' / 'js_worker.js': 'Node worker',
        ROOT / 'default_sources.json': '默认书源',
    }
    missing = []
    for path, desc in required.items():
        if path.exists():
            print(f'  ✓ {desc}')
        else:
            print(f'  ✗ {desc} — 缺失: {path}')
            missing.append(desc)
    if missing:
        print(f'\n  ⚠ 缺失 {len(missing)} 项，打包可能不完整。建议先解决后再打包。')
        if input('  继续打包？(y/N): ').strip().lower() != 'y':
            print('  已取消')
            return

    # 3. 图标
    icon_path = ROOT / 'assets' / 'app.ico'
    icon_args = []
    if icon_path.exists():
        icon_args = ['--icon=' + str(icon_path)]
        print(f'\n  ✓ 应用图标: {icon_path}')
    else:
        print('\n  ⚐ 无应用图标（assets/app.ico 不存在），使用默认图标')

    # 4. 运行 PyInstaller
    print('\n[3/3] PyInstaller 打包')
    print('  (可能需要数分钟，请耐心等待...)')

    import PyInstaller.__main__ as pi

    args = [
        str(ROOT / 'app.py'),
        '--name=阅境',
        '--onefile',
        '--noconsole',
        '--clean',
        # 数据目录
        '--add-data=templates;templates',
        '--add-data=static;static',
        '--add-data=runtime/js_worker.js;runtime',
        '--add-data=default_sources.json;.',
        # Node.js 便携版（作为二进制文件打包）
        f'--add-binary={NODE_EXE};runtime/bin',
        # py-mini-racer 的 V8 原生二进制
        '--collect-binaries=py_mini_racer',
        '--collect-data=py_mini_racer',
        # 隐式导入
        '--hidden-import=platformdirs',
        '--hidden-import=requests',
        '--hidden-import=bs4',
        '--hidden-import=lxml',
    ] + icon_args

    pi.run(args)

    # 5. 结果
    dist_exe = ROOT / 'dist' / '阅境.exe'
    if dist_exe.exists():
        size_mb = dist_exe.stat().st_size / 1024 / 1024
        print(f'\n{"═" * 50}')
        print(f'  ✓ 打包完成！')
        print(f'  输出: {dist_exe}')
        print(f'  大小: {size_mb:.1f} MB')
        print(f'{"═" * 50}')
    else:
        print('\n  ⚠ 打包似乎失败，请检查上方错误信息')
        print('  常见问题：')
        print('    1. 杀毒软件拦截 PyInstaller（尝试临时关闭）')
        print('    2. 磁盘空间不足（至少需要 2GB 可用空间）')
        print('    3. pip install pyinstaller 版本过旧')


if __name__ == '__main__':
    build()
