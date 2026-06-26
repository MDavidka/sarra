'use client';

import React from 'react';
import GameCard from '@/components/GameCard';
import { Gamepad2, Info } from 'lucide-react';

export default function GamesPage() {
  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-12">
      
      {/* Header Info */}
      <div className="text-center md:text-left max-w-3xl mb-12">
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-secondary border border-border text-xs font-semibold text-primary mb-4">
          <Gamepad2 className="h-3.5 w-3.5" />
          <span>Extensive Multiplayer Support</span>
        </div>
        <h1 className="text-[clamp(1.75rem,5vw,2.75rem)] font-extrabold leading-tight text-white tracking-tight mb-4">
          Supported Multiplayer <span className="text-primary">Game Servers</span>
        </h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          Browse our high-performance game servers. Every server is deployed on high-frequency AMD Ryzen cores with ultra-low latency routing. Select a game to configure and launch your node.
        </p>
      </div>

      {/* Interactive Catalog */}
      <GameCard />

      {/* Info Notice */}
      <div className="mt-16 bg-secondary/30 border border-border/80 rounded-2xl p-6 flex flex-col sm:flex-row gap-4 items-start sm:items-center">
        <div className="p-3 rounded-xl bg-primary/10 border border-primary/20 text-primary flex-shrink-0">
          <Info className="h-6 w-6" />
        </div>
        <div>
          <h3 className="text-sm font-bold text-white mb-1">Don't see your favorite game?</h3>
          <p className="text-xs text-muted-foreground max-w-2xl leading-relaxed">
            We are constantly adding new titles to our edge network. Our custom Docker-based orchestrator can run almost any game server. Contact our engineering team to request custom game containers or dedicated enterprise clusters.
          </p>
        </div>
      </div>

    </div>
  );
}
