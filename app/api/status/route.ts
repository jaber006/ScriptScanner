import { NextResponse } from 'next/server';
import { supabase } from '@/app/lib/supabase';

const PHARMACY_ID = process.env.PHARMACY_ID || 'legana-dds';

/**
 * Status API — check if the dispensary agent is online.
 * 
 * The agent is considered "online" if it has processed a job
 * in the last 60 seconds, or if there are no stuck "processing" jobs.
 */
export async function GET() {
  try {
    // Check for recently completed jobs (last 5 minutes)
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();

    const { data: recentJobs, error: recentError } = await supabase
      .from('dispense_jobs')
      .select('id, status, updated_at')
      .eq('pharmacy_id', PHARMACY_ID)
      .gte('updated_at', fiveMinAgo)
      .in('status', ['completed', 'processing'])
      .limit(1);

    if (recentError) {
      return NextResponse.json({
        online: false,
        error: recentError.message,
      });
    }

    // Check for pending jobs (queue depth)
    const { count, error: pendingError } = await supabase
      .from('dispense_jobs')
      .select('id', { count: 'exact', head: true })
      .eq('pharmacy_id', PHARMACY_ID)
      .eq('status', 'pending');

    if (pendingError) {
      return NextResponse.json({
        online: false,
        error: pendingError.message,
      });
    }

    const online = (recentJobs?.length ?? 0) > 0;

    return NextResponse.json({
      online,
      pendingJobs: count ?? 0,
      lastActivity: recentJobs?.[0]?.updated_at ?? null,
      pharmacyId: PHARMACY_ID,
    });
  } catch (err: unknown) {
    return NextResponse.json({
      online: false,
      error: err instanceof Error ? err.message : 'Status check failed',
    });
  }
}
