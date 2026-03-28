# -*- mode: python ; coding: utf-8 -*-
#
# ScriptScanner PyInstaller spec file
# Entry point: launcher.py (GUI) which shells out to vision_agent.py
#
# Build:
#   cd agent
#   python -m PyInstaller scriptscanner.spec --clean -y
#   → dist\ScriptScanner.exe

import os
import sys

HERE = os.path.dirname(os.path.abspath(SPEC))  # noqa: F821 (SPEC is PyInstaller built-in)

# ── Data files ───────────────────────────────────────────────────────────────

datas = [
    # Bundle vision_agent.py and learning_cache.py so the launcher subprocess can find them
    (os.path.join(HERE, 'vision_agent.py'), '.'),
    (os.path.join(HERE, 'learning_cache.py'), '.'),
    (os.path.join(HERE, 'config_manager.py'), '.'),
]

# Include .env.local from project root if it exists (optional fallback)
env_local = os.path.join(HERE, '..', '.env.local')
if os.path.exists(env_local):
    datas.append((env_local, '.'))

# Include config.json with baked-in API keys
config_json = os.path.join(HERE, 'config.json')
if os.path.exists(config_json):
    datas.append((config_json, '.'))

# ── Hidden imports ────────────────────────────────────────────────────────────

hidden_imports = [
    'PIL',
    'PIL._tkinter_finder',
    'PIL.Image',
    'PIL.ImageDraw',
    'imagehash',
    'pyautogui',
    'pyperclip',
    'pygetwindow',
    'anthropic',
    'supabase',
    'flask',
    'flask_cors',
    'pystray',
    'pystray._win32',
    'learning_cache',
    'config_manager',
    'tkinter',
    'tkinter.ttk',
    'tkinter.messagebox',
    'sqlite3',
    'threading',
    'queue',
    'subprocess',
    'json',
    'logging',
]

# ── Analysis ──────────────────────────────────────────────────────────────────

a = Analysis(
    [os.path.join(HERE, 'launcher.py')],
    pathex=[HERE],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'scipy', 'numpy', 'pandas'],
    noarchive=False,
)

# ── PYZ ───────────────────────────────────────────────────────────────────────

pyz = PYZ(a.pure, a.zipped_data, cipher=None)  # noqa: F821

# ── EXE ───────────────────────────────────────────────────────────────────────

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ScriptScanner',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # No console window — GUI only (logs go to file + GUI)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
