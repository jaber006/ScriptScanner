"""
ScriptScanner Learning Cache
=============================
Perceptual hashing + SQLite action memory to reduce Claude API calls over time.

As the agent sees the same screens repeatedly, it learns what actions work and
can execute them instantly (Tier 1) or with a cheap verification call (Tier 2)
instead of a full Sonnet vision call (Tier 3).

Usage:
    from learning_cache import LearningCache
    cache = LearningCache()

    result = cache.lookup(screenshot_pil, task_type="type_patient")
    if result:
        # Use cached action
    else:
        # Call Claude, then:
        cache.record(screenshot_pil, action_dict, success=True, task_type="type_patient")
"""

import os
import json
import sqlite3
import hashlib
import logging
import threading
from datetime import datetime
from typing import Optional
from io import BytesIO

try:
    from PIL import Image
    import imagehash
    IMAGEHASH_AVAILABLE = True
except ImportError:
    IMAGEHASH_AVAILABLE = False

log = logging.getLogger('learning_cache')

# Path to the cache database (same dir as this file)
CACHE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(CACHE_DIR, 'cache.db')

# pHash similarity threshold: hamming distance <= 8 means "same screen"
HASH_THRESHOLD = 8

# Confidence tier thresholds
TIER1_MIN_SUCCESS = 5   # Instant: no API call
TIER2_MIN_SUCCESS = 2   # Verify: cheap Haiku call
# else TIER3: full Sonnet call

# Approximate costs (USD per 1M tokens, as of 2025)
SONNET_INPUT_COST  = 3.00   # claude-sonnet-4 input
SONNET_OUTPUT_COST = 15.00  # claude-sonnet-4 output
HAIKU_INPUT_COST   = 0.80   # claude-haiku-3-5 input
HAIKU_OUTPUT_COST  = 4.00   # claude-haiku-3-5 output

# Typical token counts per call
SONNET_AVG_INPUT_TOKENS  = 1800  # screenshot (vision) + context
SONNET_AVG_OUTPUT_TOKENS = 80
HAIKU_AVG_INPUT_TOKENS   = 300   # text-only verify call
HAIKU_AVG_OUTPUT_TOKENS  = 20

SONNET_COST_PER_CALL = (
    SONNET_AVG_INPUT_TOKENS  / 1_000_000 * SONNET_INPUT_COST +
    SONNET_AVG_OUTPUT_TOKENS / 1_000_000 * SONNET_OUTPUT_COST
)
HAIKU_COST_PER_CALL = (
    HAIKU_AVG_INPUT_TOKENS  / 1_000_000 * HAIKU_INPUT_COST +
    HAIKU_AVG_OUTPUT_TOKENS / 1_000_000 * HAIKU_OUTPUT_COST
)


# ─── Schema ───────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS action_memory (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    screen_hash   TEXT NOT NULL,        -- pHash of the screenshot (hex)
    screen_context TEXT,                -- "patient_search", "drug_field", etc.
    task_type     TEXT,                 -- "type_patient", "click_search", etc.
    action_json   TEXT NOT NULL,        -- full action JSON that worked
    success_count INTEGER DEFAULT 1,
    fail_count    INTEGER DEFAULT 0,
    last_used     TIMESTAMP,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workflow_sequences (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_hash  TEXT NOT NULL,       -- hash of the full item data pattern
    step_number    INTEGER NOT NULL,
    screen_hash    TEXT,
    action_json    TEXT NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS session_stats (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id    TEXT NOT NULL,
    tier1_hits    INTEGER DEFAULT 0,
    tier2_hits    INTEGER DEFAULT 0,
    tier3_hits    INTEGER DEFAULT 0,
    total_actions INTEGER DEFAULT 0,
    tokens_sonnet INTEGER DEFAULT 0,
    tokens_haiku  INTEGER DEFAULT 0,
    scripts_done  INTEGER DEFAULT 0,
    started_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_action_memory_hash ON action_memory(screen_hash);
CREATE INDEX IF NOT EXISTS idx_workflow_hash ON workflow_sequences(workflow_hash, step_number);
"""


# ─── LearningCache ────────────────────────────────────────────────────────────

class LearningCache:
    """
    Perceptual-hash-based action cache for the ScriptScanner vision agent.

    Thread-safe via SQLite WAL mode + a threading.Lock for hash lookups.
    Gracefully degrades if imagehash is missing or DB is corrupted.
    """

    def __init__(self, db_path: str = DB_PATH, enabled: bool = True):
        self.db_path = db_path
        self.enabled = enabled and IMAGEHASH_AVAILABLE
        self._lock = threading.Lock()
        self._session_id = datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')

        if not IMAGEHASH_AVAILABLE and enabled:
            log.warning("imagehash library not installed — learning cache disabled. "
                        "Run: pip install imagehash")

        if self.enabled:
            self._init_db()
            self._init_session()
            log.info(f"Learning cache ready: {self.db_path}")
        else:
            log.info("Learning cache disabled.")

    # ── DB Init ───────────────────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Create schema. Rebuilds silently if DB is corrupted."""
        try:
            with self._get_conn() as conn:
                conn.executescript(SCHEMA_SQL)
        except sqlite3.DatabaseError as e:
            log.warning(f"Cache DB error ({e}) — rebuilding fresh DB at {self.db_path}")
            try:
                os.remove(self.db_path)
            except OSError:
                pass
            with self._get_conn() as conn:
                conn.executescript(SCHEMA_SQL)

    def _init_session(self):
        try:
            with self._get_conn() as conn:
                conn.execute(
                    "INSERT INTO session_stats (session_id) VALUES (?)",
                    (self._session_id,)
                )
        except Exception as e:
            log.debug(f"Session init error: {e}")

    # ── Perceptual Hashing ────────────────────────────────────────────────────

    def _phash(self, image: "Image.Image") -> Optional[str]:
        """
        Compute perceptual hash of the full image + key regions.
        Returns a hex string, or None on error.
        """
        if not IMAGEHASH_AVAILABLE:
            return None
        try:
            w, h = image.size
            # Full image hash
            full_hash = imagehash.phash(image, hash_size=8)

            # Title bar region (top ~10%)
            title_region = image.crop((0, 0, w, max(1, int(h * 0.10))))
            title_hash = imagehash.phash(title_region, hash_size=8)

            # Main content area (10%-85% height)
            main_region = image.crop((0, int(h * 0.10), w, int(h * 0.85)))
            main_hash = imagehash.phash(main_region, hash_size=8)

            # Dialog area (center 40% width, 30%-70% height) — where popups appear
            cx1, cy1 = int(w * 0.30), int(h * 0.30)
            cx2, cy2 = int(w * 0.70), int(h * 0.70)
            dialog_region = image.crop((cx1, cy1, cx2, cy2))
            dialog_hash = imagehash.phash(dialog_region, hash_size=8)

            # Combine into a single string
            combined = f"{full_hash}:{title_hash}:{main_hash}:{dialog_hash}"
            return combined
        except Exception as e:
            log.debug(f"pHash error: {e}")
            return None

    def _hash_distance(self, hash_str1: str, hash_str2: str) -> int:
        """
        Compute hamming distance between two combined hash strings.
        Returns the SUM of component distances (lower = more similar).
        """
        try:
            parts1 = hash_str1.split(':')
            parts2 = hash_str2.split(':')
            if len(parts1) != len(parts2):
                return 9999
            total = 0
            for p1, p2 in zip(parts1, parts2):
                h1 = imagehash.hex_to_hash(p1)
                h2 = imagehash.hex_to_hash(p2)
                total += (h1 - h2)
            return total
        except Exception as e:
            log.debug(f"Hash distance error: {e}")
            return 9999

    # ── Lookup ────────────────────────────────────────────────────────────────

    def lookup(
        self,
        screenshot: "Image.Image",
        task_type: Optional[str] = None,
    ) -> Optional[dict]:
        """
        Look up a cached action for this screen state.

        Returns a dict with:
            {
                "action": <action dict>,
                "tier": 1 or 2,
                "screen_hash": <hash str>,
                "confidence": {success_count, fail_count},
                "cache_id": <row id>,
            }
        or None if no reliable match found (→ use full Sonnet call).
        """
        if not self.enabled:
            return None

        screen_hash = self._phash(screenshot)
        if not screen_hash:
            return None

        try:
            with self._lock:
                return self._lookup_by_hash(screen_hash, task_type)
        except Exception as e:
            log.debug(f"Cache lookup error: {e}")
            return None

    def _lookup_by_hash(self, screen_hash: str, task_type: Optional[str]) -> Optional[dict]:
        """Find the best matching cached action for this screen hash."""
        with self._get_conn() as conn:
            # Fetch candidates with same task_type (or all if no task_type)
            if task_type:
                rows = conn.execute(
                    """SELECT * FROM action_memory
                       WHERE task_type = ? AND fail_count <= 2
                       ORDER BY success_count DESC, last_used DESC
                       LIMIT 50""",
                    (task_type,)
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM action_memory
                       WHERE fail_count <= 2
                       ORDER BY success_count DESC, last_used DESC
                       LIMIT 100"""
                ).fetchall()

            best_row = None
            best_distance = HASH_THRESHOLD + 1

            for row in rows:
                dist = self._hash_distance(screen_hash, row['screen_hash'])
                if dist <= HASH_THRESHOLD and dist < best_distance:
                    best_distance = dist
                    best_row = row

            if not best_row:
                return None

            success = best_row['success_count']
            fails   = best_row['fail_count']

            if success >= TIER1_MIN_SUCCESS and fails == 0:
                tier = 1
            elif success >= TIER2_MIN_SUCCESS:
                tier = 2
            else:
                return None  # Not confident enough

            try:
                action = json.loads(best_row['action_json'])
            except json.JSONDecodeError:
                return None

            return {
                "action": action,
                "tier": tier,
                "screen_hash": screen_hash,
                "confidence": {"success_count": success, "fail_count": fails},
                "cache_id": best_row['id'],
                "screen_context": best_row['screen_context'],
            }

    # ── Record ────────────────────────────────────────────────────────────────

    def record(
        self,
        screenshot: "Image.Image",
        action: dict,
        success: bool,
        task_type: Optional[str] = None,
        screen_context: Optional[str] = None,
        cache_id: Optional[int] = None,
    ):
        """
        Record the outcome of an action for this screen state.

        If cache_id is provided (from a prior lookup), updates that row.
        Otherwise, inserts a new row or increments an existing similar one.
        """
        if not self.enabled:
            return

        screen_hash = self._phash(screenshot)
        if not screen_hash:
            return

        try:
            with self._lock:
                if success:
                    self._record_success(screen_hash, action, task_type, screen_context, cache_id)
                else:
                    self._record_failure(screen_hash, action, task_type, cache_id)
        except Exception as e:
            log.debug(f"Cache record error: {e}")

    def _record_success(self, screen_hash, action, task_type, screen_context, cache_id):
        action_json = json.dumps(action)
        now = datetime.utcnow().isoformat()

        with self._get_conn() as conn:
            if cache_id:
                # Update existing row
                conn.execute(
                    """UPDATE action_memory
                       SET success_count = success_count + 1, last_used = ?
                       WHERE id = ?""",
                    (now, cache_id)
                )
                return

            # Check if a similar screen hash exists
            rows = conn.execute(
                "SELECT id, screen_hash FROM action_memory WHERE task_type = ? LIMIT 100",
                (task_type or '',)
            ).fetchall()

            for row in rows:
                if self._hash_distance(screen_hash, row['screen_hash']) <= HASH_THRESHOLD:
                    conn.execute(
                        """UPDATE action_memory
                           SET success_count = success_count + 1, last_used = ?,
                               action_json = ?
                           WHERE id = ?""",
                        (now, action_json, row['id'])
                    )
                    return

            # Insert new row
            conn.execute(
                """INSERT INTO action_memory
                   (screen_hash, screen_context, task_type, action_json, success_count, last_used)
                   VALUES (?, ?, ?, ?, 1, ?)""",
                (screen_hash, screen_context, task_type, action_json, now)
            )

    def _record_failure(self, screen_hash, action, task_type, cache_id):
        now = datetime.utcnow().isoformat()
        with self._get_conn() as conn:
            if cache_id:
                conn.execute(
                    """UPDATE action_memory
                       SET fail_count = fail_count + 1, last_used = ?
                       WHERE id = ?""",
                    (now, cache_id)
                )
                # Invalidate if too many failures
                conn.execute(
                    """UPDATE action_memory
                       SET success_count = 0
                       WHERE id = ? AND fail_count > 2""",
                    (cache_id,)
                )
                return

            # Find matching row by hash
            rows = conn.execute(
                "SELECT id, screen_hash FROM action_memory WHERE task_type = ? LIMIT 100",
                (task_type or '',)
            ).fetchall()
            for row in rows:
                if self._hash_distance(screen_hash, row['screen_hash']) <= HASH_THRESHOLD:
                    conn.execute(
                        """UPDATE action_memory
                           SET fail_count = fail_count + 1, last_used = ?
                           WHERE id = ?""",
                        (now, row['id'])
                    )
                    conn.execute(
                        """UPDATE action_memory
                           SET success_count = 0
                           WHERE id = ? AND fail_count > 2""",
                        (row['id'],)
                    )
                    return

    # ── Workflow Recording ────────────────────────────────────────────────────

    @staticmethod
    def _workflow_hash(item_data: dict) -> str:
        """Hash the structural pattern of a prescription item (drug + supply type)."""
        pattern = {
            'drug_prefix': (item_data.get('drug', '') or '')[:6].upper(),
            'supply_type': item_data.get('supplyType', 'N'),
            'has_repeats': int(item_data.get('repeats', '0') or '0') > 0,
        }
        return hashlib.sha256(json.dumps(pattern, sort_keys=True).encode()).hexdigest()[:16]

    def record_workflow(self, item_data: dict, steps: list[dict]):
        """
        Record a complete successful workflow sequence.

        steps: list of {"screenshot": PIL.Image, "action": dict}
        """
        if not self.enabled or not steps:
            return

        wf_hash = self._workflow_hash(item_data)
        try:
            with self._lock, self._get_conn() as conn:
                # Remove old sequence for this workflow
                conn.execute(
                    "DELETE FROM workflow_sequences WHERE workflow_hash = ?",
                    (wf_hash,)
                )
                for i, step in enumerate(steps):
                    screen_hash = self._phash(step.get('screenshot'))
                    action_json = json.dumps(step.get('action', {}))
                    conn.execute(
                        """INSERT INTO workflow_sequences
                           (workflow_hash, step_number, screen_hash, action_json)
                           VALUES (?, ?, ?, ?)""",
                        (wf_hash, i, screen_hash, action_json)
                    )
            log.info(f"Recorded workflow {wf_hash} ({len(steps)} steps)")
        except Exception as e:
            log.debug(f"Workflow record error: {e}")

    def lookup_workflow(self, item_data: dict) -> Optional[list[dict]]:
        """
        Look up a previously recorded workflow for this item pattern.
        Returns list of action dicts or None.
        """
        if not self.enabled:
            return None

        wf_hash = self._workflow_hash(item_data)
        try:
            with self._get_conn() as conn:
                rows = conn.execute(
                    """SELECT action_json FROM workflow_sequences
                       WHERE workflow_hash = ?
                       ORDER BY step_number ASC""",
                    (wf_hash,)
                ).fetchall()
                if not rows:
                    return None
                return [json.loads(r['action_json']) for r in rows]
        except Exception as e:
            log.debug(f"Workflow lookup error: {e}")
            return None

    # ── Haiku Verification ────────────────────────────────────────────────────

    def verify_with_haiku(
        self,
        client,
        screenshot_b64: str,
        cached_action: dict,
        screen_context: Optional[str] = None,
    ) -> bool:
        """
        Ask claude-haiku-3-5 whether the cached action is appropriate for this screen.
        Returns True if verified, False if mismatch (fall through to Sonnet).
        """
        try:
            context_hint = screen_context or cached_action.get('description', 'screen action')
            question = (
                f"I'm about to perform this action on the screen:\n"
                f"{json.dumps(cached_action)}\n\n"
                f"Expected screen context: {context_hint}\n\n"
                f"Does the current screenshot match what I'd expect for this action? "
                f"Reply with exactly: YES or NO"
            )
            response = client.messages.create(
                model="claude-haiku-3-5-20241022",
                max_tokens=10,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": screenshot_b64,
                                },
                            },
                            {"type": "text", "text": question},
                        ],
                    }
                ],
            )
            answer = response.content[0].text.strip().upper()
            verified = answer.startswith('YES')
            log.debug(f"Haiku verify: {answer} → {'✅' if verified else '❌'}")

            # Track usage
            self._bump_stat('tier2_hits', 1)
            if hasattr(response, 'usage') and response.usage:
                self._bump_stat('tokens_haiku',
                                (response.usage.input_tokens or 0) +
                                (response.usage.output_tokens or 0))
            return verified
        except Exception as e:
            log.debug(f"Haiku verify error: {e}")
            return False  # On error, fall back to Sonnet

    # ── Session Stats ─────────────────────────────────────────────────────────

    def _bump_stat(self, column: str, amount: int = 1):
        """Increment a session stat column."""
        try:
            now = datetime.utcnow().isoformat()
            with self._get_conn() as conn:
                conn.execute(
                    f"""UPDATE session_stats
                        SET {column} = {column} + ?, updated_at = ?
                        WHERE session_id = ?""",
                    (amount, now, self._session_id)
                )
        except Exception as e:
            log.debug(f"Stat bump error: {e}")

    def track_tier(self, tier: int, tokens_used: int = 0):
        """Call after each action with the tier used."""
        col_map = {1: 'tier1_hits', 2: 'tier2_hits', 3: 'tier3_hits'}
        col = col_map.get(tier, 'tier3_hits')
        self._bump_stat(col, 1)
        self._bump_stat('total_actions', 1)
        if tier == 3:
            self._bump_stat('tokens_sonnet', tokens_used)

    def track_script_complete(self):
        """Call after a full prescription dispense completes."""
        self._bump_stat('scripts_done', 1)

    def get_stats(self) -> dict:
        """Return cumulative stats across ALL sessions."""
        if not self.enabled:
            return {"enabled": False}
        try:
            with self._get_conn() as conn:
                totals = conn.execute(
                    """SELECT
                         SUM(tier1_hits)    AS t1,
                         SUM(tier2_hits)    AS t2,
                         SUM(tier3_hits)    AS t3,
                         SUM(total_actions) AS total,
                         SUM(tokens_sonnet) AS ts,
                         SUM(tokens_haiku)  AS th,
                         SUM(scripts_done)  AS scripts
                       FROM session_stats"""
                ).fetchone()

                cache_count = conn.execute(
                    "SELECT COUNT(*) FROM action_memory WHERE success_count > 0"
                ).fetchone()[0]

                workflow_count = conn.execute(
                    "SELECT COUNT(DISTINCT workflow_hash) FROM workflow_sequences"
                ).fetchone()[0]

            t1 = totals['t1'] or 0
            t2 = totals['t2'] or 0
            t3 = totals['t3'] or 0
            total = totals['total'] or 0
            ts = totals['ts'] or 0
            th = totals['th'] or 0
            scripts = totals['scripts'] or 0

            # Cost estimates
            actual_cost = (
                (t3 * SONNET_COST_PER_CALL) +
                (t2 * HAIKU_COST_PER_CALL)
            )
            naive_cost = total * SONNET_COST_PER_CALL
            saved = naive_cost - actual_cost

            hit_rate = (t1 + t2) / total * 100 if total > 0 else 0

            return {
                "enabled": True,
                "cached_screens": cache_count,
                "recorded_workflows": workflow_count,
                "total_actions": total,
                "tier1_instant": t1,
                "tier2_verified": t2,
                "tier3_full": t3,
                "hit_rate_pct": round(hit_rate, 1),
                "scripts_dispensed": scripts,
                "tokens_sonnet": ts,
                "tokens_haiku": th,
                "estimated_cost_usd": round(actual_cost, 4),
                "estimated_saved_usd": round(saved, 4),
                "cost_per_script": round(actual_cost / scripts, 4) if scripts > 0 else 0,
            }
        except Exception as e:
            log.debug(f"Stats error: {e}")
            return {"enabled": True, "error": str(e)}

    def print_stats(self):
        """Pretty-print cache statistics."""
        s = self.get_stats()
        if not s.get('enabled'):
            print("Learning cache: DISABLED")
            return

        print("\n" + "=" * 55)
        print("  📊 ScriptScanner Learning Cache Stats")
        print("=" * 55)
        print(f"  Cached screen states:   {s.get('cached_screens', 0)}")
        print(f"  Recorded workflows:     {s.get('recorded_workflows', 0)}")
        print(f"  Scripts dispensed:      {s.get('scripts_dispensed', 0)}")
        print()
        print(f"  Total actions:          {s.get('total_actions', 0)}")
        print(f"    Tier 1 (instant):     {s.get('tier1_instant', 0)}")
        print(f"    Tier 2 (verified):    {s.get('tier2_verified', 0)}")
        print(f"    Tier 3 (full):        {s.get('tier3_full', 0)}")
        print(f"  Cache hit rate:         {s.get('hit_rate_pct', 0):.1f}%")
        print()
        print(f"  Tokens (Sonnet):        {s.get('tokens_sonnet', 0):,}")
        print(f"  Tokens (Haiku):         {s.get('tokens_haiku', 0):,}")
        print(f"  Estimated cost:         ${s.get('estimated_cost_usd', 0):.4f}")
        print(f"  Estimated saved:        ${s.get('estimated_saved_usd', 0):.4f}")
        if s.get('scripts_dispensed', 0) > 0:
            print(f"  Cost per script:        ${s.get('cost_per_script', 0):.4f}")
        print("=" * 55 + "\n")

    def clear(self):
        """Reset the entire learning database."""
        try:
            with self._get_conn() as conn:
                conn.execute("DELETE FROM action_memory")
                conn.execute("DELETE FROM workflow_sequences")
                conn.execute("DELETE FROM session_stats")
            log.info("Learning cache cleared.")
            return True
        except Exception as e:
            log.error(f"Cache clear error: {e}")
            return False
