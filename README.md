# ScriptScanner 💊📸

AI-powered pharmacy prescription scanner. Snap a photo → extract details → dispense to Z Dispense.

## Features

- **Phone camera capture** — Take a photo of any prescription
- **AI-powered OCR** — Claude Vision extracts patient, doctor, and medication details
- **Select & defer** — Choose which items to dispense now vs defer
- **Z Dispense integration** — Sends keystroke sequences to dispensary software (Phase 2)
- **PWA** — Installable on Android Chrome, works offline-first

## Quick Start

```bash
npm install
cp .env.local.example .env.local  # Add your Anthropic API key
npm run dev
```

Open `http://localhost:3000` on your phone (same network).

## Environment Variables

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Your Anthropic API key for Claude Vision |

## Deploy to Vercel

```bash
vercel --prod
```

Set `ANTHROPIC_API_KEY` in Vercel Environment Variables.

## Architecture

```
Phone (PWA)
  │
  ├── /api/scan     → Claude Vision API → Structured JSON
  │
  └── /api/dispense → Keystroke queue → WebSocket → Dispensary PC
                                                        │
                                                    Z Dispense
```

## Z Dispense Field Mapping

| Order | Field | Notes |
|-------|-------|-------|
| 1 | Patient | LastName FirstName → Enter → Select |
| 2 | Supply Type | N=PBS, P=Private, R=RPBS, etc. |
| 3 | Script Date | From prescription |
| 4 | Doctor | Name → Select |
| 5 | Drug | Search term → Select |
| 6 | Directions | Sig or 'S' for standard |
| 7 | Repeats | Number + 'D' to defer |
| 8 | Quantity | Usually pre-filled |
| 9 | Price | Auto-calculated |
| 10 | Pharmacist | Initials |
| 11 | F10 | Finish + print label |

## Roadmap

- [x] Phone capture + AI reading MVP
- [ ] Z Dispense keystroke injection via WebSocket
- [ ] eRx barcode scanning
- [ ] Patient history lookup
- [ ] Script queue management
