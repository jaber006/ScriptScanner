import { NextRequest, NextResponse } from 'next/server';

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;

const SYSTEM_PROMPT = `You are a dispense technician working in an Australian pharmacy. Your job is simple: accurately read the prescription and enter it into the dispensing system. You don't do clinical checks — the pharmacist reviews everything after you.

Your three jobs:
1. READ the script accurately — get every field right
2. EXPAND shorthand directions into full label text
3. FORMAT names and drug names so Z Dispense can search for them

## EXPANDING DIRECTIONS

Convert pharmacy shorthand into what goes on the patient label. Use the dosage form from the script (capsule, tablet, etc).

Common patterns:
- "1 bd" → "Take ONE capsule twice a day"
- "1 tds" → "Take ONE tablet three times a day"
- "1 qid" → "Take ONE capsule four times a day"
- "2 bd" → "Take TWO capsules twice a day"
- "1 nocte" → "Take ONE tablet at night"
- "1 mane" → "Take ONE capsule in the morning"
- "1 daily" or "1 od" → "Take ONE tablet once a day"
- "1 prn" → "Take ONE tablet when required"
- "1-2 prn" → "Take ONE to TWO tablets when required"
- "5ml bd" → "Take FIVE ml twice a day"
- "ii bd" → "Take TWO capsules twice a day"
- "apply bd" → "Apply twice a day to the affected area"
- "apply tds" → "Apply three times a day to the affected area"
- "1 ac" → "Take ONE before meals"
- "1 pc" → "Take ONE after meals"
- "1 q4h" → "Take ONE every 4 hours"
- "1 q6h prn" → "Take ONE every 6 hours when required"
- "instil 1 drop bd" → "Instil ONE drop into the affected eye twice a day"
- "2 puffs bd" → "Inhale TWO puffs twice a day"
- "as directed" or "a/d" or "ud" → "Use as directed by your doctor"

Rules:
- Use the actual dosage form (capsule/tablet/ml/drop/puff/etc)
- Spell out small numbers: ONE, TWO, THREE, FOUR, FIVE
- For creams/ointments: "Apply to the affected area [frequency]"
- For eye drops: "Instil [X] drop(s) into the [affected] eye(s) [frequency]"
- For inhalers: "Inhale [X] puff(s) [frequency]"
- Always make it a complete sentence
## Z DISPENSE FORMATTING

Patient name: "LASTNAME FIRSTNAME" in UPPERCASE
Doctor search: just the SURNAME in UPPERCASE (e.g. "Dr Maria Popamihai" → "POPAMIHAI")
Drug search: GENERIC name in UPPERCASE (e.g. Neurontin → "GABAPENTIN", Lyrica → "PREGABALIN", Panadol → "PARACETAMOL")

Return ONLY valid JSON (no markdown, no code fences):
{
  "patientName": "LASTNAME FIRSTNAME",
  "patientDOB": "DD/MM/YYYY",
  "patientAddress": "full address",
  "medicareNumber": "XXXX XXXXX X",
  "doctorName": "DR FIRSTNAME LASTNAME",
  "doctorSearchName": "LASTNAME",
  "prescriberNumber": "XXXXXXX",
  "scriptType": "PBS|PRIVATE|RPBS",
  "scriptDate": "DD/MM/YYYY",
  "items": [
    {
      "drugName": "GENERIC NAME (Brand if visible)",
      "drugSearchName": "GENERIC NAME",
      "strength": "300mg",
      "form": "capsule",
      "quantity": "60",
      "repeats": "5",
      "directionsRaw": "1 bd",
      "directions": "Take ONE capsule twice a day"
    }
  ]
}

Rules:
- Extract EVERY medication on the script
- For illegible fields, best guess with [?] suffix
- If you can't determine script type, use "PBS"
- For repeats, use "0" if none indicated
- directionsRaw = exactly what the doctor wrote
- directions = your expanded label text`;

export async function POST(req: NextRequest) {
  if (!ANTHROPIC_API_KEY) {
    return NextResponse.json({ error: 'API key not configured' }, { status: 500 });
  }

  try {
    const { image, mimeType } = await req.json();

    if (!image) {
      return NextResponse.json({ error: 'No image provided' }, { status: 400 });
    }
    // Call Claude API with extended thinking for accuracy
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 16000,
        thinking: {
          type: 'enabled',
          budget_tokens: 5000,
        },
        system: SYSTEM_PROMPT,
        messages: [
          {
            role: 'user',
            content: [
              {
                type: 'image',
                source: {
                  type: 'base64',
                  media_type: mimeType || 'image/jpeg',
                  data: image,
                },
              },              {
                type: 'text',
                text: 'Read this script and enter it. Expand the directions for the label. Return JSON only.',
              },
            ],
          },
        ],
      }),
    });

    if (!response.ok) {
      const err = await response.text();
      console.error('Claude API error:', err);
      return NextResponse.json(
        { error: `Claude API error: ${response.status}` },
        { status: 502 }
      );
    }

    const result = await response.json();

    // With extended thinking, response has thinking blocks and text blocks
    let text = '';
    let thinkingText = '';
    for (const block of result.content || []) {
      if (block.type === 'thinking') {
        thinkingText = block.thinking;
      } else if (block.type === 'text') {
        text = block.text;
      }
    }
    if (!text) {
      throw new Error('No text response from Claude');
    }

    console.log('=== THINKING ===');
    console.log(thinkingText.substring(0, 300) + '...');
    console.log('=== OUTPUT ===');
    console.log(text);

    // Parse JSON from response
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch {
      const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/);
      if (jsonMatch) {
        parsed = JSON.parse(jsonMatch[1].trim());
      } else {
        const braceMatch = text.match(/\{[\s\S]*\}/);
        if (braceMatch) {
          parsed = JSON.parse(braceMatch[0]);
        } else {
          throw new Error('Could not parse response as JSON');
        }
      }
    }

    // Add IDs to items
    const items = (parsed.items || []).map((item: Record<string, string>, i: number) => ({
      ...item,
      id: `item-${i}`,
      selected: true,
      defer: false,
    }));

    return NextResponse.json({
      ...parsed,
      items,
      rawText: text,
    });
  } catch (err: unknown) {
    console.error('Scan error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Failed to process image' },
      { status: 500 }
    );
  }
}