import { create } from 'zustand';

export interface Game {
  id: string;
  name: string;
  category: 'Survival' | 'Sandbox' | 'FPS' | 'Strategy' | 'RPG';
  icon: string;
  description: string;
  pricePerMonth: number;
  cpu: number; // vCPUs
  ram: number; // GB
  slots: number; // Max players
  accentColor: string;
  bgGradient: string;
  bannerImage: string;
}

export interface GameInstance {
  id: string;
  name: string;
  gameId: string;
  status: 'running' | 'stopped' | 'restarting';
  ipAddress: string;
  cpuUsage: number;
  ramUsage: number; // in MB
  maxRam: number; // in MB
  slotsUsed: number;
  maxSlots: number;
  uptime: number; // in seconds
  createdAt: string;
  location: string;
  port: number;
  consoleLogs: string[];
}

interface User {
  email: string;
  username: string;
}

interface GameStore {
  user: User | null;
  games: Game[];
  instances: GameInstance[];
  isLoading: boolean;
  login: (email: string) => void;
  logout: () => void;
  createInstance: (name: string, gameId: string, location: string, customRam?: number) => void;
  stopInstance: (id: string) => void;
  startInstance: (id: string) => void;
  restartInstance: (id: string) => void;
  deleteInstance: (id: string) => void;
  addConsoleLog: (id: string, log: string) => void;
  tickInstances: () => void;
}

const DEFAULT_GAMES: Game[] = [
  {
    id: 'minecraft',
    name: 'Minecraft Java & Bedrock',
    category: 'Sandbox',
    icon: 'Layers',
    description: 'Build, explore, and survive in the infinite blocky world. Supports custom mods, Spigot, Paper, and Forge.',
    pricePerMonth: 4.99,
    cpu: 2,
    ram: 4,
    slots: 20,
    accentColor: '#4caf50',
    bgGradient: 'from-green-950/40 to-emerald-900/10',
    bannerImage: '⛏️'
  },
  {
    id: 'palworld',
    name: 'Palworld',
    category: 'Survival',
    icon: 'Flame',
    description: 'Fight, farm, build, and work alongside mysterious creatures called "Pals" in this multiplayer survival adventure.',
    pricePerMonth: 12.99,
    cpu: 4,
    ram: 12,
    slots: 32,
    accentColor: '#00b4d8',
    bgGradient: 'from-cyan-950/40 to-blue-900/10',
    bannerImage: '🐾'
  },
  {
    id: 'rust',
    name: 'Rust',
    category: 'Survival',
    icon: 'Hammer',
    description: 'The ultimate survival game. Overcome struggles such as hunger, thirst, and cold. Build bases and raid rivals.',
    pricePerMonth: 14.99,
    cpu: 4,
    ram: 16,
    slots: 100,
    accentColor: '#f44336',
    bgGradient: 'from-red-950/40 to-orange-900/10',
    bannerImage: '⚙️'
  },
  {
    id: 'ark-survival',
    name: 'ARK: Survival Ascended',
    category: 'Survival',
    icon: 'Skull',
    description: 'Respawn in a new dinosaur survival experience beyond your wildest dreams. Tame, breed, and conquer.',
    pricePerMonth: 18.99,
    cpu: 6,
    ram: 16,
    slots: 70,
    accentColor: '#ff9800',
    bgGradient: 'from-amber-950/40 to-amber-900/10',
    bannerImage: '🦖'
  },
  {
    id: 'cs2',
    name: 'Counter-Strike 2',
    category: 'FPS',
    icon: 'Target',
    description: 'Deploy high-tick CS2 servers with sub-millisecond network latency, custom maps, and full workshop support.',
    pricePerMonth: 8.99,
    cpu: 2,
    ram: 6,
    slots: 12,
    accentColor: '#e0a96d',
    bgGradient: 'from-yellow-950/40 to-amber-900/10',
    bannerImage: '💥'
  },
  {
    id: 'valheim',
    name: 'Valheim',
    category: 'Survival',
    icon: 'Shield',
    description: 'A brutal exploration and survival game for 1-10 players, set in a procedurally-generated purgatory inspired by Viking culture.',
    pricePerMonth: 6.99,
    cpu: 2,
    ram: 8,
    slots: 10,
    accentColor: '#2196f3',
    bgGradient: 'from-blue-950/40 to-indigo-900/10',
    bannerImage: '🛡️'
  },
  {
    id: 'enshrouded',
    name: 'Enshrouded',
    category: 'RPG',
    icon: 'Sparkles',
    description: 'You are Flameborn, last ember of hope of a dying race. Awaken, survive the Shroud, and rebuild a lost kingdom.',
    pricePerMonth: 11.99,
    cpu: 4,
    ram: 12,
    slots: 16,
    accentColor: '#9c27b0',
    bgGradient: 'from-purple-950/40 to-violet-900/10',
    bannerImage: '🔮'
  },
  {
    id: 'satisfactory',
    name: 'Satisfactory',
    category: 'Strategy',
    icon: 'Wrench',
    description: 'An open-world first-person factory building game with a dash of exploration and combat. Create massive conveyor belt webs.',
    pricePerMonth: 9.99,
    cpu: 4,
    ram: 10,
    slots: 8,
    accentColor: '#00e676',
    bgGradient: 'from-green-950/40 to-teal-900/10',
    bannerImage: '🏭'
  },
  {
    id: 'terraria',
    name: 'Terraria',
    category: 'Sandbox',
    icon: 'Trees',
    description: 'Dig, Fight, Explore, Build! Nothing is impossible in this action-packed adventure sandbox game.',
    pricePerMonth: 3.99,
    cpu: 1,
    ram: 2,
    slots: 16,
    accentColor: '#e91e63',
    bgGradient: 'from-pink-950/40 to-rose-900/10',
    bannerImage: '🌳'
  },
  {
    id: 'factorio',
    name: 'Factorio',
    category: 'Strategy',
    icon: 'Cpu',
    description: 'Build and maintain factories. Mine resources, research technologies, build infrastructure, automate production, and fight enemies.',
    pricePerMonth: 5.99,
    cpu: 2,
    ram: 4,
    slots: 40,
    accentColor: '#795548',
    bgGradient: 'from-amber-950/40 to-stone-900/10',
    bannerImage: '🚂'
  }
];

const LOCATIONS = [
  { code: 'FRA', name: 'Frankfurt, DE', ip: '142.250.181.' },
  { code: 'NYC', name: 'New York, USA', ip: '192.178.22.' },
  { code: 'SGP', name: 'Singapore, SG', ip: '111.223.45.' },
  { code: 'LND', name: 'London, UK', ip: '185.122.90.' },
  { code: 'SFO', name: 'San Francisco, USA', ip: '104.244.42.' }
];

const MOCK_LOG_TEMPLATES = [
  "Connection established with master cluster [node-04a]",
  "Loading world database...",
  "World save completed successfully in 142ms",
  "Garbage Collector: freed 452MB of heap memory",
  "Player {username} joined the server (IP: {ip})",
  "Player {username} left the server (Disconnect requested by client)",
  "Ping check: average latency 12.4ms, jitter 1.1ms",
  "Auto-saving game state... Done.",
  "Tickrate stabilized at 20.0 TPS (100% thread efficiency)",
  "Warning: High memory usage detected on chunk load (Temporary)",
  "Server config synced with API gateway",
  "Backup service: backup-archive-latest.tar.gz uploaded to remote storage"
];

const MOCK_USERNAMES = [
  "ShadowSlayer", "pixel_king", "GamerPro99", "AlphaViking", "NeonBlade",
  "CyberPanda", "VoidWalker", "LootGoblin", "QuantumCreeper", "AetherKnight"
];

const generateIp = (locationName: string) => {
  const loc = LOCATIONS.find(l => l.name === locationName) || LOCATIONS[0];
  const lastOctet = Math.floor(Math.random() * 254) + 1;
  return `${loc.ip}${lastOctet}`;
};

const getInitialInstances = (): GameInstance[] => [
  {
    id: 'inst-1',
    name: 'Survival World Pro',
    gameId: 'minecraft',
    status: 'running',
    ipAddress: '142.250.181.12',
    cpuUsage: 18,
    ramUsage: 2150,
    maxRam: 4096,
    slotsUsed: 4,
    maxSlots: 20,
    uptime: 145220,
    createdAt: new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString(),
    location: 'Frankfurt, DE',
    port: 25565,
    consoleLogs: [
      "[08:12:44 INFO]: Starting minecraft server version 1.21",
      "[08:12:45 INFO]: Loading properties",
      "[08:12:45 INFO]: Default game type: SURVIVAL",
      "[08:12:46 INFO]: Generating keypair",
      "[08:12:46 INFO]: Starting Minecraft server on 142.250.181.12:25565",
      "[08:12:48 INFO]: Preparing level \"world\"",
      "[08:12:52 INFO]: Preparing start region for dimension minecraft:overworld",
      "[08:12:55 INFO]: Time elapsed: 3122 ms",
      "[08:12:55 INFO]: Done (8.411s)! For help, type \"help\"",
      "[10:14:22 INFO]: Player ShadowSlayer joined the game",
      "[10:15:01 INFO]: Player pixel_king joined the game",
      "[11:42:15 INFO]: World save completed successfully",
      "[14:30:22 INFO]: Player GamerPro99 joined the game",
      "[15:10:05 INFO]: Player AlphaViking joined the game"
    ]
  },
  {
    id: 'inst-2',
    name: 'Palworld Co-Op',
    gameId: 'palworld',
    status: 'running',
    ipAddress: '192.178.22.84',
    cpuUsage: 34,
    ramUsage: 6480,
    maxRam: 12288,
    slotsUsed: 6,
    maxSlots: 32,
    uptime: 82300,
    createdAt: new Date(Date.now() - 3 * 24 * 3600 * 1000).toISOString(),
    location: 'New York, USA',
    port: 8211,
    consoleLogs: [
      "[00:01:10] [AetherNode Server Manager] Initializing Palworld Server Container...",
      "[00:01:12] [Palworld] Server started successfully.",
      "[00:01:12] [Palworld] Listening on port 8211.",
      "[00:01:15] [Palworld] Master server registry check: OK.",
      "[00:05:22] [Palworld] Client connection accepted: 192.168.1.102",
      "[00:05:25] [Palworld] Player NeonBlade authenticated with server.",
      "[01:12:40] [Palworld] Autosaving world state...",
      "[01:12:41] [Palworld] Save finished."
    ]
  },
  {
    id: 'inst-3',
    name: 'Rust PVP Arena',
    gameId: 'rust',
    status: 'stopped',
    ipAddress: '111.223.45.210',
    cpuUsage: 0,
    ramUsage: 0,
    maxRam: 16384,
    slotsUsed: 0,
    maxSlots: 100,
    uptime: 0,
    createdAt: new Date(Date.now() - 1 * 24 * 3600 * 1000).toISOString(),
    location: 'Singapore, SG',
    port: 28015,
    consoleLogs: [
      "[Server] Server shut down requested.",
      "[Server] Saving world before exit...",
      "[Server] World saved.",
      "[Server] Server stopped.",
      "[AetherNode] Server instance stopped by owner."
    ]
  }
];

export const useGameStore = create<GameStore>((set, get) => {
  // Initialize from localStorage safely
  const getStoredUser = () => {
    if (typeof window === 'undefined') return null;
    const stored = localStorage.getItem('aethernode_user');
    return stored ? JSON.parse(stored) : null;
  };

  const getStoredInstances = () => {
    if (typeof window === 'undefined') return getInitialInstances();
    const stored = localStorage.getItem('aethernode_instances');
    return stored ? JSON.parse(stored) : getInitialInstances();
  };

  return {
    user: getStoredUser(),
    games: DEFAULT_GAMES,
    instances: getStoredInstances(),
    isLoading: false,

    login: (email: string) => {
      const username = email.split('@')[0];
      const newUser = { email, username: username.charAt(0).toUpperCase() + username.slice(1) };
      set({ user: newUser });
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_user', JSON.stringify(newUser));
      }
    },

    logout: () => {
      set({ user: null });
      if (typeof window !== 'undefined') {
        localStorage.removeItem('aethernode_user');
      }
    },

    createInstance: (name: string, gameId: string, location: string, customRam?: number) => {
      const game = get().games.find(g => g.id === gameId);
      if (!game) return;

      const newId = `inst-${Math.floor(Math.random() * 1000000)}`;
      const port = gameId === 'minecraft' ? 25565 : Math.floor(Math.random() * 10000) + 10000;
      const ramInMb = (customRam || game.ram) * 1024;
      const ip = generateIp(location);

      const newInstance: GameInstance = {
        id: newId,
        name: name || `${game.name} Server`,
        gameId,
        status: 'running',
        ipAddress: ip,
        cpuUsage: 12,
        ramUsage: Math.floor(ramInMb * 0.3), // starts at 30% usage
        maxRam: ramInMb,
        slotsUsed: 0,
        maxSlots: game.slots,
        uptime: 5,
        createdAt: new Date().toISOString(),
        location,
        port,
        consoleLogs: [
          `[AetherNode Server Manager] Provisioning resources for ${game.name} ...`,
          `[AetherNode Server Manager] Attaching virtual network interface... IP is ${ip}:${port}`,
          `[AetherNode Server Manager] Allocating ${customRam || game.ram}GB RAM and ${game.cpu} vCPUs ...`,
          `[AetherNode Server Manager] Launching game container...`,
          `[Server] Booting game engine...`,
          `[Server] Server listening on ${ip}:${port}`,
          `[Server] Server status: ONLINE`
        ]
      };

      const updatedInstances = [...get().instances, newInstance];
      set({ instances: updatedInstances });
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_instances', JSON.stringify(updatedInstances));
      }
    },

    stopInstance: (id: string) => {
      const updatedInstances = get().instances.map(inst => {
        if (inst.id === id) {
          return {
            ...inst,
            status: 'stopped' as const,
            cpuUsage: 0,
            ramUsage: 0,
            uptime: 0,
            slotsUsed: 0,
            consoleLogs: [
              ...inst.consoleLogs,
              `[Server] Received stop signal. Commencing graceful shutdown...`,
              `[Server] Saving world state...`,
              `[Server] Save complete. Closing database connections...`,
              `[Server] Server offline.`,
              `[AetherNode Server Manager] Container stopped.`
            ]
          };
        }
        return inst;
      });
      set({ instances: updatedInstances });
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_instances', JSON.stringify(updatedInstances));
      }
    },

    startInstance: (id: string) => {
      const updatedInstances = get().instances.map(inst => {
        if (inst.id === id) {
          const game = get().games.find(g => g.id === inst.gameId);
          const initialRam = inst.maxRam * 0.3;
          return {
            ...inst,
            status: 'running' as const,
            cpuUsage: 15,
            ramUsage: Math.floor(initialRam),
            uptime: 1,
            consoleLogs: [
              ...inst.consoleLogs,
              `[AetherNode Server Manager] Booting container ${id}...`,
              `[Server] Initializing game engine...`,
              `[Server] Loading assets and world file...`,
              `[Server] Server online on ${inst.ipAddress}:${inst.port}`
            ]
          };
        }
        return inst;
      });
      set({ instances: updatedInstances });
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_instances', JSON.stringify(updatedInstances));
      }
    },

    restartInstance: (id: string) => {
      // Set to restarting
      const restartingInstances = get().instances.map(inst => {
        if (inst.id === id) {
          return {
            ...inst,
            status: 'restarting' as const,
            cpuUsage: 5,
            ramUsage: Math.floor(inst.maxRam * 0.1),
            uptime: 0,
            consoleLogs: [
              ...inst.consoleLogs,
              `[AetherNode Server Manager] Triggering system restart...`,
              `[Server] Shutting down current processes...`,
              `[Server] Saving world data...`,
              `[Server] Restarting game loop...`
            ]
          };
        }
        return inst;
      });
      set({ instances: restartingInstances });

      // Simulate boot up after 2.5 seconds
      setTimeout(() => {
        const afterRestartInstances = get().instances.map(inst => {
          if (inst.id === id) {
            return {
              ...inst,
              status: 'running' as const,
              cpuUsage: 12,
              ramUsage: Math.floor(inst.maxRam * 0.35),
              consoleLogs: [
                ...inst.consoleLogs,
                `[Server] System reboot successful.`,
                `[Server] Re-allocating dynamic ports...`,
                `[Server] Game server is now ONLINE.`
              ]
            };
          }
          return inst;
        });
        set({ instances: afterRestartInstances });
        if (typeof window !== 'undefined') {
          localStorage.setItem('aethernode_instances', JSON.stringify(afterRestartInstances));
        }
      }, 2500);
    },

    deleteInstance: (id: string) => {
      const updatedInstances = get().instances.filter(inst => inst.id !== id);
      set({ instances: updatedInstances });
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_instances', JSON.stringify(updatedInstances));
      }
    },

    addConsoleLog: (id: string, log: string) => {
      const updatedInstances = get().instances.map(inst => {
        if (inst.id === id) {
          // Limit logs to last 100 entries to prevent memory leak
          const newLogs = [...inst.consoleLogs, log];
          if (newLogs.length > 100) {
            newLogs.shift();
          }
          return { ...inst, consoleLogs: newLogs };
        }
        return inst;
      });
      set({ instances: updatedInstances });
    },

    tickInstances: () => {
      const updatedInstances = get().instances.map(inst => {
        if (inst.status !== 'running') return inst;

        // Increment uptime
        const newUptime = inst.uptime + 3;

        // Fluctuating stats
        const randomFactor = Math.random() - 0.5; // -0.5 to 0.5
        const cpuChange = Math.floor(randomFactor * 10);
        const newCpu = Math.max(5, Math.min(95, inst.cpuUsage + cpuChange));

        const ramChange = Math.floor(randomFactor * 150);
        const newRam = Math.max(Math.floor(inst.maxRam * 0.2), Math.min(Math.floor(inst.maxRam * 0.85), inst.ramUsage + ramChange));

        // Randomly connect or disconnect players
        let newSlots = inst.slotsUsed;
        let logMessage: string | null = null;

        if (Math.random() < 0.15) { // 15% chance of player activity
          const isJoin = Math.random() > 0.4; // 60% join, 40% leave
          if (isJoin && inst.slotsUsed < inst.maxSlots) {
            newSlots += 1;
            const username = MOCK_USERNAMES[Math.floor(Math.random() * MOCK_USERNAMES.length)];
            const ip = `192.168.1.${Math.floor(Math.random() * 254) + 1}`;
            logMessage = `[${new Date().toLocaleTimeString()}] [Server INFO]: Player ${username} connected from IP ${ip}`;
          } else if (!isJoin && inst.slotsUsed > 0) {
            newSlots -= 1;
            const username = MOCK_USERNAMES[Math.floor(Math.random() * MOCK_USERNAMES.length)];
            logMessage = `[${new Date().toLocaleTimeString()}] [Server INFO]: Player ${username} disconnected (Time out)`;
          }
        }

        // Random system logs
        if (!logMessage && Math.random() < 0.25) {
          const logTemplate = MOCK_LOG_TEMPLATES[Math.floor(Math.random() * MOCK_LOG_TEMPLATES.length)];
          const username = MOCK_USERNAMES[Math.floor(Math.random() * MOCK_USERNAMES.length)];
          const ip = `192.168.1.${Math.floor(Math.random() * 254) + 1}`;
          
          const formattedLog = logTemplate
            .replace("{username}", username)
            .replace("{ip}", ip);
            
          logMessage = `[${new Date().toLocaleTimeString()}] [System]: ${formattedLog}`;
        }

        const newLogs = logMessage ? [...inst.consoleLogs, logMessage] : inst.consoleLogs;
        if (newLogs.length > 100) {
          newLogs.shift();
        }

        return {
          ...inst,
          uptime: newUptime,
          cpuUsage: newCpu,
          ramUsage: newRam,
          slotsUsed: newSlots,
          consoleLogs: newLogs
        };
      });

      set({ instances: updatedInstances });
      // Only persist to localStorage occasionally or during state changes, but let's persist here for consistency.
      if (typeof window !== 'undefined') {
        localStorage.setItem('aethernode_instances', JSON.stringify(updatedInstances));
      }
    }
  };
});
