"""
ScriptScanner Dispensary Agent v2.0
====================================
Runs on the dispensary PC. Polls Supabase for pending scripts and
types them into Z Dispense using keystroke automation.

The AI (Claude) has already:
  - Read the prescription image
  - THOUGHT about each drug and its directions
  - Expanded abbreviations (e.g. "1 bd" -> "Take ONE capsule twice a day")
  - Formatted names for Z Dispense search
  - Built the keystroke sequence

This agent just types what Claude prepared into Z Dispense.

Requirements:
    pip install pyautogui pyperclip flask flask-cors pygetwindow supabase

Usage:
    python agent.py                    # Interactive mode with Supabase polling
    python agent.py --dry-run          # Don't type, just print what would happen
    python agent.py --poll-interval 5  # Check every 5 seconds (default: 3)
"""

import os
import sys
import time
import json
import logging
import argparse
from threading import Lock, Thread
import pyautogui
import pyperclip

# --- Config ---
PORT = 9876
TYPING_DELAY = 0.05
FIELD_DELAY = 0.4
TAB_DELAY = 0.25
SEARCH_DELAY = 0.8
SELECTION_DELAY = 0.6
Z_DISPENSE_TITLE = "Z Dispense"

pyautogui.FAILSAFE = True
pyautogui.PAUSE = TYPING_DELAY

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('agent')

PHARMACIST_INITIALS = ""
DRY_RUN = False
dispense_lock = Lock()


# --- Helpers ---

def focus_zdispense():
    try:
        import pygetwindow as gw
        windows = gw.getWindowsWithTitle(Z_DISPENSE_TITLE)
        if windows:
            win = windows[0]
            if win.isMinimized:
                win.restore()
            win.activate()
            time.sleep(0.5)
            log.info(f"  Focused window: {win.title}")
            return True
        else:
            log.warning(f"  Window '{Z_DISPENSE_TITLE}' not found")
            return False
    except ImportError:
        log.warning("  pygetwindow not installed")
        return False
    except Exception as e:
        log.warning(f"  Could not focus Z Dispense: {e}")
        return False

def type_text(text, field_name=""):
    if not text:
        log.info(f"    [{field_name}] (empty)")
        return
    if DRY_RUN:
        log.info(f"    [DRY] {field_name} = '{text}'")
        return
    pyperclip.copy(str(text))
    pyautogui.hotkey('ctrl', 'v')
    time.sleep(0.1)
    log.info(f"    {field_name} = '{text}'")

def press_key(key, delay=0.25, label=""):
    if DRY_RUN:
        log.info(f"    [DRY] Press {key} {label}")
        return
    pyautogui.press(key)
    time.sleep(delay)
    if label:
        log.info(f"    Press {key} {label}")

def clear_field():
    if DRY_RUN:
        return
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.05)
    pyautogui.press('delete')
    time.sleep(0.05)

# --- Z Dispense Field Sequence ---

def dispense_item(ks, is_first_item=True):
    drug_name = ks.get('drug', 'unknown')
    patient_name = ks.get('patient', 'unknown')
    log.info(f"\n  === Item: {drug_name} for {patient_name} ===")

    # 1. PATIENT
    if is_first_item:
        log.info(f"  Field 1: Patient (search)")
        clear_field()
        type_text(ks.get('patient', ''), 'Patient')
        press_key('enter', SEARCH_DELAY, '-> search patients')
        press_key('enter', SELECTION_DELAY, '-> select patient')
    else:
        log.info(f"  Field 1: Patient (same = S)")
        clear_field()
        type_text('S', 'Same Patient')
        press_key('enter', SELECTION_DELAY, '-> same patient')

    # 2. SUPPLY TYPE
    log.info(f"  Field 2: Supply Type")
    time.sleep(FIELD_DELAY)
    clear_field()
    type_text(ks.get('supplyType', 'N'), 'Supply Type')
    press_key('tab', TAB_DELAY, '-> next')

    # 3. SCRIPT DATE
    log.info(f"  Field 3: Script Date")
    clear_field()
    type_text(ks.get('scriptDate', ''), 'Script Date')
    press_key('tab', TAB_DELAY, '-> next')
    # 4. DOCTOR
    log.info(f"  Field 4: Doctor (search)")
    clear_field()
    type_text(ks.get('doctor', ''), 'Doctor')
    press_key('enter', SEARCH_DELAY, '-> search doctors')
    press_key('enter', SELECTION_DELAY, '-> select doctor')

    # 5. DRUG
    log.info(f"  Field 5: Drug (search)")
    clear_field()
    type_text(ks.get('drug', ''), 'Drug')
    press_key('enter', SEARCH_DELAY, '-> search drugs')
    press_key('enter', SELECTION_DELAY, '-> select drug')

    # 6. DIRECTIONS
    log.info(f"  Field 6: Directions")
    time.sleep(FIELD_DELAY)
    clear_field()
    type_text(ks.get('directions', 'As directed'), 'Directions')
    press_key('tab', TAB_DELAY, '-> next')

    # 7. REPEATS
    log.info(f"  Field 7: Repeats")
    clear_field()
    type_text(ks.get('repeats', '0'), 'Repeats')
    press_key('tab', TAB_DELAY, '-> next')

    # 8. QUANTITY
    log.info(f"  Field 8: Quantity")
    clear_field()
    type_text(ks.get('quantity', ''), 'Quantity')
    press_key('tab', TAB_DELAY, '-> next')
    # 9. PRICE - skip
    log.info(f"  Field 9: Price (skip)")
    press_key('tab', TAB_DELAY, '-> skip price')

    # 10. PHARMACIST INITIALS
    if PHARMACIST_INITIALS:
        log.info(f"  Field 10: Pharmacist Initials")
        clear_field()
        type_text(PHARMACIST_INITIALS, 'Initials')
        time.sleep(FIELD_DELAY)
        # 11. F10 - FINISH
        log.info(f"  Field 11: F10 -> Print Label")
        press_key('f10', SELECTION_DELAY, '-> FINISH & PRINT')
        log.info(f"  === DONE - Label printed ===\n")
    else:
        log.info(f"  === STOPPED - No initials set ===\n")

# --- Supabase Polling ---

def start_supabase_poller(url, key, poll_interval=3, store_id='default'):
    try:
        from supabase import create_client
    except ImportError:
        log.error("supabase package not installed. Run: pip install supabase")
        sys.exit(1)

    sb = create_client(url, key)
    log.info(f"Connected to Supabase")
    log.info(f"Poll interval: {poll_interval}s")
    log.info(f"Waiting for scripts...\n")

    while True:
        try:
            result = sb.table('script_queue') \
                .select('*') \
                .eq('status', 'pending') \
                .order('created_at', desc=False) \
                .limit(1) \
                .execute()

            if result.data and len(result.data) > 0:
                job = result.data[0]
                job_id = job['id']
                patient = job.get('patient_name', 'Unknown')
                keystrokes = job.get('keystrokes', [])
                log.info(f"{'='*50}")
                log.info(f"Found pending job: {job_id[:8]}...")
                log.info(f"Patient: {patient}")
                log.info(f"Items: {len(keystrokes)}")
                log.info(f"{'='*50}")

                sb.table('script_queue') \
                    .update({'status': 'dispensing', 'dispensed_by': PHARMACIST_INITIALS}) \
                    .eq('id', job_id) \
                    .execute()

                for i, ks in enumerate(keystrokes):
                    log.info(f"\n  Preview item {i+1}:")
                    log.info(f"    Patient:    {ks.get('patient', '?')}")
                    log.info(f"    Type:       {ks.get('supplyType', '?')}")
                    log.info(f"    Date:       {ks.get('scriptDate', '?')}")
                    log.info(f"    Doctor:     {ks.get('doctor', '?')}")
                    log.info(f"    Drug:       {ks.get('drug', '?')}")
                    log.info(f"    Directions: {ks.get('directions', '?')}")
                    log.info(f"    Repeats:    {ks.get('repeats', '?')}")
                    log.info(f"    Quantity:   {ks.get('quantity', '?')}")
                if not DRY_RUN:
                    focus_zdispense()
                    log.info("\nStarting in 2 seconds...")
                    time.sleep(2)

                try:
                    for i, ks in enumerate(keystrokes):
                        log.info(f"\n  Dispensing item {i+1}/{len(keystrokes)}")
                        dispense_item(ks, is_first_item=(i == 0))
                        if i < len(keystrokes) - 1:
                            time.sleep(SELECTION_DELAY)

                    sb.table('script_queue') \
                        .update({'status': 'completed', 'dispensed_at': 'now()'}) \
                        .eq('id', job_id) \
                        .execute()
                    log.info(f"\n>> All {len(keystrokes)} items dispensed successfully")

                except pyautogui.FailSafeException:
                    log.error("FAILSAFE TRIGGERED - mouse moved to corner")
                    sb.table('script_queue') \
                        .update({'status': 'failed', 'error_message': 'Failsafe - aborted'}) \
                        .eq('id', job_id) \
                        .execute()
                except Exception as e:
                    log.error(f"Dispense error: {e}")
                    sb.table('script_queue') \
                        .update({'status': 'failed', 'error_message': str(e)}) \
                        .eq('id', job_id) \
                        .execute()
        except KeyboardInterrupt:
            log.info("\nShutting down...")
            break
        except Exception as e:
            log.error(f"Poll error: {e}")

        time.sleep(poll_interval)


# --- Flask API (for dashboard) ---

def start_flask():
    from flask import Flask, request, jsonify
    from flask_cors import CORS

    app = Flask(__name__)
    CORS(app)

    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            'status': 'ok',
            'agent': 'ScriptScanner Dispensary Agent',
            'version': '2.0.0',
            'initials': PHARMACIST_INITIALS or '(not set)',
            'dry_run': DRY_RUN,
        })
    @app.route('/config', methods=['POST'])
    def config():
        global PHARMACIST_INITIALS
        data = request.json
        if 'initials' in data:
            PHARMACIST_INITIALS = data['initials'].strip().upper()
            log.info(f"Pharmacist initials set to: {PHARMACIST_INITIALS}")
        return jsonify({'success': True, 'initials': PHARMACIST_INITIALS})

    @app.route('/dispense', methods=['POST'])
    def dispense():
        if not dispense_lock.acquire(blocking=False):
            return jsonify({'error': 'Already dispensing'}), 409
        try:
            data = request.json
            keystrokes = data.get('keystrokes', [])
            patient = data.get('patientName', 'Unknown')
            if not keystrokes:
                return jsonify({'error': 'No keystrokes provided'}), 400
            log.info(f"=== DISPENSE (via dashboard) === Patient: {patient}")
            if not DRY_RUN:
                focus_zdispense()
                time.sleep(2)
            for i, ks in enumerate(keystrokes):
                dispense_item(ks, is_first_item=(i == 0))
                if i < len(keystrokes) - 1:
                    time.sleep(SELECTION_DELAY)
            return jsonify({'success': True, 'message': f'Entered {len(keystrokes)} items'})
        except pyautogui.FailSafeException:
            return jsonify({'error': 'Failsafe triggered'}), 500
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        finally:
            dispense_lock.release()
    @app.route('/test', methods=['POST'])
    def test():
        log.info("Test keystroke injection...")
        time.sleep(2)
        type_text("ScriptScanner test - it works!", "Test")
        return jsonify({'success': True})

    app.run(host='127.0.0.1', port=PORT, debug=False)


# --- Main ---

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='ScriptScanner Dispensary Agent')
    parser.add_argument('--dry-run', action='store_true', help="Don't type, just log")
    parser.add_argument('--poll-interval', type=int, default=3, help='Poll interval seconds')
    parser.add_argument('--store-id', default='default', help='Store ID')
    parser.add_argument('--no-flask', action='store_true', help='Disable Flask API')
    args = parser.parse_args()

    DRY_RUN = args.dry_run

    print()
    print("==================================================")
    print("   ScriptScanner Dispensary Agent v2.0")
    print("   AI-powered prescription dispensing")
    print("==================================================")
    print()
    if DRY_RUN:
        print("  WARNING: DRY RUN MODE - will NOT type into Z Dispense")
        print()

    initials = input("Enter your pharmacist initials (e.g. MJ): ").strip().upper()
    if initials:
        PHARMACIST_INITIALS = initials
        print(f"  Initials set to: {PHARMACIST_INITIALS}")
    else:
        print("  No initials - will stop before initials field")

    # Load Supabase config
    supabase_url = os.environ.get('SUPABASE_URL') or os.environ.get('NEXT_PUBLIC_SUPABASE_URL', '')
    supabase_key = os.environ.get('SUPABASE_KEY') or os.environ.get('NEXT_PUBLIC_SUPABASE_ANON_KEY', '')

    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env.local')
    if os.path.exists(env_file) and (not supabase_url or not supabase_key):
        log.info(f"Loading config from {env_file}")
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if '=' in line and not line.startswith('#'):
                    k, v = line.split('=', 1)
                    k, v = k.strip(), v.strip()
                    if k == 'NEXT_PUBLIC_SUPABASE_URL' and not supabase_url:
                        supabase_url = v
                    elif k == 'NEXT_PUBLIC_SUPABASE_ANON_KEY' and not supabase_key:
                        supabase_key = v
    if not supabase_url or not supabase_key:
        print("\n  ERROR: Supabase credentials not found!")
        print("  Set SUPABASE_URL and SUPABASE_KEY env vars,")
        print("  or create a .env.local file in the project root.")
        sys.exit(1)

    print(f"""
  Pharmacy: {args.store_id}
  Pharmacist: {PHARMACIST_INITIALS or '???'}
  Dry run: {DRY_RUN}
  Poll interval: {args.poll_interval}s

  SAFETY: Move mouse to any corner to abort.

  The AI has already:
    - Read the prescription
    - Expanded directions (e.g. "1 bd" -> "Take ONE capsule twice a day")
    - Formatted names for Z Dispense search
    - Built the keystroke sequence

  This agent just types what Claude prepared.
  You review the label AFTER dispensing.
""")

    if not args.no_flask:
        flask_thread = Thread(target=start_flask, daemon=True)
        flask_thread.start()
        log.info(f"Flask API running on http://localhost:{PORT}")

    start_supabase_poller(
        url=supabase_url,
        key=supabase_key,
        poll_interval=args.poll_interval,
        store_id=args.store_id,
    )