"""
ScriptScanner Vision Agent
===========================
AI-powered prescription dispensing using Claude Vision API.

Instead of hardcoded keystrokes, this agent:
1. Takes a screenshot of the desktop
2. Sends it to Claude with the prescription data
3. Claude tells us what to do next (click, type, press, wait, done)
4. We execute it and take another screenshot
5. Repeat until done or error

Learning cache (see learning_cache.py) makes the agent faster and cheaper
over time by remembering what actions work for each screen state.

Usage:
    python vision_agent.py                    # Poll Supabase and dispense
    python vision_agent.py --dry-run          # See what Claude says, don't act
    python vision_agent.py --once             # Process one job and exit
    python vision_agent.py --test-screenshot  # Take screenshot, show Claude's analysis
    python vision_agent.py --stats            # Show cache hit rate and cost savings
    python vision_agent.py --clear-cache      # Reset the learning database
    python vision_agent.py --learning off     # Disable caching (always full vision)
"""

import os
import sys
import time
import json
import base64
import logging
import argparse
import re
from io import BytesIO
from datetime import datetime
from threading import Thread, Lock

import pyautogui
import pyperclip
from PIL import Image

# Learning cache (gracefully degrades if imagehash not installed)
try:
    from learning_cache import LearningCache
    LEARNING_CACHE_MODULE = True
except ImportError:
    LEARNING_CACHE_MODULE = False

# --- Config ---
PORT = 9876
MAX_STEPS = 50          # Safety limit: max actions per prescription item
ACTION_DELAY = 0.3      # Wait after each action before next screenshot
MAX_IMG_WIDTH = 1280    # Resize screenshots to this width for API
POLL_INTERVAL = 3       # Seconds between Supabase polls
Z_DISPENSE_TITLE = "Z Dispense"
MODEL = "claude-sonnet-4-20250514"

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
log = logging.getLogger('vision_agent')

DRY_RUN = False
LEARNING_ENABLED = True
dispense_lock = Lock()

# Global learning cache instance (initialised in main())
cache: "LearningCache | None" = None

# --- Env Loading ---

def load_env():
    """Load .env.local from project root (parent of agent/)."""
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env.local')
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    env[k.strip()] = v.strip()
        log.info(f"Loaded config from {env_path}")
    return env

def get_config():
    env = load_env()
    return {
        'anthropic_key': (
            os.environ.get('ANTHROPIC_API_KEY') or
            env.get('ANTHROPIC_API_KEY', '')
        ),
        'supabase_url': (
            os.environ.get('SUPABASE_URL') or
            os.environ.get('NEXT_PUBLIC_SUPABASE_URL') or
            env.get('SUPABASE_URL') or
            env.get('NEXT_PUBLIC_SUPABASE_URL', '')
        ),
        'supabase_key': (
            os.environ.get('SUPABASE_KEY') or
            os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY') or
            env.get('SUPABASE_KEY') or
            env.get('NEXT_PUBLIC_SUPABASE_ANON_KEY', '')
        ),
    }

# --- Screenshot Utils ---

def take_screenshot(save=True) -> tuple[Image.Image, str]:
    """Take a screenshot, optionally save it, return (PIL image, base64 string)."""
    screenshot = pyautogui.screenshot()

    # Resize if wider than MAX_IMG_WIDTH
    w, h = screenshot.size
    if w > MAX_IMG_WIDTH:
        ratio = MAX_IMG_WIDTH / w
        new_h = int(h * ratio)
        screenshot = screenshot.resize((MAX_IMG_WIDTH, new_h), Image.LANCZOS)

    if save:
        screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots')
        os.makedirs(screenshots_dir, exist_ok=True)
        ts = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        path = os.path.join(screenshots_dir, f'screen_{ts}.png')
        screenshot.save(path)

    # Convert to base64
    buf = BytesIO()
    screenshot.save(buf, format='PNG')
    b64 = base64.standard_b64encode(buf.getvalue()).decode('utf-8')

    return screenshot, b64

# --- Claude Vision ---

SYSTEM_PROMPT = """You are a pharmacy dispensing assistant controlling Z Dispense software on a Windows desktop.

Your ONLY job is to guide the dispensing of a prescription step-by-step by analyzing the current screen.

RESPONSE FORMAT:
Always respond with exactly ONE JSON object. No markdown, no explanation, no code blocks. Just the JSON.

Valid actions:
{"action": "click", "x": 123, "y": 456, "description": "Click New Dispense button"}
{"action": "type", "text": "DARE", "description": "Type patient surname"}
{"action": "press", "key": "enter", "description": "Confirm patient selection"}
{"action": "press", "key": "tab", "description": "Move to next field"}
{"action": "press", "key": "f10", "description": "Print label and complete dispense"}
{"action": "press", "key": "escape", "description": "Close dialog"}
{"action": "wait", "seconds": 1, "description": "Wait for dialog to load"}
{"action": "done", "status": "completed", "description": "Dispensing complete — label printed"}
{"action": "error", "message": "Z Dispense not visible", "description": "Cannot proceed"}

RULES:
1. NEVER guess coordinates. Only click on UI elements you can CLEARLY see in the screenshot.
2. If Z Dispense is not open or visible, immediately return error.
3. If you see an unexpected dialog, error message, or popup, describe it in an error action.
4. Include the field name or button text in your description so the human can follow along.
5. One action at a time — you will get another screenshot after each action.
6. Move through Z Dispense fields in order: Patient → Supply Type → Script Date → Doctor → Drug → Directions → Repeats → Quantity → Price (skip with tab) → Initials → F10

WORKFLOW for each prescription item:
- Patient field: Type the patient surname, press Enter to search, wait for list, press Enter to select first match
- Supply Type: Type 'N' (Normal) or as specified, press Tab
- Script Date: Type the date (DD/MM/YYYY format), press Tab
- Doctor field: Type doctor surname, press Enter to search, press Enter to select
- Drug field: Type drug name, press Enter to search, press Enter to select
- Directions: Type full directions text, press Tab
- Repeats: Type number, press Tab
- Quantity: Type quantity, press Tab
- Price: Press Tab to skip (auto-calculated)
- Pharmacist initials: Type initials, wait 0.5s
- Press F10 to print label → this is DONE

IMPORTANT DETAILS:
- Z Dispense typically has a main window with fields on the left and dispensing info on the right
- Search results appear as a list/dialog — press Enter or double-click to select the first/correct match
- If a patient has multiple matches, select the one that matches the DOB in the prescription data
- If a doctor has multiple matches, select the one matching the name in the prescription data
- After F10, you may see a "Label printed" confirmation — that's done
- If you see "No script date" or similar validation errors, note them as errors
- Ctrl+A then Delete clears a field before typing
"""

def build_user_message(script_data: dict, item_index: int, step: int, history: list) -> str:
    """Build the user message with prescription context."""
    keystrokes = script_data.get('keystrokes', [])
    ks = keystrokes[item_index] if item_index < len(keystrokes) else {}

    lines = [
        f"PRESCRIPTION DATA (item {item_index + 1} of {len(keystrokes)}):",
        f"  Patient name: {script_data.get('patient_name', '')}",
        f"  Patient DOB: {script_data.get('patient_dob', '')}",
        f"  Doctor: {script_data.get('doctor_name', '')}",
        f"  Script date: {script_data.get('script_date', '')}",
        f"",
        f"CURRENT ITEM TO DISPENSE:",
        f"  Patient (for search): {ks.get('patient', '')}",
        f"  Doctor (for search): {ks.get('doctor', '')}",
        f"  Drug: {ks.get('drug', '')}",
        f"  Directions: {ks.get('directions', '')}",
        f"  Quantity: {ks.get('quantity', '')}",
        f"  Repeats: {ks.get('repeats', '0')}",
        f"  Supply type: {ks.get('supplyType', 'N')}",
        f"  Script date: {ks.get('scriptDate', script_data.get('script_date', ''))}",
    ]

    if item_index > 0:
        lines.append(f"\nNOTE: This is item {item_index + 1}. Patient is already loaded from item 1.")

    if history:
        lines.append(f"\nSTEPS TAKEN SO FAR (step {step}):")
        for i, h in enumerate(history[-5:]):  # last 5 actions
            lines.append(f"  {i+1}. {h}")

    lines.append(f"\nWhat is the current state of the screen? What should I do next?")
    return '\n'.join(lines)

def ask_claude(client, screenshot_b64: str, script_data: dict, item_index: int, step: int, history: list) -> tuple[dict, str, int]:
    """Send screenshot + context to Claude, get back a JSON action + token count."""
    user_text = build_user_message(script_data, item_index, step, history)

    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
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
                    {
                        "type": "text",
                        "text": user_text,
                    }
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()
    log.debug(f"Claude raw response: {raw}")

    # Token usage for cost tracking
    tokens_used = 0
    if hasattr(response, 'usage') and response.usage:
        tokens_used = (response.usage.input_tokens or 0) + (response.usage.output_tokens or 0)

    # Parse JSON — strip markdown if present
    json_str = raw
    # Remove ```json ... ``` if wrapped
    match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw)
    if match:
        json_str = match.group(1).strip()
    else:
        # Find first { ... } block
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            json_str = match.group(0)

    try:
        action = json.loads(json_str)
    except json.JSONDecodeError as e:
        log.error(f"Failed to parse Claude response as JSON: {e}")
        log.error(f"Raw: {raw}")
        action = {"action": "error", "message": f"JSON parse error: {e}", "description": raw}

    return action, raw, tokens_used

# --- Action Executor ---

def execute_action(action: dict) -> bool:
    """Execute a single action. Returns True if should continue, False if done/error."""
    act = action.get('action', '')
    desc = action.get('description', '')
    log.info(f"  ACTION [{act}]: {desc}")

    if DRY_RUN:
        log.info(f"  [DRY RUN] Would execute: {action}")
        return act not in ('done', 'error')

    if act == 'click':
        x, y = action.get('x'), action.get('y')
        if x is None or y is None:
            log.error("Click action missing x or y coordinates")
            return False
        pyautogui.click(x, y)
        time.sleep(ACTION_DELAY)
        return True

    elif act == 'type':
        text = action.get('text', '')
        if text:
            # Use clipboard paste for reliability
            pyperclip.copy(str(text))
            pyautogui.hotkey('ctrl', 'v')
            time.sleep(0.1)
        return True

    elif act == 'press':
        key = action.get('key', '')
        if key:
            pyautogui.press(key)
            time.sleep(ACTION_DELAY)
        return True

    elif act == 'wait':
        seconds = float(action.get('seconds', 1))
        log.info(f"  Waiting {seconds}s...")
        time.sleep(seconds)
        return True

    elif act == 'done':
        log.info(f"  ✅ DISPENSING COMPLETE: {desc}")
        return False

    elif act == 'error':
        msg = action.get('message', 'Unknown error')
        log.error(f"  ❌ ERROR: {msg} — {desc}")
        return False

    else:
        log.warning(f"  Unknown action: {act}")
        return True

# --- Focus Z Dispense ---

def focus_zdispense():
    """Bring Z Dispense window to front."""
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(Z_DISPENSE_TITLE)
        if windows:
            win = windows[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.5)
            log.info(f"Focused: {win.title}")
            return True
        else:
            log.warning(f"Window '{Z_DISPENSE_TITLE}' not found")
            return False
    except ImportError:
        log.warning("pygetwindow not installed")
        return False
    except Exception as e:
        log.warning(f"Could not focus Z Dispense: {e}")
        return False

# --- Core Vision Loop ---

def _infer_task_type(action: dict, step: int) -> str:
    """Infer a task_type label from an action dict for cache keying."""
    act = action.get('action', '')
    text = action.get('text', '')
    desc = (action.get('description', '') or '').lower()
    key = action.get('key', '')

    if act == 'type':
        if 'patient' in desc or 'surname' in desc:
            return 'type_patient'
        if 'doctor' in desc or 'dr ' in desc:
            return 'type_doctor'
        if 'drug' in desc or 'medic' in desc:
            return 'type_drug'
        if 'direction' in desc:
            return 'type_directions'
        if 'quantity' in desc or 'qty' in desc:
            return 'type_quantity'
        if 'repeat' in desc:
            return 'type_repeats'
        if 'initial' in desc:
            return 'type_initials'
        if 'date' in desc:
            return 'type_date'
        return f'type_step{step}'
    if act == 'press':
        return f'press_{key}'
    if act == 'click':
        return f'click_{desc[:20].replace(" ", "_")}'
    if act == 'wait':
        return 'wait'
    return act


def dispense_with_vision(client, script_data: dict, item_index: int = 0) -> bool:
    """
    Run the vision loop for one prescription item.
    Returns True on success, False on failure.

    Uses the global `cache` (LearningCache) to skip API calls for known screens.
    """
    global cache

    ks = script_data.get('keystrokes', [])[item_index] if script_data.get('keystrokes') else {}
    drug = ks.get('drug', 'unknown')
    patient = ks.get('patient', 'unknown')
    log.info(f"\n{'='*60}")
    log.info(f"VISION LOOP: Item {item_index + 1} — {drug} for {patient}")
    log.info(f"{'='*60}")

    history = []
    step = 0
    workflow_steps = []   # (screenshot, action) pairs for workflow recording
    prev_screenshot = None

    while step < MAX_STEPS:
        step += 1
        log.info(f"\n--- Step {step}/{MAX_STEPS} ---")

        # Take screenshot
        if DRY_RUN and step > 1:
            log.info("  [DRY RUN] Stopping after first Claude call")
            return True

        screenshot_pil, screenshot_b64 = take_screenshot(save=True)
        log.info(f"  Screenshot taken")

        action = None
        tier_used = 3
        cache_id = None
        tokens_used = 0

        # ── Cache Lookup ──────────────────────────────────────────────────────
        if cache and cache.enabled:
            cached = cache.lookup(screenshot_pil)
            if cached:
                tier = cached['tier']
                candidate = cached['action']
                cache_id = cached['cache_id']

                if tier == 1:
                    # Instant: no API call
                    log.info(f"  🟢 TIER 1 (instant) — cached: {candidate.get('description', '')}")
                    action = candidate
                    tier_used = 1

                elif tier == 2:
                    # Verify with Haiku
                    log.info(f"  🟡 TIER 2 (verify) — checking with Haiku...")
                    verified = cache.verify_with_haiku(
                        client, screenshot_b64, candidate,
                        screen_context=cached.get('screen_context')
                    )
                    if verified:
                        log.info(f"  ✅ Haiku verified: {candidate.get('description', '')}")
                        action = candidate
                        tier_used = 2
                    else:
                        log.info(f"  ❌ Haiku rejected — falling back to Sonnet")
                        cache_id = None  # Don't update this cache entry on mismatch

        # ── Full Sonnet Call ──────────────────────────────────────────────────
        if action is None:
            tier_used = 3
            log.info(f"  🔵 TIER 3 (full Sonnet vision)...")
            try:
                action, raw, tokens_used = ask_claude(
                    client, screenshot_b64, script_data, item_index, step, history
                )
            except Exception as e:
                log.error(f"Claude API error: {e}")
                return False

        log.info(f"  Action [{tier_used}] → {action}")

        # Track stats
        if cache:
            cache.track_tier(tier_used, tokens_used)

        # Record in history
        desc = action.get('description', action.get('action', ''))
        history.append(f"Step {step}: {action.get('action')} — {desc}")

        # Execute
        try:
            should_continue = execute_action(action)
        except pyautogui.FailSafeException:
            log.error("FAILSAFE TRIGGERED — mouse moved to corner")
            return False
        except Exception as e:
            log.error(f"Action execution error: {e}")
            return False

        # ── Learning Feedback ─────────────────────────────────────────────────
        if cache and cache.enabled and action.get('action') not in ('done', 'error', 'wait'):
            # Take post-action screenshot to detect if screen changed
            time.sleep(ACTION_DELAY)
            post_pil, _ = take_screenshot(save=False)

            # Simple change detection: compare hashes
            pre_hash = cache._phash(screenshot_pil)
            post_hash = cache._phash(post_pil)
            screen_changed = (pre_hash != post_hash) if pre_hash and post_hash else True

            task_type = _infer_task_type(action, step)

            if screen_changed or action.get('action') in ('type', 'press'):
                # Consider successful if screen changed or it was a type/press (hard to verify otherwise)
                cache.record(
                    screenshot_pil, action, success=True,
                    task_type=task_type, cache_id=cache_id
                )
            else:
                # Screen didn't change — might have failed
                if tier_used in (1, 2):
                    log.warning(f"  ⚠️  Screen unchanged after cached action — recording failure")
                    cache.record(
                        screenshot_pil, action, success=False,
                        task_type=task_type, cache_id=cache_id
                    )
        else:
            time.sleep(ACTION_DELAY)

        # Record step for workflow
        workflow_steps.append({'screenshot': screenshot_pil, 'action': action})

        # Done or error?
        if action.get('action') == 'done':
            if cache:
                # Record successful workflow
                cache.record_workflow(ks, workflow_steps)
                cache.track_script_complete()
            return True
        if action.get('action') == 'error':
            return False
        if not should_continue:
            return False

        prev_screenshot = screenshot_pil

    log.error(f"MAX STEPS ({MAX_STEPS}) reached — aborting")
    return False

# --- Test Screenshot Mode ---

def test_screenshot_mode(client):
    """Take a screenshot, ask Claude to analyze what it sees."""
    log.info("TEST SCREENSHOT MODE")
    log.info("Taking screenshot...")
    _, b64 = take_screenshot(save=True)
    log.info("Sending to Claude for analysis...")

    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": b64},
                    },
                    {
                        "type": "text",
                        "text": (
                            "Please describe what you see on this screen. "
                            "Is Z Dispense open? What state is it in? "
                            "What fields or dialogs are visible? "
                            "Where would you click to start a new dispensing?"
                        ),
                    }
                ],
            }
        ],
    )
    print("\n" + "="*60)
    print("CLAUDE'S ANALYSIS:")
    print("="*60)
    print(response.content[0].text)
    print("="*60 + "\n")

# --- Supabase Polling ---

def poll_supabase(client, sb, run_once=False):
    """Poll Supabase for pending jobs and process them."""
    log.info(f"Polling Supabase for pending jobs (interval: {POLL_INTERVAL}s)...")
    log.info("SAFETY: Move mouse to any corner to abort current dispense.\n")

    while True:
        try:
            result = (
                sb.table('dispense_jobs')
                .select('*')
                .eq('status', 'pending')
                .order('created_at', desc=False)
                .limit(1)
                .execute()
            )

            if result.data:
                job = result.data[0]
                job_id = job['id']
                payload = job.get('payload', {})
                patient = payload.get('patient', {}).get('name', 'Unknown')
                # Build keystrokes from payload items
                keystrokes = []
                for item in payload.get('items', []):
                    keystrokes.append({
                        'patient': payload.get('patient', {}).get('name', '').split()[-1] if payload.get('patient', {}).get('name') else '',
                        'doctor': payload.get('doctor', {}).get('name', '').replace('Dr ', '').split()[-1] if payload.get('doctor', {}).get('name') else '',
                        'drug': item.get('drugName', ''),
                        'directions': item.get('directions', 'As directed'),
                        'quantity': item.get('quantity', ''),
                        'repeats': item.get('repeats', '0'),
                        'supplyType': 'N' if payload.get('scriptType') == 'PRIVATE' else 'P',
                        'scriptDate': payload.get('scriptDate', ''),
                        'strength': item.get('strength', ''),
                        'form': item.get('form', ''),
                    })
                job['keystrokes'] = keystrokes

                log.info(f"\n{'='*60}")
                log.info(f"JOB: {job_id[:8]}...")
                log.info(f"Patient: {patient}")
                log.info(f"Items: {len(keystrokes)}")
                log.info(f"{'='*60}")

                # Mark as dispensing
                sb.table('dispense_jobs').update({
                    'status': 'dispensing'
                }).eq('id', job_id).execute()

                # Focus Z Dispense
                if not DRY_RUN:
                    focus_zdispense()
                    log.info("Starting in 2 seconds...")
                    time.sleep(2)

                # Process each item
                all_ok = True
                for i, ks in enumerate(keystrokes):
                    log.info(f"\nDisensing item {i+1}/{len(keystrokes)}: {ks.get('drug', '?')}")
                    ok = dispense_with_vision(client, job, item_index=i)
                    if not ok:
                        log.error(f"Item {i+1} failed — stopping")
                        all_ok = False
                        break
                    if i < len(keystrokes) - 1:
                        log.info("Pausing before next item...")
                        time.sleep(1.5)

                # Update status
                final_status = 'completed' if all_ok else 'failed'
                update_data = {'status': final_status}
                if final_status == 'completed':
                    update_data['dispensed_at'] = datetime.utcnow().isoformat()
                sb.table('dispense_jobs').update(update_data).eq('id', job_id).execute()

                log.info(f"\n>> Job {job_id[:8]} → {final_status}")

                if run_once:
                    log.info("--once flag set, exiting.")
                    break

            else:
                log.debug("No pending jobs.")

        except KeyboardInterrupt:
            log.info("\nShutting down...")
            break
        except Exception as e:
            log.error(f"Poll error: {e}", exc_info=True)

        if run_once:
            break

        time.sleep(POLL_INTERVAL)

# --- Flask Health Endpoint ---

def start_flask():
    """Start a minimal Flask health API on port 9876."""
    try:
        from flask import Flask, jsonify
        from flask_cors import CORS
    except ImportError:
        log.warning("Flask not installed — health endpoint disabled")
        return

    app = Flask(__name__)
    CORS(app)

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            'status': 'ok',
            'agent': 'ScriptScanner Vision Agent',
            'version': '3.0.0',
            'model': MODEL,
            'dry_run': DRY_RUN,
        })

    try:
        app.run(host='127.0.0.1', port=PORT, debug=False, use_reloader=False)
    except Exception as e:
        log.warning(f"Flask failed to start: {e}")

# --- Main ---

def main():
    global DRY_RUN, LEARNING_ENABLED, POLL_INTERVAL, cache

    parser = argparse.ArgumentParser(description='ScriptScanner Vision Agent')
    parser.add_argument('--dry-run', action='store_true', help="Take screenshots and call Claude, but don't act")
    parser.add_argument('--once', action='store_true', help='Process one job and exit')
    parser.add_argument('--test-screenshot', action='store_true', help='Screenshot + Claude analysis only')
    parser.add_argument('--no-flask', action='store_true', help='Disable Flask health endpoint')
    parser.add_argument('--poll-interval', type=int, default=3, help='Supabase poll interval (seconds)')
    parser.add_argument('--stats', action='store_true', help='Show cache hit rate and cost savings, then exit')
    parser.add_argument('--clear-cache', action='store_true', help='Reset the learning database, then exit')
    parser.add_argument('--learning', choices=['on', 'off'], default='on',
                        help='Enable or disable learning cache (default: on)')
    args = parser.parse_args()

    DRY_RUN = args.dry_run
    LEARNING_ENABLED = (args.learning == 'on')
    POLL_INTERVAL = args.poll_interval

    print()
    print("=" * 60)
    print("   ScriptScanner Vision Agent v3.0")
    print(f"   Model: {MODEL}")
    print("=" * 60)
    if DRY_RUN:
        print("   ⚠️  DRY RUN — will NOT interact with Z Dispense")
    if not LEARNING_ENABLED:
        print("   ℹ️  Learning cache: OFF")
    print()

    # Init learning cache (before anything else so --stats / --clear-cache work)
    if LEARNING_CACHE_MODULE:
        cache = LearningCache(enabled=LEARNING_ENABLED)
    else:
        log.warning("learning_cache module not found — running without cache")
        cache = None

    # Handle --stats
    if args.stats:
        if cache:
            cache.print_stats()
        else:
            print("Learning cache module not available.")
        return

    # Handle --clear-cache
    if args.clear_cache:
        if cache:
            cache.clear()
            print("✅ Learning cache cleared.")
        else:
            print("Learning cache module not available.")
        return

    # Load config
    cfg = get_config()

    if not cfg['anthropic_key']:
        print("ERROR: ANTHROPIC_API_KEY not found in .env.local or environment")
        sys.exit(1)

    # Init Anthropic
    import anthropic
    client = anthropic.Anthropic(api_key=cfg['anthropic_key'])
    log.info(f"Anthropic client initialized (model: {MODEL})")

    # Test screenshot mode
    if args.test_screenshot:
        test_screenshot_mode(client)
        return

    # Need Supabase for everything else
    if not cfg['supabase_url'] or not cfg['supabase_key']:
        print("ERROR: Supabase URL/key not found in .env.local or environment")
        sys.exit(1)

    from supabase import create_client
    sb = create_client(cfg['supabase_url'], cfg['supabase_key'])
    log.info(f"Supabase connected: {cfg['supabase_url']}")

    # Start Flask health endpoint in background
    if not args.no_flask:
        flask_thread = Thread(target=start_flask, daemon=True)
        flask_thread.start()
        log.info(f"Health endpoint: http://localhost:{PORT}/health")

    # Make screenshots directory
    screenshots_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'screenshots')
    os.makedirs(screenshots_dir, exist_ok=True)
    log.info(f"Screenshots → {screenshots_dir}")

    learning_status = "on" if (cache and cache.enabled) else "off"
    print(f"""
  Config:
    Supabase: {cfg['supabase_url']}
    Model: {MODEL}
    Max steps per item: {MAX_STEPS}
    Poll interval: {POLL_INTERVAL}s
    Dry run: {DRY_RUN}
    Learning cache: {learning_status}

  SAFETY: Move mouse to any screen corner to abort!
""")

    poll_supabase(client, sb, run_once=args.once)


if __name__ == '__main__':
    main()
