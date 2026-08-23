'use client';

import { useEffect } from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[Syte Next.js] App crash:', error);
  }, [error]);

  return (
    <div className="profileGate">
      <div className="shadcnCard loginCard" style={{ maxWidth: '520px', width: '100%' }}>
        <div className="cardBrand">
          <div className="cardMark" style={{ background: '#ef4444', color: '#fff' }}>!</div>
          <div>
            <h1>Application Error</h1>
            <p>Syte encountered an error under high load or network interruption.</p>
          </div>
        </div>

        <div className="cardLead" style={{ marginTop: '16px', marginBottom: '16px' }}>
          <p style={{ color: '#b91c1c', fontWeight: 600, wordBreak: 'break-word' }}>
            {error.message || 'An unexpected error occurred.'}
          </p>
          {error.digest && (
            <small style={{ color: '#71717a', display: 'block', marginTop: '6px' }}>
              Error Digest: {error.digest}
            </small>
          )}
        </div>

        <div className="shadcnForm" style={{ display: 'flex', gap: '8px', marginTop: '20px' }}>
          <button
            type="button"
            className="shadcnPrimary"
            style={{ flex: 1, cursor: 'pointer' }}
            onClick={() => reset()}
          >
            Retry Connection
          </button>
          <button
            type="button"
            className="shadcnOutline"
            style={{ flex: 1, cursor: 'pointer' }}
            onClick={() => window.location.reload()}
          >
            Reload Page
          </button>
        </div>
      </div>
    </div>
  );
}
