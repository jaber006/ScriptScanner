"""ScriptScanner Dispensary Agent

Polls Supabase for pending dispense jobs and injects keystrokes into Z Dispense.

Architecture:
  Vercel Web App → Supabase (dispense_jobs) ← This Agent → Z Dispense

Usage:
  python dispensary_agent.py                 # Run agent (polls every 3s)
  python dispensary_agent.py --dry-run       # Log keystrokes without typing
  python dispensary_agent.py --once          # Process one job and exit

Environment Variables:
  SUPABASE_URL       — Supabase project URL
  SUPABASE_KEY       — Supabase anon/service key
  PHARMACY_ID        — Unique pharmacy identifier (default: "legana-dds")
  PHARMACIST_INITIALS — Default pharmacist initials for dispensing

Requires:
  pip install supabase pyautogui
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
PHARMACY_ID = os.environ.get("PHARMACY_ID", "legana-dds")
PHARMACIST_INITIALS = os.environ.get("PHARMACIST_INITIALS", "MJ")
POLL_INTERVAL = 3  # seconds
INTER_FIELD_DELAY = 0.15  # seconds between keystrokes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("dispensary_agent")


# ---------------------------------------------------------------------------
# Z Dispense Keystroke Maps (Barney Layout)
# ---------------------------------------------------------------------------

# ALT shortcuts for each field
FIELD_SHORTCUTS = {
    "patient": "alt+n",
    "supply_type": "alt+y",
    "script_date": None,  # tab from supply_type
    "doctor": "alt+d",
    "drug": "alt+u",
    "directions": "alt+c",
    "repeats": None,  # tab from directions
    "quantity": "alt+q",
    "price": None,  # tab from quantity (auto-filled)
    "pharmacist": "alt+r",
}

# Script type mapping
SCRIPT_TYPE_MAP = {
    "PBS": "N",
    "GENERAL": "N",
    "PRIVATE": "P",
    "RPBS": "R",
    "REPAT": "R",
    "DVA": "R",
    "DENTAL": "D",
    "OPTOMETRICAL": "E",
    "NURSE": "U",
    "MIDWIFE": "F",
    "EMERGENCY": "B",
    "CONTINUED": "C",
    "S3R": "T",
    "NON-PBS": "S",
}


# ---------------------------------------------------------------------------
# Supabase Client
# ---------------------------------------------------------------------------

def get_supabase():
    """Initialize Supabase client."""
    try:
        from supabase import create_client
    except ImportError:
        logger.error("supabase-py not installed. Run: pip install supabase")
        sys.exit(1)

    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("Set SUPABASE_URL and SUPABASE_KEY environment variables")
        sys.exit(1)

    return create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_pending_jobs(client):
    """Fetch pending dispense jobs for this pharmacy."""
    result = (
        client.table("dispense_jobs")
        .select("*")
        .eq("pharmacy_id", PHARMACY_ID)
        .eq("status", "pending")
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    return result.data or []


def update_job_status(client, job_id: str, status: str, result: dict = None):
    """Update job status in Supabase."""
    update = {
        "status": status,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if result:
        update["result"] = json.dumps(result)

    client.table("dispense_jobs").update(update).eq("id", job_id).execute()


# ---------------------------------------------------------------------------
# Keystroke Injection
# ---------------------------------------------------------------------------

def build_drug_search(drug_name: str, form: str = "", strength: str = "") -> str:
    """Build Z Dispense drug search string: 'drug form strength'."""
    parts = [drug_name, form, strength]
    return " ".join(p for p in parts if p).lower()


def inject_keystrokes(job: dict, dry_run: bool = False):
    """Inject prescription data into Z Dispense via keystrokes.

    Args:
        job: Dispense job from Supabase
        dry_run: If True, log actions without sending keystrokes
    """
    if not dry_run:
        try:
            import pyautogui
            pyautogui.FAILSAFE = True  # Move mouse to corner to abort
        except ImportError:
            logger.error("pyautogui not installed. Run: pip install pyautogui")
            return False

    payload = job.get("payload", {})
    patient = payload.get("patient", {})
    doctor = payload.get("doctor", {})
    script_type = payload.get("scriptType", "PBS")
    script_date = payload.get("scriptDate", "")
    items = payload.get("items", [])
    deferred = payload.get("deferredItems", [])

    type_code = SCRIPT_TYPE_MAP.get(script_type.upper(), "N")

    all_items = [(item, False) for item in items] + [(item, True) for item in deferred]

    if not all_items:
        logger.warning("No items to dispense")
        return False

    for idx, (item, is_deferred) in enumerate(all_items):
        logger.info("--- Item %d/%d %s ---", idx + 1, len(all_items),
                     "(DEFER)" if is_deferred else "")

        drug_search = build_drug_search(
            item.get("drugName", ""),
            item.get("form", ""),
            item.get("strength", ""),
        )

        repeats = item.get("repeats", "0")
        if is_deferred:
            repeats = f"{repeats}D"

        steps = [
            ("CLEAR SCREEN", "shift+escape", None),
            ("Patient", "alt+n", patient.get("name", "")),
            ("Supply Type", "alt+y", type_code),
            ("Script Date", "tab", script_date),
            ("Doctor", "alt+d", doctor.get("name", "")),
            ("Drug", "alt+u", drug_search),
            ("Directions", "alt+c", item.get("directions", "S")),
            ("Repeats", "tab", repeats),
            ("Quantity", "alt+q", item.get("quantity", "")),
            ("Pharmacist", "alt+r", PHARMACIST_INITIALS),
            ("SAVE", "F10", None),
        ]

        for step_name, shortcut, value in steps:
            if dry_run:
                if value:
                    logger.info("  [DRY] %s → %s → type '%s'", step_name, shortcut, value)
                else:
                    logger.info("  [DRY] %s → %s", step_name, shortcut)
                continue

            # Send shortcut
            keys = shortcut.split("+")
            if len(keys) > 1:
                pyautogui.hotkey(*keys)
            else:
                pyautogui.press(keys[0])
            time.sleep(0.08)

            # Type value if present
            if value:
                pyautogui.write(value, interval=0.02)

                # Press Enter for search fields
                if step_name in ("Patient", "Doctor", "Drug"):
                    time.sleep(0.2)
                    pyautogui.press("enter")
                    time.sleep(0.3)

            time.sleep(INTER_FIELD_DELAY)

        logger.info("  ✓ Item %d complete", idx + 1)

    return True


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

def run_agent(dry_run: bool = False, once: bool = False):
    """Main agent loop — poll Supabase and dispense."""
    logger.info("ScriptScanner Dispensary Agent starting")
    logger.info("  Pharmacy: %s", PHARMACY_ID)
    logger.info("  Pharmacist: %s", PHARMACIST_INITIALS)
    logger.info("  Dry run: %s", dry_run)
    logger.info("  Poll interval: %ds", POLL_INTERVAL)

    client = get_supabase()
    logger.info("Connected to Supabase")

    while True:
        try:
            jobs = fetch_pending_jobs(client)

            if jobs:
                job = jobs[0]
                job_id = job["id"]
                logger.info("Found pending job: %s", job_id)

                # Mark as processing
                update_job_status(client, job_id, "processing")

                # Inject keystrokes
                success = inject_keystrokes(job, dry_run=dry_run)

                # Update status
                if success:
                    update_job_status(client, job_id, "completed", {
                        "dispensed_at": datetime.now(timezone.utc).isoformat(),
                        "pharmacist": PHARMACIST_INITIALS,
                    })
                    logger.info("Job %s completed", job_id)
                else:
                    update_job_status(client, job_id, "failed", {
                        "error": "Injection failed",
                    })
                    logger.error("Job %s failed", job_id)

                if once:
                    break

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Agent stopped by user")
            break
        except Exception as e:
            logger.error("Error: %s", e)
            time.sleep(POLL_INTERVAL * 2)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ScriptScanner Dispensary Agent")
    parser.add_argument("--dry-run", action="store_true", help="Log keystrokes without typing")
    parser.add_argument("--once", action="store_true", help="Process one job and exit")
    args = parser.parse_args()

    run_agent(dry_run=args.dry_run, once=args.once)
