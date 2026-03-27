-- ScriptScanner Supabase Schema
-- Run this in Supabase SQL Editor: https://supabase.com/dashboard/project/oodyhkovgftqgbmimecd/sql

-- Dispense Jobs table — bridge between web app and dispensary PC
CREATE TABLE IF NOT EXISTS dispense_jobs (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  pharmacy_id TEXT NOT NULL DEFAULT 'legana-dds',
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'cancelled')),
  payload JSONB NOT NULL,
  result JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for polling by pharmacy + status
CREATE INDEX IF NOT EXISTS idx_dispense_jobs_pharmacy_status
  ON dispense_jobs (pharmacy_id, status, created_at);

-- Scan history — log of all scanned prescriptions
CREATE TABLE IF NOT EXISTS scan_history (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  pharmacy_id TEXT NOT NULL DEFAULT 'legana-dds',
  image_url TEXT,
  extracted_data JSONB NOT NULL,
  raw_ai_response TEXT,
  confidence NUMERIC(5,2),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE dispense_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE scan_history ENABLE ROW LEVEL SECURITY;

-- Allow anon key to read/write (for MVP — tighten later with auth)
CREATE POLICY "Allow all for dispense_jobs" ON dispense_jobs
  FOR ALL USING (true) WITH CHECK (true);

CREATE POLICY "Allow all for scan_history" ON scan_history
  FOR ALL USING (true) WITH CHECK (true);

-- Enable realtime for dispense_jobs (so dispensary can subscribe instead of polling)
ALTER PUBLICATION supabase_realtime ADD TABLE dispense_jobs;
