import { createClient } from '@supabase/supabase-js';

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL!;
const supabaseKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;

export const supabase = createClient(supabaseUrl, supabaseKey);

export interface DispenseJob {
  id?: string;
  pharmacy_id: string;
  status: 'pending' | 'processing' | 'completed' | 'failed' | 'cancelled';
  payload: {
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
    items: Array<{
      drugName: string;
      strength: string;
      form: string;
      quantity: string;
      repeats: string;
      directions: string;
      defer: boolean;
    }>;
    deferredItems: Array<{
      drugName: string;
      strength: string;
      form: string;
      quantity: string;
      repeats: string;
      directions: string;
      defer: boolean;
    }>;
  };
  result?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

export interface ScanHistoryEntry {
  id?: string;
  pharmacy_id: string;
  image_url?: string;
  extracted_data: Record<string, unknown>;
  raw_ai_response?: string;
  confidence?: number;
  created_at?: string;
}
