'use client';

import React, { useState, useEffect } from 'react';
import { Shield, Zap, HardDrive, MapPin, RefreshCw, Activity, CheckCircle } from 'lucide-react';

interface PingNode {
  city: string;
  code: string;
  basePing: number;
  currentPing: number;
}

export default function BentoGrid() {
  const [pings, setPings] = useState<PingNode[]>([
    { city: 'Frankfurt', code: 'DE', basePing: 12, currentPing: 12 },
    { city: 'New York', code: 'USA', basePing: 24, currentPing: 24 },
    { city: 'London', code: 'UK', basePing: 16, currentPing: 16 },
    { city: 'Singapore', code: 'SG', basePing: 65, currentPing: 65 },
    { city: 'San Francisco', code: 'USA', basePing: 38, currentPing: 38 },
  ]);

  // Simulate real-time network fluctuations in the anchor cell
  useEffect(() => {
    const interval = setInterval(() => {
      setPings((prevPings) =>
        prevPings.map((p) => {
          const drift = (Math.random() - 0.5) * 2; // -1ms to +1ms
          const newPing = Math.max(p.basePing - 3, Math.min(p.basePing + 5, p.currentPing + drift));
          return { ...p, currentPing: Number(newPing.toFixed(1)) };
        })
      );
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  return (
    <section className="py-20 relative overflow-hidden">
      {/* Background decorations */}
      <div className="absolute top-1/2 left-0 w-96 h-96 bg-primary/3 blur-[120px] rounded-full pointer-events-none" />
      <div className="absolute bottom-0 right-0 w-96 h-96 bg-blue-500/3 blur-[120px] rounded-full pointer-events-none" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        
        {/* Header */}
        <div className="text-center md:text-left max-w-3xl mb-12">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary border border-border text-xs font-semibold text-primary mb-4">
            <Activity className="h-3.5 w-3.5" />
            <span>High-Performance Infrastructure</span>
          </div>
          <h2 className="text-[clamp(1.75rem,4vw,2.75rem)] font-extrabold leading-tight text-white tracking-tight mb-4">
            Engineered for <span className="text-primary">Zero Latency</span> and Zero Downtime
          </h2>
          <p className="text-muted-foreground text-base md:text-lg">
            We don't share threads or oversell hardware. Your game runs on isolated, high-frequency AMD Ryzen 9 processors backed by NVMe SSDs.
          </p>
        </div>

        {/* Bento Grid */}
        <div className="grid grid-cols-1 md:grid-cols-12 gap-4 auto-rows-[160px] md:auto-rows-[180px]">
          
          {/* ANCHOR CELL: Global Low-Latency Network (Span 12 on mobile, 7 on desktop, 2 rows) */}
          <div className="md:col-span-7 md:row-span-2 rounded-2xl bg-card border border-border/80 p-6 flex flex-col justify-between relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-64 h-64 bg-primary/5 blur-[60px] rounded-full pointer-events-none" />
            
            <div>
              <div className="flex items-center gap-3 mb-4">
                <div className="w-10 h-10 rounded-xl bg-secondary border border-border flex items-center justify-center text-primary">
                  <MapPin className="h-5 w-5" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-white">Global Edge Network</h3>
                  <p className="text-xs text-muted-foreground">Premium routing through Tier-1 transit providers</p>
                </div>
              </div>
              
              <p className="text-sm text-muted-foreground max-w-md leading-relaxed mb-6">
                Our global network topology guarantees sub-millisecond packet routing. Players connect automatically to the nearest server location to eliminate desync.
              </p>
            </div>

            {/* Simulated Live Ping Status */}
            <div className="bg-secondary/40 border border-border/50 rounded-xl p-4">
              <div className="flex items-center justify-between text-xs text-muted-foreground border-b border-border/40 pb-2 mb-2">
                <span>POP Location</span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
                  Live Ping Status
                </span>
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
                {pings.map((p) => (
                  <div key={p.city} className="flex flex-col gap-1 bg-background/50 border border-border/30 rounded-lg p-2.5 transition-colors hover:border-primary/20">
                    <span className="text-xs font-semibold text-white truncate">{p.city}</span>
                    <span className="text-[10px] text-muted-foreground">{p.code}</span>
                    <span className="text-xs font-mono font-bold text-primary mt-1">{p.currentPing} ms</span>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* CELL 2: Instant Setup (Span 12 on mobile, 5 on desktop, 1 row) */}
          <div className="md:col-span-5 md:row-span-1 rounded-2xl bg-card border border-border/80 p-6 flex items-center gap-5 relative overflow-hidden group">
            <div className="w-12 h-12 rounded-xl bg-secondary border border-border flex items-center justify-center text-primary flex-shrink-0">
              <Zap className="h-6 w-6" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white mb-1">Instant Server Provisioning</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                No waiting. The moment your transaction clears, our automated orchestration script spins up your containerized server in under 55 seconds.
              </p>
            </div>
          </div>

          {/* CELL 3: DDoS Protection (Span 12 on mobile, 5 on desktop, 1 row) */}
          <div className="md:col-span-5 md:row-span-1 rounded-2xl bg-card border border-border/80 p-6 flex items-center gap-5 relative overflow-hidden group">
            <div className="w-12 h-12 rounded-xl bg-secondary border border-border flex items-center justify-center text-red-400 flex-shrink-0">
              <Shield className="h-6 w-6" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white mb-1">12 Tbps DDoS Mitigation</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Always-on protection. Our multi-layered filtering nodes absorb heavy UDP floods and target-specific application layer exploits instantly.
              </p>
            </div>
          </div>

          {/* CELL 4: NVMe Storage (Span 12 on mobile, 4 on desktop, 1 row) */}
          <div className="md:col-span-4 md:row-span-1 rounded-2xl bg-card border border-border/80 p-5 flex flex-col justify-between relative overflow-hidden group">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-secondary border border-border flex items-center justify-center text-primary">
                <HardDrive className="h-4.5 w-4.5" />
              </div>
              <h3 className="text-sm font-bold text-white">PCIe 4.0 NVMe RAID</h3>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed my-2">
              Uncapped read/write operations. Fast chunks load, rapid map renders, and seamless mod syncing without micro-stutters.
            </p>
            <div className="text-[10px] text-primary/80 font-mono flex items-center gap-1">
              <CheckCircle className="h-3 w-3" /> Up to 7,200 MB/s speed
            </div>
          </div>

          {/* CELL 5: Backups (Span 12 on mobile, 4 on desktop, 1 row) */}
          <div className="md:col-span-4 md:row-span-1 rounded-2xl bg-card border border-border/80 p-5 flex flex-col justify-between relative overflow-hidden group">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-secondary border border-border flex items-center justify-center text-primary">
                <RefreshCw className="h-4.5 w-4.5" />
              </div>
              <h3 className="text-sm font-bold text-white">Automated World Backups</h3>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed my-2">
              Our system takes rolling snapshots of your game world every 6 hours. Restore any snapshot directly from your dashboard with one click.
            </p>
            <div className="text-[10px] text-primary/80 font-mono flex items-center gap-1">
              <CheckCircle className="h-3 w-3" /> Offsite cloud storage
            </div>
          </div>

          {/* CELL 6: Mod Manager (Span 12 on mobile, 4 on desktop, 1 row) */}
          <div className="md:col-span-4 md:row-span-1 rounded-2xl bg-card border border-border/80 p-5 flex flex-col justify-between relative overflow-hidden group">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-lg bg-secondary border border-border flex items-center justify-center text-primary">
                <Activity className="h-4.5 w-4.5" />
              </div>
              <h3 className="text-sm font-bold text-white">Advanced Metrics</h3>
            </div>
            <p className="text-xs text-muted-foreground leading-relaxed my-2">
              Monitor server performance, player count trends, and RAM consumption in real-time. Gain precise diagnostic control over your environment.
            </p>
            <div className="text-[10px] text-primary/80 font-mono flex items-center gap-1">
              <CheckCircle className="h-3 w-3" /> Grafana-powered telemetry
            </div>
          </div>

        </div>

      </div>
    </section>
  );
}
