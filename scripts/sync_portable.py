"""Sync source files to portable dir — preserves runtime/python/ and runtime/bin/node.exe"""
import shutil, os
from pathlib import Path

SRC = Path(r'D:\AI\novel-reader')
DST = Path(r'D:\AI\programs\阅境')

# Source dirs to sync (overwrite target completely)
SYNC_DIRS = ['sources', 'rules', 'utils', 'templates', 'static']

# Source files to sync (overwrite target)
SYNC_FILES = ['app.py', 'default_sources.json']

# Subdirs/files inside runtime/ to sync (only these, preserve everything else)
RUNTIME_SYNC = ['__init__.py', 'js_engine.py', 'js_worker.js']

print('=== Syncing to portable dir ===')

# Sync directories
for d in SYNC_DIRS:
    s = SRC / d
    t = DST / d
    if t.exists():
        shutil.rmtree(str(t))
    if s.exists():
        shutil.copytree(str(s), str(t), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        print(f'  {d}/')

# Sync root files
for f in SYNC_FILES:
    s = SRC / f
    if s.exists():
        shutil.copy2(str(s), str(DST / f))
        print(f'  {f}')

# Sync runtime/ selectively (preserve python/ and bin/node.exe)
rt_src = SRC / 'runtime'
rt_dst = DST / 'runtime'
rt_dst.mkdir(parents=True, exist_ok=True)
for f in RUNTIME_SYNC:
    s = rt_src / f
    if s.exists():
        shutil.copy2(str(s), str(rt_dst / f))
        print(f'  runtime/{f}')

# Ensure node.exe exists
node_src = SRC / 'runtime' / 'bin' / 'node.exe'
node_dst = rt_dst / 'bin' / 'node.exe'
if not node_dst.exists() and node_src.exists():
    node_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(node_src), str(node_dst))
    print(f'  runtime/bin/node.exe (copied)')
elif node_dst.exists():
    print(f'  runtime/bin/node.exe (preserved)')

print(f'\nDone. Portable dir: {DST}')
