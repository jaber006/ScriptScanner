"""
ScriptScanner Config Manager
============================
Priority: config.json (next to exe) → .env.local → environment variables

Usage:
    cfg = ConfigManager()
    cfg.load()
    api_key = cfg.get('ANTHROPIC_API_KEY')
    cfg.set('PHARMACIST_INITIALS', 'MJ')
    cfg.save()
    if cfg.is_configured():
        ...
"""

import os
import sys
import json
import logging

log = logging.getLogger('config_manager')

# Keys we care about
REQUIRED_KEYS = [
    'ANTHROPIC_API_KEY',
    'SUPABASE_URL',
    'SUPABASE_KEY',
]

OPTIONAL_KEYS = [
    'PHARMACIST_INITIALS',
    'DRY_RUN',
    'POLL_INTERVAL',
]

ALL_KEYS = REQUIRED_KEYS + OPTIONAL_KEYS

# Map config.json friendly names → standard env var names
_KEY_MAP = {
    'anthropic_api_key': 'ANTHROPIC_API_KEY',
    'supabase_url': 'SUPABASE_URL',
    'supabase_key': 'SUPABASE_KEY',
    'pharmacist_initials': 'PHARMACIST_INITIALS',
    'pharmacy_id': 'PHARMACY_ID',
    'dry_run': 'DRY_RUN',
    'poll_interval': 'POLL_INTERVAL',
}


def _map_key(k: str) -> str:
    """Map a config.json key to standard env var name."""
    return _KEY_MAP.get(k, k)


def _get_exe_dir() -> str:
    """Return the directory of the running exe (or script if not frozen)."""
    if getattr(sys, 'frozen', False):
        # PyInstaller: sys.executable is the .exe
        return os.path.dirname(sys.executable)
    else:
        # Dev mode: next to this file
        return os.path.dirname(os.path.abspath(__file__))


def _find_env_local() -> str:
    """Search for .env.local going up from the agent directory."""
    # 1. Next to agent/ (project root)
    agent_dir = os.path.dirname(os.path.abspath(__file__))
    parent = os.path.dirname(agent_dir)
    candidate = os.path.join(parent, '.env.local')
    if os.path.exists(candidate):
        return candidate
    # 2. Same dir as exe
    exe_candidate = os.path.join(_get_exe_dir(), '.env.local')
    if os.path.exists(exe_candidate):
        return exe_candidate
    return ''


class ConfigManager:
    def __init__(self):
        self._data: dict = {}
        self.config_path = os.path.join(_get_exe_dir(), 'config.json')
        self._loaded = False

    def load(self) -> dict:
        """Load config from all sources (priority: config.json > .env.local > env vars)."""
        combined = {}

        # 3. Environment variables (lowest priority)
        for key in ALL_KEYS:
            val = os.environ.get(key, '')
            if val:
                combined[key] = val
            # Also try alternate names
            if key == 'SUPABASE_KEY':
                alt = os.environ.get('SUPABASE_ANON_KEY') or os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY', '')
                if alt and not combined.get(key):
                    combined[key] = alt
            if key == 'SUPABASE_URL':
                alt = os.environ.get('NEXT_PUBLIC_SUPABASE_URL', '')
                if alt and not combined.get(key):
                    combined[key] = alt

        # 2. .env.local
        env_path = _find_env_local()
        if env_path:
            try:
                with open(env_path, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if '=' in line and not line.startswith('#'):
                            k, v = line.split('=', 1)
                            k = k.strip()
                            v = v.strip().strip('"').strip("'")
                            # Normalise alternate key names
                            if k in ('NEXT_PUBLIC_SUPABASE_URL',):
                                k = 'SUPABASE_URL'
                            if k in ('NEXT_PUBLIC_SUPABASE_ANON_KEY', 'SUPABASE_ANON_KEY'):
                                k = 'SUPABASE_KEY'
                            if k in ALL_KEYS:
                                combined[k] = v
                log.info(f"Loaded .env.local from {env_path}")
            except Exception as e:
                log.warning(f"Could not read .env.local: {e}")

        # 1b. Bundled config.json inside PyInstaller exe
        if getattr(sys, 'frozen', False):
            bundled = os.path.join(sys._MEIPASS, 'config.json')
            if os.path.exists(bundled):
                try:
                    with open(bundled, 'r', encoding='utf-8') as f:
                        saved = json.load(f)
                    for k, v in saved.items():
                        mapped = _map_key(k)
                        if v:
                            combined[mapped] = str(v)
                    log.info(f"Loaded bundled config.json from {bundled}")
                except Exception as e:
                    log.warning(f"Could not read bundled config.json: {e}")

        # 1a. config.json next to exe (highest priority — overrides bundled)
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    saved = json.load(f)
                for k, v in saved.items():
                    mapped = _map_key(k)
                    if v:  # Only override if non-empty
                        combined[mapped] = str(v)
                log.info(f"Loaded config.json from {self.config_path}")
            except Exception as e:
                log.warning(f"Could not read config.json: {e}")

        self._data = combined
        self._loaded = True
        return dict(combined)

    def save(self) -> bool:
        """Save current config to config.json next to the exe."""
        try:
            # Only save non-empty values
            to_save = {k: v for k, v in self._data.items() if v}
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(to_save, f, indent=2)
            log.info(f"Config saved to {self.config_path}")
            return True
        except Exception as e:
            log.error(f"Could not save config.json: {e}")
            return False

    def get(self, key: str, default: str = '') -> str:
        """Get a config value by key."""
        if not self._loaded:
            self.load()
        return self._data.get(key, default)

    def set(self, key: str, value: str):
        """Set a config value (in memory; call save() to persist)."""
        if not self._loaded:
            self.load()
        self._data[key] = value

    def is_configured(self) -> bool:
        """Return True if all required keys have values."""
        if not self._loaded:
            self.load()
        return all(self._data.get(k, '').strip() for k in REQUIRED_KEYS)

    def as_dict(self) -> dict:
        """Return a copy of the current config."""
        if not self._loaded:
            self.load()
        return dict(self._data)

    def set_env_vars(self):
        """Push config into os.environ so existing code that reads env vars works."""
        if not self._loaded:
            self.load()
        for k, v in self._data.items():
            if v:
                os.environ[k] = v
        # Also set the alternate names vision_agent.py looks for
        if self._data.get('SUPABASE_URL'):
            os.environ['NEXT_PUBLIC_SUPABASE_URL'] = self._data['SUPABASE_URL']
        if self._data.get('SUPABASE_KEY'):
            os.environ['NEXT_PUBLIC_SUPABASE_ANON_KEY'] = self._data['SUPABASE_KEY']
