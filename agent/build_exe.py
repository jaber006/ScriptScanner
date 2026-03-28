"""
build_exe.py — Simple PyInstaller build for ScriptScanner Vision Agent (console only).

This builds vision_agent.py directly as a console exe (no GUI launcher).
For the full GUI launcher exe, use build.bat / scriptscanner.spec instead.

Run from the agent/ directory:
    cd agent
    python build_exe.py

Output: agent/dist/ScriptScanner.exe
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)

import PyInstaller.__main__

args = [
    'vision_agent.py',
    '--onefile',
    '--name=ScriptScanner',
    '--icon=NONE',
    '--hidden-import=PIL',
    '--hidden-import=PIL._tkinter_finder',
    '--hidden-import=imagehash',
    '--hidden-import=learning_cache',
    '--hidden-import=pyautogui',
    '--hidden-import=pyperclip',
    '--hidden-import=pygetwindow',
    '--hidden-import=anthropic',
    '--hidden-import=supabase',
    '--hidden-import=flask',
    '--hidden-import=flask_cors',
    '--console',
    '--clean',
    '--noconfirm',
]

# Include .env.local from project root if it exists
env_local = os.path.join(HERE, '..', '.env.local')
if os.path.exists(env_local):
    args.append(f'--add-data={env_local};.')

PyInstaller.__main__.run(args)
