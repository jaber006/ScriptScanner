# ScriptScanner Vision Agent

AI-powered prescription dispensing using Claude Vision API.

Instead of hardcoded keystrokes, this agent **sees the screen** and decides what to do — just like a human operator would.

## How It Works

```
Supabase (pending job)
    ↓
Take screenshot
    ↓
Send to Claude Vision + prescription data
    ↓
Claude responds: { "action": "click", "x": 450, "y": 220, "description": "Click New Dispense" }
    ↓
Execute action (pyautogui)
    ↓
Wait 0.3s → screenshot again
    ↓
Repeat until Claude says "done" or "error"
    ↓
Update Supabase: completed / failed
```

## Setup

### 1. Install Python 3.12

Download from https://www.python.org/downloads/

### 2. Install dependencies

```bash
cd agent
pip install -r requirements.txt
```

### 3. Configure environment

The agent reads `.env.local` from the project root (one level up from `agent/`):

```
ANTHROPIC_API_KEY=sk-ant-...
NEXT_PUBLIC_SUPABASE_URL=https://xxx.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

### 4. Ensure Z Dispense is open

The agent needs Z Dispense to be open on screen before processing starts. It will try to bring the window to focus automatically.

## Usage

```bash
# Poll Supabase and dispense when jobs arrive
python vision_agent.py

# Dry run — see screenshots + Claude responses, but don't touch Z Dispense
python vision_agent.py --dry-run

# Process one job and exit
python vision_agent.py --once

# Just take a screenshot and see what Claude says about the current screen
python vision_agent.py --test-screenshot

# Custom poll interval (seconds)
python vision_agent.py --poll-interval 5

# Disable Flask health endpoint
python vision_agent.py --no-flask
```

## Safety

- **Failsafe**: Move your mouse to any corner of the screen to immediately abort
- `pyautogui.FAILSAFE = True` is always on
- Screenshots are saved to `agent/screenshots/` with timestamps for review
- All actions are logged with descriptions

## Health Check

```
GET http://localhost:9876/health
```

Returns:
```json
{
  "status": "ok",
  "agent": "ScriptScanner Vision Agent",
  "version": "3.0.0",
  "model": "claude-sonnet-4-20250514",
  "dry_run": false
}
```

## How Claude Sees Z Dispense

Claude receives:
1. A screenshot (resized to max 1280px wide)
2. The full prescription data (patient, doctor, drug, directions, quantity, repeats, supply type)
3. History of the last 5 actions taken

Claude responds with ONE JSON action per turn:

| Action | Example |
|--------|---------|
| `click` | `{"action": "click", "x": 120, "y": 300, "description": "Click Patient field"}` |
| `type` | `{"action": "type", "text": "DARE", "description": "Type patient surname"}` |
| `press` | `{"action": "press", "key": "enter", "description": "Confirm patient search"}` |
| `wait` | `{"action": "wait", "seconds": 1, "description": "Wait for dialog"}` |
| `done` | `{"action": "done", "status": "completed", "description": "Label printed"}` |
| `error` | `{"action": "error", "message": "Z Dispense not visible", "description": "..."}` |

## Supabase Schema

Expected `script_queue` table columns:

| Column | Type | Notes |
|--------|------|-------|
| `id` | uuid | Primary key |
| `status` | text | pending → dispensing → completed/failed |
| `patient_name` | text | Full name e.g. "CHARMAIN DARE" |
| `patient_dob` | text | DOB e.g. "03/02/1953" |
| `doctor_name` | text | e.g. "Dr Lioudmila Ioussov" |
| `script_date` | text | e.g. "26/03/2026" |
| `keystrokes` | jsonb | Array of items to dispense |
| `dispensed_at` | timestamp | Set on completion |
| `error_message` | text | Set on failure |

### Keystroke item format:
```json
{
  "patient": "DARE",
  "doctor": "IOUSSOV",
  "drug": "Cerumol Ear Drops",
  "directions": "Instil 5 drops into affected ear(s) at night for 7 days",
  "quantity": "1",
  "repeats": "0",
  "supplyType": "N",
  "scriptDate": "26/03/2026"
}
```

---

## Learning Cache

The agent includes a perceptual-hash-based learning system that makes dispensing faster and cheaper over time.

### How It Works

```
First time on a new screen:
  Take screenshot → pHash → no match → full Sonnet call → record result

After 2+ successes:
  Take screenshot → pHash → match found → verify with cheap Haiku → execute

After 5+ successes (zero failures):
  Take screenshot → pHash → instant match → execute immediately (no API call)
```

### Confidence Tiers

| Tier | Condition | API Call |
|------|-----------|----------|
| 🟢 **Tier 1 (instant)** | `success_count >= 5` and `fail_count == 0` | None |
| 🟡 **Tier 2 (verified)** | `success_count >= 2` | Haiku (cheap, text-only) |
| 🔵 **Tier 3 (full)** | Unknown screen | Sonnet with vision |

### Cost Savings Example

After dispensing ~20 prescriptions with a similar workflow:
- Most screens will be Tier 1 or 2
- Expected cache hit rate: 70–90%
- Cost per script drops from ~$0.05 to ~$0.005

### Cache Commands

```bash
# Show cache hit rate and estimated cost savings
python vision_agent.py --stats

# Reset the learning database (start fresh)
python vision_agent.py --clear-cache

# Disable caching for this session (always use full Sonnet vision)
python vision_agent.py --learning off
```

### Sample --stats Output

```
=======================================================
  📊 ScriptScanner Learning Cache Stats
=======================================================
  Cached screen states:   24
  Recorded workflows:     8
  Scripts dispensed:      47

  Total actions:          423
    Tier 1 (instant):     198
    Tier 2 (verified):    142
    Tier 3 (full):         83
  Cache hit rate:         80.4%

  Tokens (Sonnet):        149,400
  Tokens (Haiku):          28,400
  Estimated cost:         $0.4712
  Estimated saved:        $1.8860
  Cost per script:        $0.0100
=======================================================
```

### Files

| File | Purpose |
|------|---------|
| `agent/learning_cache.py` | `LearningCache` class — all cache logic |
| `agent/cache.db` | SQLite database (auto-created, WAL mode) |

### Workflow Recording

After a complete successful dispense, the agent records the entire step sequence. Next time a prescription with the same drug prefix + supply type pattern arrives, it attempts to replay the workflow. If any step fails, it falls back to full vision.

### Resetting After Z Dispense Updates

If the pharmacy software is updated and the UI changes, cached screen hashes may no longer match. Run `--clear-cache` to rebuild from scratch.

---

## Troubleshooting

**"Z Dispense not found"** — Make sure Z Dispense is open and the window title contains "Z Dispense".

**"JSON parse error"** — Claude returned an unexpected response. Check the logs for the raw response. Usually a transient API issue.

**"MAX STEPS reached"** — The agent took 50 steps without finishing. Check screenshots to see where it got stuck.

**Paste not working** — Some systems need `xdotool` or alternative paste methods. On Windows, `pyperclip` + `ctrl+v` should work. Ensure the target field accepts paste.

**Wrong patient/doctor selected** — In future, you can add more explicit matching logic. Currently Claude selects the first search result.
