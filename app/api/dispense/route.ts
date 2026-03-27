import { NextRequest, NextResponse } from 'next/server';
import { createClient } from '@supabase/supabase-js';

/**
 * Dispense API — receives selected items from the phone app,
 * builds keystroke sequences for Z Dispense, and writes to Supabase
 * for the PC agent to pick up.
 *
 * Z Dispense BARNEY layout field order:
 * 1. Patient name → Enter (search) → Enter (select first match)
 * 2. Tab → Supply Type code (N/P/R/etc.)
 * 3. Tab → Script Date DD/MM/YYYY
 * 4. Tab → Doctor surname → Enter (search) → Enter (select)
 * 5. Tab → Drug search → Enter (search) → Enter (select)
 * 6. Tab → Directions (full expanded text)
 * 7. Tab → Repeats number
 * 8. Tab → Quantity number
 * 9. Tab → Price (skip, auto-calculated)
 * 10. Tab → Pharmacist Initials
 * 11. F10 → Finish & print label
 */

// Script type mapping to Z Dispense codes
const SCRIPT_TYPE_MAP: Record<string, string> = {
  'PBS': 'N',
  'GENERAL': 'N',
  'PRIVATE': 'P',
  'RPBS': 'R',
  'REPAT': 'R',  'DVA': 'R',
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
  drugSearchName?: string;
  strength: string;
  form: string;
  quantity: string;
  repeats: string;
  directions: string;
  directionsRaw?: string;
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
    searchName?: string;
    prescriberNumber: string;
  };
  scriptType: string;
  scriptDate: string;
  items: DispenseItem[];
  deferredItems: DispenseItem[];
}
export async function POST(req: NextRequest) {
  const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_SERVICE_ROLE_KEY || process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (!supabaseUrl || !supabaseKey) {
    return NextResponse.json({ error: 'Supabase not configured' }, { status: 500 });
  }

  const supabase = createClient(supabaseUrl, supabaseKey);

  try {
    const payload: DispensePayload = await req.json();

    // Build Z Dispense keystroke sequence for each item
    const typeCode = SCRIPT_TYPE_MAP[payload.scriptType?.toUpperCase()] || 'N';
    const allItems = [...payload.items, ...payload.deferredItems];

    // Doctor search name: use searchName field or extract surname
    const doctorSearch = payload.doctor.searchName
      || payload.doctor.name.replace(/^(Dr\.?\s*)/i, '').split(' ').pop()?.toUpperCase()
      || payload.doctor.name;

    const keystrokes = allItems.map((item) => {
      const drugSearch = item.drugSearchName
        || item.drugName.replace(/\s*\(.*\)/, '').toUpperCase();

      const repeatsField = item.defer
        ? `${item.repeats || '0'}D`
        : (item.repeats || '0');
      return {
        patient: payload.patient.name,
        supplyType: typeCode,
        scriptDate: payload.scriptDate,
        doctor: doctorSearch,
        drug: drugSearch,
        directions: item.directions || 'As directed by your doctor',
        repeats: repeatsField,
        quantity: item.quantity || '',
        defer: item.defer,
      };
    });

    // Write to Supabase script_queue
    const { data, error } = await supabase
      .from('script_queue')
      .insert({
        patient_name: payload.patient.name,
        patient_dob: payload.patient.dob || null,
        patient_address: payload.patient.address || null,
        medicare_number: payload.patient.medicare || null,
        doctor_name: payload.doctor.name,
        prescriber_number: payload.doctor.prescriberNumber || null,
        script_type: typeCode,
        script_date: payload.scriptDate || null,
        items: allItems,
        keystrokes: keystrokes,
        status: 'pending',
      })
      .select()
      .single();
    if (error) {
      console.error('Supabase insert error:', error);
      return NextResponse.json({ error: `Database error: ${error.message}` }, { status: 500 });
    }

    console.log('=== SCRIPT QUEUED ===');
    console.log('ID:', data.id);
    console.log('Patient:', payload.patient.name);
    console.log('Doctor search:', doctorSearch);
    console.log('Items:', allItems.length);
    allItems.forEach((item, i) => {
      console.log(`  Item ${i + 1}: ${item.drugSearchName || item.drugName} — "${item.directions}"`);
    });

    return NextResponse.json({
      success: true,
      message: `Queued ${payload.items.length} items for dispensing, ${payload.deferredItems.length} deferred`,
      queueId: data.id,
    });
  } catch (err: unknown) {
    console.error('Dispense error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Failed to process dispense request' },
      { status: 500 }
    );
  }
}