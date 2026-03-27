'use client';

import { useState, useRef, useEffect, useCallback } from 'react';

// ─── Types ───────────────────────────────────────────────────────────
interface ScriptItem {
  id: string;
  drugName: string;
  strength: string;
  form: string;
  quantity: string;
  repeats: string;
  directions: string;
  selected: boolean;
  defer: boolean;
}

interface ScriptData {
  patientName: string;
  patientDOB: string;
  patientAddress: string;
  medicareNumber: string;
  doctorName: string;
  prescriberNumber: string;
  scriptType: string;
  scriptDate: string;
  items: ScriptItem[];
  rawText?: string;
}

type AppState = 'camera' | 'processing' | 'review' | 'sending';

// ─── Main Component ─────────────────────────────────────────────────
export default function Home() {
  const [state, setState] = useState<AppState>('camera');
  const [scriptData, setScriptData] = useState<ScriptData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [imagePreview, setImagePreview] = useState<string | null>(null);
  const [wsStatus, setWsStatus] = useState<'disconnected' | 'connected' | 'error'>('disconnected');
  const fileInputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Register service worker
  useEffect(() => {
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/sw.js').catch(() => {});
    }
  }, []);

  // ─── Camera / File Capture ───────────────────────────────────────
  const handleCapture = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setError(null);
    setState('processing');

    // Show preview
    const reader = new FileReader();
    reader.onload = (ev) => setImagePreview(ev.target?.result as string);
    reader.readAsDataURL(file);

    // Convert to base64 for API
    const base64 = await fileToBase64(file);

    try {
      const res = await fetch('/api/scan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image: base64,
          mimeType: file.type || 'image/jpeg',
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || `Server error: ${res.status}`);
      }

      const data: ScriptData = await res.json();
      setScriptData(data);
      setState('review');
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to scan script');
      setState('camera');
    }

    // Reset input so same file can be re-selected
    e.target.value = '';
  }, []);

  // ─── Item Selection ──────────────────────────────────────────────
  const toggleItem = useCallback((id: string, field: 'selected' | 'defer') => {
    setScriptData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        items: prev.items.map((item) => {
          if (item.id !== id) return item;
          if (field === 'defer') {
            return { ...item, defer: !item.defer, selected: !item.defer ? false : item.selected };
          }
          return { ...item, selected: !item.selected, defer: !item.selected ? false : item.defer };
        }),
      };
    });
  }, []);

  // ─── Dispense Action ─────────────────────────────────────────────
  const handleDispense = useCallback(async () => {
    if (!scriptData) return;
    const selected = scriptData.items.filter((i) => i.selected);
    const deferred = scriptData.items.filter((i) => i.defer);
    if (selected.length === 0 && deferred.length === 0) {
      setError('Select at least one item to dispense or defer');
      return;
    }

    setState('sending');
    setError(null);

    try {
      const res = await fetch('/api/dispense', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          patient: {
            name: scriptData.patientName,
            dob: scriptData.patientDOB,
            address: scriptData.patientAddress,
            medicare: scriptData.medicareNumber,
          },
          doctor: {
            name: scriptData.doctorName,
            searchName: (scriptData as Record<string, string>).doctorSearchName || '',
            prescriberNumber: scriptData.prescriberNumber,
          },
          scriptType: scriptData.scriptType,
          scriptDate: scriptData.scriptDate,
          items: selected.map((i) => ({ ...i, defer: false })),
          deferredItems: deferred.map((i) => ({ ...i, defer: true })),
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error || 'Failed to send to dispensary');
      }

      // Success — reset
      setState('camera');
      setScriptData(null);
      setImagePreview(null);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to send');
      setState('review');
    }
  }, [scriptData]);

  // ─── Reset ────────────────────────────────────────────────────────
  const handleReset = useCallback(() => {
    setState('camera');
    setScriptData(null);
    setImagePreview(null);
    setError(null);
  }, []);

  // ─── Render ───────────────────────────────────────────────────────
  return (
    <div style={styles.container}>
      {/* Header */}
      <header style={styles.header}>
        <div style={styles.headerLeft}>
          <span style={styles.logo}>💊</span>
          <h1 style={styles.title}>ScriptScan</h1>
        </div>
        <div style={styles.headerRight}>
          <span style={{
            ...styles.statusDot,
            background: wsStatus === 'connected' ? 'var(--success)' : wsStatus === 'error' ? 'var(--danger)' : 'var(--text-dim)',
          }} />
          <span style={styles.statusText}>
            {wsStatus === 'connected' ? 'PC Connected' : 'PC Offline'}
          </span>
        </div>
      </header>

      {/* Error Banner */}
      {error && (
        <div style={styles.errorBanner}>
          <span>⚠️ {error}</span>
          <button onClick={() => setError(null)} style={styles.errorClose}>✕</button>
        </div>
      )}

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/*"
        capture="environment"
        onChange={handleFileChange}
        style={{ display: 'none' }}
      />

      {/* Camera State */}
      {state === 'camera' && (
        <div style={styles.cameraView}>
          <div style={styles.cameraIcon}>📸</div>
          <h2 style={styles.cameraTitle}>Scan a Prescription</h2>
          <p style={styles.cameraSubtitle}>
            Take a photo or select an image of the prescription
          </p>
          <button onClick={handleCapture} style={styles.captureBtn}>
            📷 Take Photo
          </button>
          <button onClick={() => {
            // Remove capture attribute for gallery pick
            if (fileInputRef.current) {
              fileInputRef.current.removeAttribute('capture');
              fileInputRef.current.click();
              // Restore capture for next time
              setTimeout(() => fileInputRef.current?.setAttribute('capture', 'environment'), 500);
            }
          }} style={styles.galleryBtn}>
            🖼️ Choose from Gallery
          </button>
        </div>
      )}

      {/* Processing State */}
      {state === 'processing' && (
        <div style={styles.processingView}>
          {imagePreview && (
            <img src={imagePreview} alt="Script preview" style={styles.preview} />
          )}
          <div style={styles.spinner} />
          <p style={styles.processingText}>Reading prescription...</p>
          <p style={styles.processingSubtext}>Claude Vision is extracting details</p>
        </div>
      )}

      {/* Review State */}
      {state === 'review' && scriptData && (
        <div style={styles.reviewView}>
          {/* Patient Info Card */}
          <div style={styles.card}>
            <div style={styles.cardHeader}>
              <span>👤 Patient</span>
              <span style={styles.scriptTypeBadge}>{scriptData.scriptType}</span>
            </div>
            <div style={styles.cardBody}>
              <InfoRow label="Name" value={scriptData.patientName} />
              <InfoRow label="DOB" value={scriptData.patientDOB} />
              <InfoRow label="Address" value={scriptData.patientAddress} />
              <InfoRow label="Medicare" value={scriptData.medicareNumber} />
            </div>
          </div>

          {/* Doctor Info Card */}
          <div style={styles.card}>
            <div style={styles.cardHeader}>👨‍⚕️ Prescriber</div>
            <div style={styles.cardBody}>
              <InfoRow label="Name" value={scriptData.doctorName} />
              <InfoRow label="Prescriber #" value={scriptData.prescriberNumber} />
              <InfoRow label="Script Date" value={scriptData.scriptDate} />
            </div>
          </div>

          {/* Items */}
          <div style={styles.itemsHeader}>
            <span>💊 Medications ({scriptData.items.length})</span>
          </div>

          {scriptData.items.map((item) => (
            <div key={item.id} style={{
              ...styles.itemCard,
              borderLeft: item.selected ? '4px solid var(--success)' : item.defer ? '4px solid var(--warning)' : '4px solid var(--border)',
            }}>
              <div style={styles.itemTop}>
                <div style={styles.itemDrug}>
                  <strong>{item.drugName}</strong>
                  <span style={styles.itemDetail}>
                    {item.strength} {item.form}
                  </span>
                </div>
              </div>
              <div style={styles.itemMeta}>
                <span>Qty: {item.quantity}</span>
                <span>Repeats: {item.repeats}</span>
              </div>
              <div style={styles.itemDirections}>{item.directions}</div>
              <div style={styles.itemActions}>
                <button
                  onClick={() => toggleItem(item.id, 'selected')}
                  style={{
                    ...styles.actionBtn,
                    background: item.selected ? 'var(--success)' : 'var(--surface-2)',
                  }}
                >
                  {item.selected ? '✓ Dispense' : 'Dispense'}
                </button>
                <button
                  onClick={() => toggleItem(item.id, 'defer')}
                  style={{
                    ...styles.actionBtn,
                    background: item.defer ? 'var(--warning)' : 'var(--surface-2)',
                    color: item.defer ? '#000' : 'var(--text)',
                  }}
                >
                  {item.defer ? '⏸ Defer' : 'Defer'}
                </button>
              </div>
            </div>
          ))}

          {/* Action Buttons */}
          <div style={styles.bottomActions}>
            <button onClick={handleReset} style={styles.resetBtn}>
              ← New Scan
            </button>
            <button onClick={handleDispense} style={styles.dispenseBtn}>
              DISPENSE →
            </button>
          </div>
        </div>
      )}

      {/* Sending State */}
      {state === 'sending' && (
        <div style={styles.processingView}>
          <div style={styles.spinner} />
          <p style={styles.processingText}>Sending to dispensary...</p>
        </div>
      )}
    </div>
  );
}

// ─── Sub-components ──────────────────────────────────────────────────
function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div style={styles.infoRow}>
      <span style={styles.infoLabel}>{label}</span>
      <span style={styles.infoValue}>{value || '—'}</span>
    </div>
  );
}

// ─── Helpers ─────────────────────────────────────────────────────────
function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      // Strip data URL prefix to get raw base64
      resolve(result.split(',')[1]);
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

// ─── Styles ──────────────────────────────────────────────────────────
const styles: Record<string, React.CSSProperties> = {
  container: {
    maxWidth: 480,
    margin: '0 auto',
    minHeight: '100dvh',
    display: 'flex',
    flexDirection: 'column',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px 20px',
    background: 'var(--surface)',
    borderBottom: '1px solid var(--border)',
    position: 'sticky',
    top: 0,
    zIndex: 10,
  },
  headerLeft: { display: 'flex', alignItems: 'center', gap: 10 },
  logo: { fontSize: 24 },
  title: { fontSize: 20, fontWeight: 700 },
  headerRight: { display: 'flex', alignItems: 'center', gap: 8 },
  statusDot: { width: 8, height: 8, borderRadius: '50%' },
  statusText: { fontSize: 12, color: 'var(--text-dim)' },

  errorBanner: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 20px',
    background: '#7f1d1d',
    color: '#fca5a5',
    fontSize: 14,
  },
  errorClose: { background: 'none', color: '#fca5a5', fontSize: 18, padding: '0 4px' },

  // Camera
  cameraView: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 40,
    gap: 16,
  },
  cameraIcon: { fontSize: 64 },
  cameraTitle: { fontSize: 24, fontWeight: 700 },
  cameraSubtitle: { fontSize: 14, color: 'var(--text-dim)', textAlign: 'center' },
  captureBtn: {
    width: '100%',
    maxWidth: 300,
    padding: '16px 24px',
    fontSize: 18,
    fontWeight: 600,
    background: 'var(--primary)',
    color: '#fff',
    borderRadius: 'var(--radius)',
    marginTop: 16,
  },
  galleryBtn: {
    width: '100%',
    maxWidth: 300,
    padding: '14px 24px',
    fontSize: 16,
    fontWeight: 500,
    background: 'var(--surface)',
    color: 'var(--text)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
  },

  // Processing
  processingView: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: 40,
    gap: 16,
  },
  preview: {
    width: '100%',
    maxWidth: 300,
    maxHeight: 200,
    objectFit: 'contain',
    borderRadius: 'var(--radius)',
    marginBottom: 16,
  },
  spinner: {
    width: 48,
    height: 48,
    border: '4px solid var(--surface-2)',
    borderTopColor: 'var(--primary)',
    borderRadius: '50%',
    animation: 'spin 0.8s linear infinite',
  },
  processingText: { fontSize: 18, fontWeight: 600 },
  processingSubtext: { fontSize: 14, color: 'var(--text-dim)' },

  // Review
  reviewView: {
    flex: 1,
    padding: '16px 16px 100px',
    display: 'flex',
    flexDirection: 'column',
    gap: 12,
    overflowY: 'auto',
  },
  card: {
    background: 'var(--surface)',
    borderRadius: 'var(--radius)',
    overflow: 'hidden',
  },
  cardHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '12px 16px',
    background: 'var(--surface-2)',
    fontWeight: 600,
    fontSize: 15,
  },
  cardBody: { padding: '8px 16px 12px' },
  scriptTypeBadge: {
    background: 'var(--primary)',
    color: '#fff',
    padding: '2px 10px',
    borderRadius: 20,
    fontSize: 12,
    fontWeight: 700,
  },
  infoRow: {
    display: 'flex',
    justifyContent: 'space-between',
    padding: '6px 0',
    borderBottom: '1px solid var(--surface-2)',
    fontSize: 14,
  },
  infoLabel: { color: 'var(--text-dim)', fontWeight: 500 },
  infoValue: { fontWeight: 600, textAlign: 'right' as const, maxWidth: '60%' },

  itemsHeader: {
    fontSize: 15,
    fontWeight: 600,
    padding: '8px 4px 0',
  },
  itemCard: {
    background: 'var(--surface)',
    borderRadius: 'var(--radius)',
    padding: 16,
    display: 'flex',
    flexDirection: 'column',
    gap: 8,
  },
  itemTop: { display: 'flex', justifyContent: 'space-between' },
  itemDrug: { display: 'flex', flexDirection: 'column', gap: 2 },
  itemDetail: { fontSize: 13, color: 'var(--text-dim)' },
  itemMeta: {
    display: 'flex',
    gap: 16,
    fontSize: 13,
    color: 'var(--text-dim)',
  },
  itemDirections: {
    fontSize: 13,
    color: 'var(--text-dim)',
    fontStyle: 'italic',
    padding: '4px 0',
  },
  itemActions: { display: 'flex', gap: 8, marginTop: 4 },
  actionBtn: {
    flex: 1,
    padding: '10px 12px',
    fontSize: 14,
    fontWeight: 600,
    borderRadius: 8,
    color: 'var(--text)',
    transition: 'background 0.15s',
  },

  bottomActions: {
    position: 'fixed',
    bottom: 0,
    left: 0,
    right: 0,
    display: 'flex',
    gap: 12,
    padding: '16px 20px',
    background: 'var(--bg)',
    borderTop: '1px solid var(--border)',
    maxWidth: 480,
    margin: '0 auto',
    zIndex: 10,
  },
  resetBtn: {
    flex: 1,
    padding: '14px',
    fontSize: 16,
    fontWeight: 600,
    background: 'var(--surface)',
    color: 'var(--text)',
    borderRadius: 'var(--radius)',
    border: '1px solid var(--border)',
  },
  dispenseBtn: {
    flex: 2,
    padding: '14px',
    fontSize: 18,
    fontWeight: 700,
    background: 'var(--success)',
    color: '#fff',
    borderRadius: 'var(--radius)',
  },
};
