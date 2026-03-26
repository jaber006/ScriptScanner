import { NextRequest, NextResponse } from 'next/server';

/**
 * Dispense API — receives selected items from the phone app.
 * 
 * Phase 1 (MVP): Just logs and returns success.
 * Phase 2: Will forward to a WebSocket server running on the dispensary PC
 *          that injects keystrokes into Z Dispense.
 * 
 * Z Dispense field order (Barney layout):
 * 1. Patient — "LastName FirstName" → Enter → Select
 * 2. Supply Type — N(PBS), P(Private), R(RPBS), etc.
 * 3. Script Date
 * 4. Doctor — name → Select
 * 5. Drug — search term → Select
 * 6. Directions — sig text (or 'S' for standard)
 * 7. Repeats — number (add 'D' to defer)
 * 8. Quantity
 * 9. Price — skip (auto)
 * 10. Pharmacist Initials
 * 11. F10 — Finish
 */

// Script type mapping to Z Dispense codes
const SCRIPT_TYPE_MAP: Record<string, string> = {
  'PBS': 'N',
  'GENERAL': 'N',
  'PRIVATE': 'P',
  'RPBS': 'R',
  'REPAT': 'R',
  'DVA': 'R',
  'DENTAL': 'D',
  'OPTOMETRICAL': 'E',
  'NURSE': 'U',
  'MIDWIFE': 'F',
  'EMERGENCY': 'B',
  'CONTINUED': 'C',
  'S3R': 'T',
  'NON-PBS': 'S',
};

interface DispenseItem {
  drugName: string;
  strength: string;
  form: string;
  quantity: string;
  repeats: string;
  directions: string;
  defer: boolean;
}

interface DispensePayload {
  patient: {
    name: string;
    dob: string;
    address: string;
    medicare: string;
  };
  doctor: {
    name: string;
    prescriberNumber: string;
  };
  scriptType: string;
  scriptDate: string;
  items: DispenseItem[];
  deferredItems: DispenseItem[];
}

export async function POST(req: NextRequest) {
  try {
    const payload: DispensePayload = await req.json();

    // Build Z Dispense keystroke sequence for each item
    const typeCode = SCRIPT_TYPE_MAP[payload.scriptType?.toUpperCase()] || 'N';
    
    const keystrokes = [];

    for (const item of [...payload.items, ...payload.deferredItems]) {
      // Build drug search term: "name form strength"
      const drugSearch = [item.drugName, item.form, item.strength]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      const repeatsField = item.defer 
        ? `${item.repeats || '0'}D` 
        : (item.repeats || '0');

      keystrokes.push({
        patient: payload.patient.name,
        supplyType: typeCode + (item.defer ? '' : ''),  // Add 'O' for owing, 'A' for authority
        scriptDate: payload.scriptDate,
        doctor: payload.doctor.name,
        drug: drugSearch,
        directions: item.directions || 'S',
        repeats: repeatsField,
        quantity: item.quantity,
        defer: item.defer,
      });
    }

    // Phase 1: Log the keystroke sequence
    console.log('=== DISPENSE REQUEST ===');
    console.log('Patient:', payload.patient.name);
    console.log('Doctor:', payload.doctor.name);
    console.log('Script Type:', payload.scriptType, '→ Z Code:', typeCode);
    console.log('Items:', keystrokes.length);
    keystrokes.forEach((ks, i) => {
      console.log(`  Item ${i + 1}:`, ks.drug, ks.defer ? '(DEFER)' : '');
    });

    // Phase 2: TODO — Send to WebSocket server on dispensary PC
    // ws.send(JSON.stringify({ action: 'dispense', keystrokes }));

    return NextResponse.json({
      success: true,
      message: `Queued ${payload.items.length} items for dispensing, ${payload.deferredItems.length} deferred`,
      keystrokes, // Return for debugging
    });
  } catch (err: unknown) {
    console.error('Dispense error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Failed to process dispense request' },
      { status: 500 }
    );
  }
}
