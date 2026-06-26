'use client';

import React from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useGameStore } from '@/lib/store';
import BentoGrid from '@/components/BentoGrid';
import GameCard from '@/components/GameCard';
import { Terminal, Shield, Zap, RefreshCw, Cpu, Server, Users, ArrowRight, Play, CheckCircle2 } from 'lucide-react';

export default function HomePage() {
  const router = useRouter();
  const { user } = useGameStore();

  return (
    <div className="relative overflow-hidden pb-16">
      
      {/* HERO SECTION */}
      <section className="relative pt-12 pb-20 md:pt-20 md:pb-28 overflow-hidden">
        {/* Glow behind hero */}
        <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[350px] bg-primary/10 blur-[130px] rounded-full pointer-events-none" />
        
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10 text-center">
          {/* Tagline Badge */}
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-secondary/80 border border-border text-xs font-semibold text-primary mb-6 animate-slide-up">
            <Terminal className="h-3.5 w-3.5" />
            <span>AMD Ryzen 9 7950X3D Infrastructure Now Live</span>
          </div>

          {/* Main Title */}
          <h1 className="text-[clamp(2.25rem,6vw,4.5rem)] font-extrabold leading-[1.1] tracking-tight text-white mb-6 text-balance max-w-5xl mx-auto animate-slide-up">
            Orchestrate Your Game Servers <br className="hidden md:inline" />
            With <span className="text-primary relative">
              Aether-Speed
              <span className="absolute -bottom-1 left-0 right-0 h-1 bg-primary/30 rounded-full blur-[2px]" />
            </span>
          </h1>

          {/* Subtitle */}
          <p className="text-muted-foreground text-sm sm:text-base md:text-lg max-w-2xl mx-auto mb-10 leading-relaxed text-pretty animate-slide-up">
            Deploy dedicated, containerized game servers instantly. Experience sub-15ms latency, automated backups, and full mod support backed by enterprise DDoS protection.
          </p>

          {/* CTA Buttons */}
          <div className="flex flex-col sm:flex-row items-center justify-center gap-4 animate-slide-up">
            <Link
              href="/games"
              className="w-full sm:w-auto px-8 py-4 bg-primary text-primary-foreground text-sm font-bold rounded-xl hover:shadow-[0_0_25px_var(--primary)] hover:scale-[1.02] active:scale-[0.98] transition-all flex items-center justify-center gap-2"
            >
              <Server className="h-4 w-4" />
              Browse Games Catalog
            </Link>
            
            {user ? (
              <Link
                href="/dashboard"
                className="w-full sm:w-auto px-8 py-4 bg-secondary hover:bg-muted text-white border border-border text-sm font-bold rounded-xl transition-all flex items-center justify-center gap-2"
              >
                <Users className="h-4 w-4 text-primary" />
                Go to Dashboard
              </Link>
            ) : (
              <Link
                href="/login"
                className="w-full sm:w-auto px-8 py-4 bg-secondary hover:bg-muted text-white border border-border text-sm font-bold rounded-xl transition-all flex items-center justify-center gap-2"
              >
                Sign In to Account
                <ArrowRight className="h-4 w-4 text-muted-foreground" />
              </Link>
            )}
          </div>

          {/* Quick Platform Stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 max-w-4xl mx-auto mt-20 p-6 rounded-2xl bg-card/40 border border-border/60 backdrop-blur-md">
            <div className="flex flex-col items-center justify-center p-3">
              <span className="text-2xl md:text-3xl font-extrabold text-white">4,210+</span>
              <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mt-1">Active Nodes</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 border-l border-border/40">
              <span className="text-2xl md:text-3xl font-extrabold text-primary">&lt; 14ms</span>
              <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mt-1">Average Ping</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 border-l border-border/40">
              <span className="text-2xl md:text-3xl font-extrabold text-white">5 Region</span>
              <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mt-1">Edge Locations</span>
            </div>
            <div className="flex flex-col items-center justify-center p-3 border-l border-border/40">
              <span className="text-2xl md:text-3xl font-extrabold text-emerald-400">99.99%</span>
              <span className="text-[11px] text-muted-foreground uppercase tracking-wider font-semibold mt-1">SLA Guarantee</span>
            </div>
          </div>

        </div>
      </section>

      {/* FEATURED GAMES SECTION */}
      <section className="py-16 relative">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex flex-col md:flex-row items-baseline justify-between mb-10">
            <div>
              <h2 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">
                Featured Game Servers
              </h2>
              <p className="text-xs sm:text-sm text-muted-foreground mt-1">
                Explore our premium catalog. Optimized configurations for instant launch.
              </p>
            </div>
            <Link href="/games" className="text-xs font-bold text-primary hover:underline flex items-center gap-1 mt-2 md:mt-0">
              View all 10 games
              <ArrowRight className="h-3 w-3" />
            </Link>
          </div>

          {/* Game List (Limited to 3 for Landing Page) */}
          <GameCard limit={3} featuredOnly={true} />
        </div>
      </section>

      {/* BENTO GRID INFRASTRUCTURE */}
      <BentoGrid />

      {/* HOW IT WORKS SECTION */}
      <section className="py-16 relative overflow-hidden bg-secondary/20 border-y border-border/50">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-2xl md:text-3xl font-extrabold text-white tracking-tight">
              Get Online in 3 Simple Steps
            </h2>
            <p className="text-xs sm:text-sm text-muted-foreground mt-2">
              Our automated deployment pipeline handles the complexity. You focus on the game.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8 relative">
            {/* Step 1 */}
            <div className="bg-card border border-border/80 rounded-2xl p-6 relative group">
              <div className="absolute top-4 right-6 text-5xl font-extrabold text-primary/10 select-none">01</div>
              <div className="w-10 h-10 rounded-lg bg-primary/10 border border-primary/20 text-primary flex items-center justify-center font-bold text-sm mb-4">
                1
              </div>
              <h3 className="text-base font-bold text-white mb-2">Select Your Game</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Choose from our extensive catalog of 10+ games. Select modpacks, versions, or server types with a single click.
              </p>
            </div>

            {/* Step 2 */}
            <div className="bg-card border border-border/80 rounded-2xl p-6 relative group">
              <div className="absolute top-4 right-6 text-5xl font-extrabold text-primary/10 select-none">02</div>
              <div className="w-10 h-10 rounded-lg bg-primary/10 border border-primary/20 text-primary flex items-center justify-center font-bold text-sm mb-4">
                2
              </div>
              <h3 className="text-base font-bold text-white mb-2">Customize Resources</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Choose your node location (Europe, US East, US West, Asia) and scale your RAM and CPU cores to match your player base.
              </p>
            </div>

            {/* Step 3 */}
            <div className="bg-card border border-border/80 rounded-2xl p-6 relative group">
              <div className="absolute top-4 right-6 text-5xl font-extrabold text-primary/10 select-none">03</div>
              <div className="w-10 h-10 rounded-lg bg-primary/10 border border-primary/20 text-primary flex items-center justify-center font-bold text-sm mb-4">
                3
              </div>
              <h3 className="text-base font-bold text-white mb-2">Play Instantly</h3>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Our containerized orchestration system deploys your server in under 55 seconds. Copy your IP address and start playing!
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* FINAL CTA BANNER */}
      <section className="py-20 relative">
        <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
          <div className="relative rounded-3xl bg-gradient-to-br from-card to-secondary/80 border border-border p-8 md:p-12 text-center overflow-hidden group">
            {/* Background Glow */}
            <div className="absolute -inset-px bg-gradient-to-r from-primary/15 to-transparent opacity-50 group-hover:opacity-100 transition-opacity duration-500 rounded-3xl pointer-events-none" />
            <div className="absolute -bottom-48 -right-48 w-96 h-96 bg-primary/10 blur-[100px] rounded-full pointer-events-none" />

            <div className="relative z-10 max-w-2xl mx-auto">
              <Cpu className="h-10 w-10 text-primary mx-auto mb-6 animate-pulse-glow" />
              <h2 className="text-[clamp(1.5rem,4vw,2.5rem)] font-extrabold leading-tight text-white mb-4">
                Ready to Deploy Your Game Server?
              </h2>
              <p className="text-xs sm:text-sm text-muted-foreground mb-8 max-w-md mx-auto leading-relaxed">
                Join thousands of gamers who host their worlds with AetherNode. Unparalleled speeds, 100% hardware isolation, and zero lag.
              </p>
              <div className="flex flex-col sm:flex-row items-center justify-center gap-3">
                <Link
                  href="/games"
                  className="w-full sm:w-auto px-6 py-3 bg-primary text-primary-foreground text-xs font-bold rounded-lg hover:shadow-[0_0_15px_var(--primary)] transition-all"
                >
                  Configure Server
                </Link>
                <Link
                  href="/login"
                  className="w-full sm:w-auto px-6 py-3 bg-secondary hover:bg-muted text-white border border-border text-xs font-semibold rounded-lg transition-all"
                >
                  Create Free Account
                </Link>
              </div>
            </div>
          </div>
        </div>
      </section>

    </div>
  );
}
