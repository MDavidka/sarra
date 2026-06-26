'use client';

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useGameStore, Game } from '@/lib/store';
import { Search, SlidersHorizontal, MapPin, Cpu, HardDrive, Users, Check, X, Shield, ArrowRight } from 'lucide-react';

interface GameCardProps {
  limit?: number;
  featuredOnly?: boolean;
}

const LOCATIONS = [
  { name: 'Frankfurt, DE', code: 'FRA', ping: '12ms' },
  { name: 'New York, USA', code: 'NYC', ping: '24ms' },
  { name: 'Singapore, SG', code: 'SGP', ping: '65ms' },
  { name: 'London, UK', code: 'LND', ping: '16ms' },
  { name: 'San Francisco, USA', code: 'SFO', ping: '38ms' }
];

export default function GameCard({ limit, featuredOnly = false }: GameCardProps) {
  const router = useRouter();
  const { games, user, createInstance, login } = useGameStore();
  
  // Search & Filter state
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('All');

  // Deployment modal state
  const [selectedGame, setSelectedGame] = useState<Game | null>(null);
  const [serverName, setServerName] = useState('');
  const [selectedLocation, setSelectedLocation] = useState(LOCATIONS[0].name);
  const [customRam, setCustomRam] = useState(4);
  const [isDeploying, setIsDeploying] = useState(false);
  const [deploySuccess, setDeploySuccess] = useState(false);

  // Guest login state inside modal
  const [loginEmail, setLoginEmail] = useState('');
  const [loginPassword, setLoginPassword] = useState('');
  const [showAuthForm, setShowAuthForm] = useState(false);

  const categories = ['All', 'Survival', 'Sandbox', 'FPS', 'Strategy', 'RPG'];

  // Filter games list
  const filteredGames = games.filter((game) => {
    const matchesSearch = game.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          game.description.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesCategory = selectedCategory === 'All' || game.category === selectedCategory;
    return matchesSearch && matchesCategory;
  });

  const displayedGames = limit ? filteredGames.slice(0, limit) : filteredGames;

  const handleOpenDeploy = (game: Game) => {
    setSelectedGame(game);
    setServerName(`${user ? user.username : 'My'}'s ${game.name} Server`);
    setCustomRam(game.ram);
    setShowAuthForm(!user);
    setDeploySuccess(false);
  };

  const handleCloseDeploy = () => {
    setSelectedGame(null);
    setIsDeploying(false);
    setDeploySuccess(false);
  };

  const handleInlineLogin = (e: React.FormEvent) => {
    e.preventDefault();
    if (loginEmail) {
      login(loginEmail);
      setShowAuthForm(false);
      setServerName(`${loginEmail.split('@')[0]}'s ${selectedGame?.name} Server`);
    }
  };

  const handleDeploy = () => {
    if (!selectedGame) return;
    setIsDeploying(true);

    // Simulate server orchestration delay
    setTimeout(() => {
      createInstance(serverName, selectedGame.id, selectedLocation, customRam);
      setIsDeploying(false);
      setDeploySuccess(true);
      
      // Redirect to dashboard after success screen
      setTimeout(() => {
        handleCloseDeploy();
        router.push('/dashboard');
      }, 1500);
    }, 2000);
  };

  return (
    <div className="w-full">
      {/* Search & Filter Controls (only show if not limited) */}
      {!limit && (
        <div className="flex flex-col md:flex-row items-stretch md:items-center justify-between gap-4 mb-8 bg-card/50 border border-border/60 p-4 rounded-xl">
          {/* Search */}
          <div className="relative flex-grow max-w-md">
            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <input
              type="text"
              placeholder="Search games (e.g., Minecraft, Palworld)..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-secondary/80 border border-border text-sm rounded-lg pl-10 pr-4 py-2.5 outline-none focus:border-primary/50 transition-colors text-white"
            />
          </div>

          {/* Categories */}
          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 md:pb-0 scrollbar-none">
            <SlidersHorizontal className="h-4 w-4 text-muted-foreground mr-1 hidden sm:block" />
            {categories.map((cat) => (
              <button
                key={cat}
                onClick={() => setSelectedCategory(cat)}
                className={`text-xs font-semibold px-3 py-1.5 rounded-lg border transition-all whitespace-nowrap ${
                  selectedCategory === cat
                    ? 'bg-primary text-primary-foreground border-primary shadow-[0_0_10px_rgba(0,229,255,0.2)]'
                    : 'bg-secondary/40 border-border/60 text-muted-foreground hover:text-white hover:bg-secondary'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Games Grid */}
      {displayedGames.length === 0 ? (
        <div className="text-center py-16 border border-dashed border-border rounded-2xl bg-card/20">
          <p className="text-muted-foreground text-sm mb-2">No games found matching your filters.</p>
          <button onClick={() => { setSearchQuery(''); setSelectedCategory('All'); }} className="text-xs text-primary font-semibold hover:underline">
            Reset filters
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
          {displayedGames.map((game) => (
            <div
              key={game.id}
              className={`group relative rounded-2xl bg-card border border-border/80 p-5 flex flex-col justify-between transition-all duration-300 hover:border-primary/30 hover:shadow-[0_4px_30px_rgba(0,229,255,0.02)] overflow-hidden`}
            >
              {/* Background Glow on Hover */}
              <div className="absolute -inset-px bg-gradient-to-r from-primary/10 to-transparent opacity-0 group-hover:opacity-100 transition-opacity duration-500 rounded-2xl pointer-events-none" />

              <div>
                {/* Top Row: Icon & Category */}
                <div className="flex items-center justify-between mb-4">
                  <span className="text-xs font-semibold uppercase tracking-wider px-2.5 py-1 rounded bg-secondary border border-border text-muted-foreground">
                    {game.category}
                  </span>
                  <span className="text-3xl" role="img" aria-label={game.name}>
                    {game.bannerImage}
                  </span>
                </div>

                {/* Title */}
                <h3 className="text-lg font-bold text-white group-hover:text-primary transition-colors mb-2">
                  {game.name}
                </h3>

                {/* Description */}
                <p className="text-xs text-muted-foreground leading-relaxed mb-5 min-h-[48px] line-clamp-3">
                  {game.description}
                </p>

                {/* Hardware Spec Badges */}
                <div className="grid grid-cols-3 gap-2 bg-secondary/30 border border-border/40 rounded-xl p-3 mb-5">
                  <div className="flex flex-col items-center justify-center text-center">
                    <Cpu className="h-3.5 w-3.5 text-primary/80 mb-1" />
                    <span className="text-[10px] text-muted-foreground">CPU</span>
                    <span className="text-xs font-mono font-bold text-white">{game.cpu} vCPUs</span>
                  </div>
                  <div className="flex flex-col items-center justify-center text-center border-x border-border/40">
                    <HardDrive className="h-3.5 w-3.5 text-primary/80 mb-1" />
                    <span className="text-[10px] text-muted-foreground">RAM</span>
                    <span className="text-xs font-mono font-bold text-white">{game.ram} GB</span>
                  </div>
                  <div className="flex flex-col items-center justify-center text-center">
                    <Users className="h-3.5 w-3.5 text-primary/80 mb-1" />
                    <span className="text-[10px] text-muted-foreground">Slots</span>
                    <span className="text-xs font-mono font-bold text-white">{game.slots} Max</span>
                  </div>
                </div>
              </div>

              {/* Bottom Row: Price & Order Button */}
              <div className="flex items-center justify-between border-t border-border/40 pt-4 mt-auto relative z-10">
                <div>
                  <span className="text-[10px] text-muted-foreground block">Starting at</span>
                  <div className="flex items-baseline gap-0.5">
                    <span className="text-lg font-extrabold text-white">${game.pricePerMonth}</span>
                    <span className="text-[10px] text-muted-foreground">/mo</span>
                  </div>
                </div>

                <button
                  onClick={() => handleOpenDeploy(game)}
                  className="flex items-center gap-1 text-xs font-bold text-primary-foreground bg-primary px-4 py-2 rounded-lg hover:shadow-[0_0_15px_var(--primary)] hover:scale-[1.02] active:scale-[0.98] transition-all"
                >
                  Order
                  <ArrowRight className="h-3 w-3" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Deployment Modal Overlay */}
      {selectedGame && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-sm overflow-y-auto">
          <div className="relative w-full max-w-lg rounded-2xl bg-card border border-border p-6 shadow-2xl animate-slide-up my-8">
            
            {/* Close Button */}
            <button
              onClick={handleCloseDeploy}
              className="absolute top-4 right-4 p-2 text-muted-foreground hover:text-white hover:bg-secondary rounded-lg transition-colors"
              aria-label="Close modal"
            >
              <X className="h-5 w-5" />
            </button>

            {/* Modal Title */}
            <div className="flex items-center gap-3 mb-6">
              <span className="text-3xl">{selectedGame.bannerImage}</span>
              <div>
                <h3 className="text-lg font-bold text-white">Deploy {selectedGame.name}</h3>
                <p className="text-xs text-muted-foreground">Configure your virtual game server environment</p>
              </div>
            </div>

            {deploySuccess ? (
              /* Deploy Success State */
              <div className="text-center py-12 flex flex-col items-center justify-center">
                <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500 flex items-center justify-center text-emerald-400 mb-4 animate-bounce">
                  <Check className="h-8 w-8" />
                </div>
                <h4 className="text-xl font-bold text-white mb-2">Server Provisioned!</h4>
                <p className="text-xs text-muted-foreground max-w-sm">
                  We are allocating hardware cores and spinning up the container. Redirecting to your control panel...
                </p>
              </div>
            ) : showAuthForm ? (
              /* Inline Login Form (Auth Guard) */
              <form onSubmit={handleInlineLogin} className="space-y-4">
                <div className="bg-secondary/40 border border-border/60 rounded-xl p-4 mb-4">
                  <h4 className="text-xs font-bold uppercase tracking-wider text-primary mb-1">Authentication Required</h4>
                  <p className="text-xs text-muted-foreground">
                    To deploy servers and manage nodes, you need an AetherNode account. Register or sign in instantly below.
                  </p>
                </div>

                <div className="space-y-3">
                  <div>
                    <label className="text-xs font-semibold text-muted-foreground block mb-1.5">Email Address</label>
                    <input
                      type="email"
                      required
                      placeholder="you@example.com"
                      value={loginEmail}
                      onChange={(e) => setLoginEmail(e.target.value)}
                      className="w-full bg-secondary border border-border text-sm rounded-lg px-3 py-2 outline-none focus:border-primary/50 text-white"
                    />
                  </div>
                  <div>
                    <label className="text-xs font-semibold text-muted-foreground block mb-1.5">Password</label>
                    <input
                      type="password"
                      required
                      placeholder="••••••••"
                      value={loginPassword}
                      onChange={(e) => setLoginPassword(e.target.value)}
                      className="w-full bg-secondary border border-border text-sm rounded-lg px-3 py-2 outline-none focus:border-primary/50 text-white"
                    />
                  </div>
                </div>

                <button
                  type="submit"
                  className="w-full py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-bold hover:shadow-[0_0_15px_var(--primary)] transition-all mt-4"
                >
                  Create Account & Continue
                </button>

                <div className="text-center pt-2">
                  <button
                    type="button"
                    onClick={() => {
                      // Quick login fallback
                      login('guest@aethernode.com');
                      setShowAuthForm(false);
                      setServerName(`Guest's ${selectedGame.name} Server`);
                    }}
                    className="text-xs text-muted-foreground hover:text-white transition-colors"
                  >
                    Or continue as <span className="text-primary hover:underline">One-Click Guest</span>
                  </button>
                </div>
              </form>
            ) : (
              /* Server Configuration Panel */
              <div className="space-y-5">
                {/* Server Name input */}
                <div>
                  <label className="text-xs font-semibold text-muted-foreground block mb-1.5">Server Instance Name</label>
                  <input
                    type="text"
                    placeholder="E.g., Survival Realm"
                    value={serverName}
                    onChange={(e) => setServerName(e.target.value)}
                    className="w-full bg-secondary border border-border text-sm rounded-lg px-3.5 py-2 outline-none focus:border-primary/50 text-white font-medium"
                  />
                </div>

                {/* Location Selection */}
                <div>
                  <label className="text-xs font-semibold text-muted-foreground block mb-1.5">Node Location</label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {LOCATIONS.map((loc) => (
                      <button
                        key={loc.name}
                        onClick={() => setSelectedLocation(loc.name)}
                        className={`flex items-center justify-between p-3 rounded-lg border text-left transition-all ${
                          selectedLocation === loc.name
                            ? 'bg-primary/5 border-primary text-white'
                            : 'bg-secondary/40 border-border/80 text-muted-foreground hover:bg-secondary hover:text-white'
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <MapPin className={`h-4 w-4 ${selectedLocation === loc.name ? 'text-primary' : 'text-muted-foreground'}`} />
                          <span className="text-xs font-semibold">{loc.name}</span>
                        </div>
                        <span className="text-[10px] font-mono opacity-80">{loc.ping}</span>
                      </button>
                    ))}
                  </div>
                </div>

                {/* RAM slider */}
                <div>
                  <div className="flex justify-between items-center mb-1.5">
                    <label className="text-xs font-semibold text-muted-foreground">RAM Allocation</label>
                    <span className="text-xs font-mono font-bold text-primary">{customRam} GB</span>
                  </div>
                  <input
                    type="range"
                    min={selectedGame.ram}
                    max={selectedGame.ram * 2}
                    step={1}
                    value={customRam}
                    onChange={(e) => setCustomRam(Number(e.target.value))}
                    className="w-full accent-primary bg-secondary h-1.5 rounded-lg cursor-pointer"
                  />
                  <div className="flex justify-between text-[10px] text-muted-foreground mt-1">
                    <span>Base: {selectedGame.ram} GB</span>
                    <span>Max Core: {selectedGame.ram * 2} GB</span>
                  </div>
                </div>

                {/* Resource Summary */}
                <div className="bg-secondary/40 border border-border/60 rounded-xl p-4 space-y-2">
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground">CPU Cores</span>
                    <span className="font-mono text-white font-semibold">{selectedGame.cpu} vCPUs (Dedicated)</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground">Storage</span>
                    <span className="font-mono text-white font-semibold">50 GB NVMe Gen4 SSD</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-muted-foreground">Network Bandwidth</span>
                    <span className="font-mono text-white font-semibold">1 Gbps Unmetered</span>
                  </div>
                  <div className="flex justify-between text-xs border-t border-border/40 pt-2 mt-2">
                    <span className="text-muted-foreground font-bold">Monthly Cost</span>
                    <span className="font-mono text-primary font-bold">
                      ${(selectedGame.pricePerMonth + (customRam - selectedGame.ram) * 1.5).toFixed(2)} /mo
                    </span>
                  </div>
                </div>

                {/* Submit Action */}
                <div className="flex gap-3 pt-2">
                  <button
                    onClick={handleCloseDeploy}
                    className="flex-1 py-2.5 rounded-lg bg-secondary hover:bg-muted border border-border text-sm font-semibold text-white transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleDeploy}
                    disabled={isDeploying}
                    className="flex-1 py-2.5 rounded-lg bg-primary text-primary-foreground text-sm font-bold hover:shadow-[0_0_15px_var(--primary)] transition-all flex items-center justify-center gap-2 disabled:opacity-50"
                  >
                    {isDeploying ? (
                      <>
                        <div className="w-4 h-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
                        Provisioning...
                      </>
                    ) : (
                      'Deploy Server'
                    )}
                  </button>
                </div>

                <div className="flex items-center justify-center gap-1.5 text-[10px] text-muted-foreground">
                  <Shield className="h-3 w-3 text-emerald-400" />
                  <span>Backed by our 99.99% Network SLA</span>
                </div>
              </div>
            )}

          </div>
        </div>
      )}
    </div>
  );
}
