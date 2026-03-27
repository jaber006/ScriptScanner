import { NextRequest, NextResponse } from 'next/server';
import { supabase } from '@/app/lib/supabase';

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY;
const PHARMACY_ID = process.env.PHARMACY_ID || 'legana-dds';

const SYSTEM_PROMPT = `You are a pharmacy prescription OCR system. Extract ALL information from the prescription image.

Return ONLY valid JSON (no markdown, no code fences) in this exact format:
{
  "patientName": "LASTNAME FIRSTNAME",
  "patientDOB": "DD/MM/YYYY",
  "patientAddress": "full address",
  "medicareNumber": "XXXX XXXXX X",
  "doctorName": "Dr FIRSTNAME LASTNAME",
  "prescriberNumber": "XXXXXXX",
  "scriptType": "PBS|PRIVATE|RPBS|DENTAL|OPTOMETRICAL|NURSE|MIDWIFE|EMERGENCY|S3R",
  "scriptDate": "DD/MM/YYYY",
  "items": [
    {
      "drugName": "drug name (generic preferred, include brand if visible)",
      "strength": "e.g. 500mg",
      "form": "e.g. capsule, tablet, cream, solution",
      "quantity": "number",
      "repeats": "number",
      "directions": "full directions/sig"
    }
  ]
}

Rules:
- Extract EVERY medication on the script, even if partially illegible
- For illegible fields, put your best guess with [?] suffix e.g. "amoxicillin [?]"
- Patient name format: LASTNAME FIRSTNAME (uppercase)
- If Medicare number is partially visible, include what you can read
- Script type: determine from the form layout, checkboxes, or markings
- Always return at least one item in the items array
- If directions say "as directed" or similar, include that
- Include authority prescription numbers if visible
- For repeats, return "0" if no repeats indicated`;

export async function POST(req: NextRequest) {
  if (!ANTHROPIC_API_KEY) {
    return NextResponse.json({ error: 'API key not configured' }, { status: 500 });
  }

  try {
    const { image, mimeType } = await req.json();

    if (!image) {
      return NextResponse.json({ error: 'No image provided' }, { status: 400 });
    }

    // Call Claude Vision API
    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 2000,
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
              },
              {
                type: 'text',
                text: 'Extract all prescription details from this image. Return JSON only.',
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
    const text = result.content?.[0]?.text || '';

    // Parse JSON from response (handle potential markdown wrapping)
    let parsed;
    try {
      // Try direct parse first
      parsed = JSON.parse(text);
    } catch {
      // Try extracting from code fences
      const jsonMatch = text.match(/```(?:json)?\s*([\s\S]*?)```/);
      if (jsonMatch) {
        parsed = JSON.parse(jsonMatch[1].trim());
      } else {
        // Try finding JSON object in text
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
      selected: true, // Default: all selected for dispense
      defer: false,
    }));

    // Save to scan history (non-blocking)
    supabase
      .from('scan_history')
      .insert({
        pharmacy_id: PHARMACY_ID,
        extracted_data: { ...parsed, items },
        raw_ai_response: text,
      })
      .then(({ error: histError }) => {
        if (histError) console.error('Failed to save scan history:', histError);
      });

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
