"""
路径解析中心 —— 为「开发模式」和「PyInstaller 打包模式」提供统一的路径定位。

设计原则：
- 用户可写数据（书架、健康检测结果、用户书源）一律放到 user_data_dir
- 应用资源（默认书源、模板、静态文件、便携 Node）从 _MEIPASS（打包）或项目根（开发）加载
- 路径首次使用时确保目录存在（mkdir parents=True exist_ok=True）

Windows: %LOCALAPPDATA%\\AIYueJing\\
macOS:   ~/Library/Application Support/AIYueJing/
Linux:   ~/.local/share/AIYueJing/
"""
from __future__ import annotations
import os
import sys
import shutil
from pathlib import Path

from platformdirs import user_data_dir as _user_data_dir

# 应用标识 —— 写英文，避免 Windows GBK 路径问题
APP_NAME = 'AIYueJing'


def user_data_path() -> Path:
    """用户可写数据目录（书架、状态、用户配置）。首次访问自动创建。"""
    p = Path(_user_data_dir(APP_NAME, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


def resource_path(*parts: str) -> Path:
    """
    应用只读资源路径（templates/static/默认书源/便携 Node）。

    优先级：
      1. PyInstaller 解包目录 sys._MEIPASS
      2. 项目根（开发模式）
    """
    base = getattr(sys, '_MEIPASS', None)
    if base:
        return Path(base).joinpath(*parts)
    # 开发模式：项目根 = utils/ 的上级
    return Path(__file__).resolve().parent.parent.joinpath(*parts)


def shelf_file() -> Path:
    return user_data_path() / 'shelf.json'


def source_status_file() -> Path:
    return user_data_path() / 'source_status.json'


def flagged_sources_file() -> Path:
    return user_data_path() / 'flagged_sources.json'


def book_sources_file() -> Path:
    """
    用户当前使用的书源 JSON。
    若用户目录不存在，且应用资源中有 default_sources.json，则首次启动复制过去。
    """
    user_file = user_data_path() / 'book_sources.json'
    if not user_file.exists():
        default = resource_path('default_sources.json')
        if default.exists():
            try:
                shutil.copyfile(default, user_file)
                print(f'📚 已为你初始化默认书源 → {user_file}')
            except Exception as e:
                print(f'⚠ 复制默认书源失败: {e}')
    return user_file
