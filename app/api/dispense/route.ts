import { NextRequest, NextResponse } from 'next/server';
import { supabase } from '@/app/lib/supabase';

/**
 * Dispense API — receives selected items from the phone app.
 * Writes a dispense job to Supabase. The dispensary PC agent polls
 * for pending jobs and injects keystrokes into Z Dispense.
 *
 * Flow: Phone → Vercel API → Supabase → Dispensary Agent → Z Dispense
 */

const PHARMACY_ID = process.env.PHARMACY_ID || 'legana-dds';

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

    const selected = payload.items || [];
    const deferred = payload.deferredItems || [];

    if (selected.length === 0 && deferred.length === 0) {
      return NextResponse.json(
        { error: 'No items to dispense' },
        { status: 400 }
      );
    }

    // Build Z Dispense keystroke preview
    const typeCode = SCRIPT_TYPE_MAP[payload.scriptType?.toUpperCase()] || 'N';

    const keystrokes = [...selected, ...deferred].map((item) => {
      const drugSearch = [item.drugName, item.form, item.strength]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();

      return {
        patient: payload.patient.name,
        supplyType: typeCode,
        scriptDate: payload.scriptDate,
        doctor: payload.doctor.name,
        drug: drugSearch,
        directions: item.directions || 'S',
        repeats: item.defer ? `${item.repeats || '0'}D` : (item.repeats || '0'),
        quantity: item.quantity,
        defer: item.defer,
      };
    });

    // Write job to Supabase
    const { data, error } = await supabase
      .from('dispense_jobs')
      .insert({
        pharmacy_id: PHARMACY_ID,
        status: 'pending',
        payload: {
          patient: payload.patient,
          doctor: payload.doctor,
          scriptType: payload.scriptType,
          scriptDate: payload.scriptDate,
          items: selected.map((i) => ({ ...i, defer: false })),
          deferredItems: deferred.map((i) => ({ ...i, defer: true })),
        },
      })
      .select()
      .single();

    if (error) {
      console.error('Supabase error:', error);
      return NextResponse.json(
        { error: `Database error: ${error.message}` },
        { status: 500 }
      );
    }

    console.log('=== DISPENSE JOB CREATED ===');
    console.log('Job ID:', data.id);
    console.log('Patient:', payload.patient.name);
    console.log('Items:', selected.length, 'Deferred:', deferred.length);

    return NextResponse.json({
      success: true,
      jobId: data.id,
      message: `Queued ${selected.length} items for dispensing, ${deferred.length} deferred`,
      keystrokes,
    });
  } catch (err: unknown) {
    console.error('Dispense error:', err);
    return NextResponse.json(
      { error: err instanceof Error ? err.message : 'Failed to process dispense request' },
      { status: 500 }
    );
  }
}
