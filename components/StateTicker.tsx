'use client';

import { useEffect } from 'react';
import { useGameStore } from '@/lib/store';

export default function StateTicker() {
  const tickInstances = useGameStore((state) => state.tickInstances);

  useEffect(() => {
    // Tick instances every 3 seconds to simulate active workloads
    const interval = setInterval(() => {
      tickInstances();
    }, 3000);

    return () => clearInterval(interval);
  }, [tickInstances]);

  return null; // Invisible helper component
}
