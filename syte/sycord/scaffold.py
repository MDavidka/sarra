"""Scaffold starter projects for Sycord project_connect.

The ``nextjs`` stack is the one Syte's design contract targets, so it ships a
complete Next.js App Router + Tailwind + shadcn/ui + Radix baseline. Emitting a
bare Next.js app (no Tailwind, no ``components/ui``, no design tokens) used to
force the agent to invent its own styling layer, which is where conflicting
files and off-contract UI kits crept in.
"""

from pathlib import Path

from syte.workspace import ensure_workspace

STACKS = ("nextjs", "python", "javascript", "html5")


def scaffold_project(project_id: str, stack: str) -> list[str]:
    stack = (stack or "nextjs").lower().strip()
    if stack not in STACKS:
        stack = "nextjs"
    app = ensure_workspace(project_id) / "app"
    app.mkdir(parents=True, exist_ok=True)
    writers = {
        "nextjs": _scaffold_nextjs,
        "python": _scaffold_python,
        "javascript": _scaffold_javascript,
        "html5": _scaffold_html5,
    }
    written = writers[stack](app)
    if stack == "nextjs":
        # A Next.js app and a static index.html / Vite entry cannot coexist:
        # preview auto-detection and `next build` both pick the wrong root.
        from syte.nextjs_layout import remove_conflicting_stack_files

        written.extend(remove_conflicting_stack_files(app))
    return written


def _scaffold_nextjs(app: Path) -> list[str]:
    files = {
        "package.json": """{
  "name": "sycord-next-app",
  "private": true,
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint"
  },
  "dependencies": {
    "next": "^14.2.0",
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "@radix-ui/react-slot": "^1.1.0",
    "class-variance-authority": "^0.7.0",
    "clsx": "^2.1.1",
    "lucide-react": "^0.441.0",
    "next-themes": "^0.3.0",
    "tailwind-merge": "^2.5.2",
    "tailwindcss-animate": "^1.0.7"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "@types/node": "^20.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "autoprefixer": "^10.4.20",
    "postcss": "^8.4.47",
    "tailwindcss": "^3.4.13"
  }
}
""",
        "next.config.mjs": """/** @type {import('next').NextConfig} */
const nextConfig = { output: 'standalone' };

export default nextConfig;
""",
        "tsconfig.json": """{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{ "name": "next" }],
    "paths": {
      "@/*": ["./*"]
    }
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
""",
        "postcss.config.mjs": """/** @type {import('postcss-load-config').Config} */
const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};

export default config;
""",
        "tailwind.config.ts": """import type { Config } from 'tailwindcss';

const config: Config = {
  darkMode: ['class'],
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    container: {
      center: true,
      padding: '1rem',
      screens: { '2xl': '72rem' },
    },
    extend: {
      fontFamily: {
        sans: ['var(--font-sans)', 'system-ui', 'sans-serif'],
      },
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
};

export default config;
""",
        "components.json": """{
  "$schema": "https://ui.shadcn.com/schema.json",
  "style": "default",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "tailwind.config.ts",
    "css": "app/globals.css",
    "baseColor": "slate",
    "cssVariables": true
  },
  "aliases": {
    "components": "@/components",
    "ui": "@/components/ui",
    "utils": "@/lib/utils",
    "lib": "@/lib",
    "hooks": "@/hooks"
  }
}
""",
        "lib/utils.ts": """import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
""",
        # Single source of truth for design tokens. --card differs from
        # --background in both modes so the design linter passes.
        "app/globals.css": """@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 222 47% 11%;
    --card: 210 40% 98%;
    --card-foreground: 222 47% 11%;
    --popover: 0 0% 100%;
    --popover-foreground: 222 47% 11%;
    --primary: 222 47% 11%;
    --primary-foreground: 210 40% 98%;
    --secondary: 210 40% 96%;
    --secondary-foreground: 222 47% 11%;
    --muted: 210 40% 96%;
    --muted-foreground: 215 16% 47%;
    --accent: 210 40% 94%;
    --accent-foreground: 222 47% 11%;
    --destructive: 0 72% 51%;
    --destructive-foreground: 210 40% 98%;
    --border: 214 32% 91%;
    --input: 214 32% 91%;
    --ring: 222 47% 11%;
    --radius: 0.625rem;
  }

  .dark {
    --background: 222 47% 7%;
    --foreground: 210 40% 98%;
    --card: 222 40% 11%;
    --card-foreground: 210 40% 98%;
    --popover: 222 40% 11%;
    --popover-foreground: 210 40% 98%;
    --primary: 210 40% 98%;
    --primary-foreground: 222 47% 11%;
    --secondary: 217 33% 17%;
    --secondary-foreground: 210 40% 98%;
    --muted: 217 33% 17%;
    --muted-foreground: 215 20% 65%;
    --accent: 217 33% 20%;
    --accent-foreground: 210 40% 98%;
    --destructive: 0 63% 41%;
    --destructive-foreground: 210 40% 98%;
    --border: 217 33% 20%;
    --input: 217 33% 20%;
    --ring: 213 27% 84%;
  }

  * {
    @apply border-border;
  }

  body {
    @apply bg-background text-foreground;
    font-family: var(--font-sans), system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }
}
""",
        "app/layout.tsx": """import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import { ThemeProvider } from '@/components/theme-provider';
import './globals.css';

const inter = Inter({ subsets: ['latin'], variable: '--font-sans' });

export const metadata: Metadata = {
  title: 'Sycord + Syte',
  description: 'Next.js App Router project with shadcn/ui and Tailwind CSS.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={inter.variable}>
        <ThemeProvider attribute="class" defaultTheme="system" enableSystem>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
""",
        "app/page.tsx": """import { ArrowRight, Rocket } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { ModeToggle } from '@/components/mode-toggle';

export default function Home() {
  return (
    <main className="min-h-dvh">
      <header className="flex items-center justify-between border-b border-border px-6 py-4">
        <span className="text-sm font-medium tracking-tight">Sycord</span>
        <ModeToggle />
      </header>

      <section className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-24">
        <p className="inline-flex w-fit items-center gap-2 rounded-full bg-card px-3 py-1 text-xs text-muted-foreground">
          <Rocket className="size-3.5" aria-hidden="true" />
          Connected via the Sycord API
        </p>
        <h1 className="text-4xl font-semibold tracking-tight sm:text-5xl">
          Your Next.js workspace is ready
        </h1>
        <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
          Tailwind CSS, design tokens, and shadcn/ui primitives are wired up. Replace this
          page with the real product — routes live in <code>app/</code> and UI primitives in
          <code> components/ui/</code>.
        </p>
        <div className="flex flex-wrap gap-3">
          <Button>
            Start building
            <ArrowRight className="size-4" aria-hidden="true" />
          </Button>
          <Button variant="outline">Read the design contract</Button>
        </div>
      </section>
    </main>
  );
}
""",
        # Radix stays behind this wrapper — application code imports the wrapper,
        # never @radix-ui/* directly (design contract rule).
        "components/ui/button.tsx": """import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { cn } from '@/lib/utils';

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: 'bg-primary text-primary-foreground hover:bg-primary/90',
        destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
        secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
        ghost: 'hover:bg-accent hover:text-accent-foreground',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'h-10 px-4 py-2',
        sm: 'h-9 rounded-md px-3',
        lg: 'h-11 rounded-md px-6',
        icon: 'size-10',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
""",
        "components/theme-provider.tsx": """'use client';

import * as React from 'react';
import { ThemeProvider as NextThemesProvider } from 'next-themes';

export function ThemeProvider({
  children,
  ...props
}: React.ComponentProps<typeof NextThemesProvider>) {
  return <NextThemesProvider {...props}>{children}</NextThemesProvider>;
}
""",
        "components/mode-toggle.tsx": """'use client';

import { Moon, Sun } from 'lucide-react';
import { useTheme } from 'next-themes';
import { Button } from '@/components/ui/button';

export function ModeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const isDark = resolvedTheme === 'dark';

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
      onClick={() => setTheme(isDark ? 'light' : 'dark')}
    >
      {isDark ? (
        <Sun className="size-4" aria-hidden="true" />
      ) : (
        <Moon className="size-4" aria-hidden="true" />
      )}
    </Button>
  );
}
""",
        "Dockerfile": """FROM node:20-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install

FROM node:20-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV HOSTNAME=0.0.0.0
ENV PORT=3000
EXPOSE 3000
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
CMD ["node", "server.js"]
""",
        "public/.gitkeep": "",
    }
    return _write_files(app, files)


def _scaffold_python(app: Path) -> list[str]:
    files = {
        "requirements.txt": "fastapi==0.115.0\nuvicorn[standard]==0.30.0\n",
        "main.py": """from fastapi import FastAPI

app = FastAPI(title="Sycord Python App")


@app.get("/")
def root():
    return {"ok": True, "message": "Sycord Python app on Syte"}
""",
        "Dockerfile": """FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=8000
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
""",
    }
    return _write_files(app, files)


def _scaffold_javascript(app: Path) -> list[str]:
    files = {
        "package.json": """{
  "name": "sycord-node-app",
  "private": true,
  "scripts": {
    "start": "node index.js"
  },
  "dependencies": {
    "express": "^4.19.0"
  }
}
""",
        "index.js": """const express = require('express');
const app = express();
const port = process.env.PORT || 3000;

app.get('/', (req, res) => {
  res.json({ ok: true, message: 'Sycord JavaScript app on Syte' });
});

app.listen(port, '0.0.0.0', () => console.log(`listening on ${port}`));
""",
        "Dockerfile": """FROM node:20-alpine
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install --production
COPY . .
ENV PORT=3000
EXPOSE 3000
CMD ["npm", "start"]
""",
    }
    return _write_files(app, files)


def _scaffold_html5(app: Path) -> list[str]:
    # Static stack only. Never mix this with a Next.js app in the same workspace —
    # scaffold_project() cleans conflicting entry points for the nextjs stack.
    files = {
        "index.html": """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Syte HTML App</title>
  <style>
    body { font-family: Inter, system-ui, sans-serif; margin: 0; padding: 2rem; }
    h1 { margin: 0 0 0.5rem; }
    p { color: #71717a; }
  </style>
</head>
<body>
  <h1>Syte HTML5</h1>
  <p>Static site on Syte — edit index.html and deploy.</p>
</body>
</html>
""",
        "Dockerfile": """FROM nginx:alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
""",
    }
    return _write_files(app, files)


def _write_files(app: Path, files: dict[str, str]) -> list[str]:
    written: list[str] = []
    for rel, content in files.items():
        path = app / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue
        path.write_text(content)
        written.append(f"app/{rel}")
    return written
