import React from 'react';
import Link from 'next/link';
import { Cpu, Github, Twitter, MessageSquare, ShieldCheck, Heart } from 'lucide-react';

export default function Footer() {
  return (
    <footer className="relative bg-card border-t border-border/80 pt-16 pb-8 overflow-hidden">
      {/* Glow Effect */}
      <div className="absolute bottom-0 left-1/2 -translate-x-1/2 w-[600px] h-[150px] bg-primary/5 blur-[120px] rounded-full pointer-events-none" />

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 relative z-10">
        <div className="grid grid-cols-1 md:grid-cols-12 gap-8 pb-12 border-b border-border/50">
          
          {/* Brand Column */}
          <div className="md:col-span-5 flex flex-col gap-4">
            <Link href="/" className="flex items-center gap-2">
              <div className="w-8 h-8 rounded bg-secondary border border-border flex items-center justify-center">
                <Cpu className="h-4 w-4 text-primary" />
              </div>
              <span className="text-lg font-extrabold tracking-wider">
                AETHER<span className="text-primary">NODE</span>
              </span>
            </Link>
            <p className="text-sm text-muted-foreground max-w-sm leading-relaxed">
              Enterprise-grade game server hosting engineered for extreme performance, ultra-low latency, and maximum uptime. Deploy your battlefield in 55 seconds.
            </p>
            <div className="flex items-center gap-3 mt-2">
              <a href="#" className="p-2 rounded bg-secondary border border-border text-muted-foreground hover:text-primary transition-colors" aria-label="Twitter">
                <Twitter className="h-4 w-4" />
              </a>
              <a href="#" className="p-2 rounded bg-secondary border border-border text-muted-foreground hover:text-primary transition-colors" aria-label="Discord">
                <MessageSquare className="h-4 w-4" />
              </a>
              <a href="#" className="p-2 rounded bg-secondary border border-border text-muted-foreground hover:text-primary transition-colors" aria-label="GitHub">
                <Github className="h-4 w-4" />
              </a>
            </div>
          </div>

          {/* Links Column 1 */}
          <div className="md:col-span-2 col-span-1">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-foreground mb-4">Platform</h3>
            <ul className="flex flex-col gap-2">
              <li><Link href="/games" className="text-sm text-muted-foreground hover:text-primary transition-colors">Game Servers</Link></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Global Network</a></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">DDoS Protection</a></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Pricing</a></li>
            </ul>
          </div>

          {/* Links Column 2 */}
          <div className="md:col-span-2 col-span-1">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-foreground mb-4">Support</h3>
            <ul className="flex flex-col gap-2">
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Knowledgebase</a></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Server Status</a></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Contact Support</a></li>
              <li><a href="#" className="text-sm text-muted-foreground hover:text-primary transition-colors">Billing Portal</a></li>
            </ul>
          </div>

          {/* Newsletter Column */}
          <div className="md:col-span-3 flex flex-col gap-4">
            <h3 className="text-sm font-semibold uppercase tracking-wider text-foreground">Stay Updated</h3>
            <p className="text-xs text-muted-foreground">
              Subscribe to get notified of new game releases and infrastructure updates.
            </p>
            <form onSubmit={(e) => e.preventDefault()} className="flex gap-2">
              <input
                type="email"
                placeholder="Enter email"
                className="flex-grow bg-secondary border border-border text-xs rounded-lg px-3 py-2 outline-none focus:border-primary/50 transition-colors text-white"
                required
              />
              <button
                type="submit"
                className="bg-primary text-primary-foreground text-xs font-semibold px-3 py-2 rounded-lg hover:shadow-[0_0_10px_var(--primary)] transition-all"
              >
                Join
              </button>
            </form>
          </div>

        </div>

        {/* Bottom bar */}
        <div className="flex flex-col sm:flex-row items-center justify-between pt-8 text-xs text-muted-foreground">
          <div className="flex items-center gap-1">
            <span>© {new Date().getFullYear()} AetherNode. All rights reserved.</span>
          </div>
          <div className="flex items-center gap-4 mt-4 sm:mt-0">
            <a href="#" className="hover:text-white transition-colors">Terms of Service</a>
            <span>•</span>
            <a href="#" className="hover:text-white transition-colors">Privacy Policy</a>
            <span>•</span>
            <div className="flex items-center gap-1 text-emerald-400">
              <ShieldCheck className="h-3.5 w-3.5" />
              <span>SLA 99.99% Guaranteed</span>
            </div>
          </div>
        </div>

      </div>
    </footer>
  );
}
