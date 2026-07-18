"""Scaffold starter projects for Sycord project_connect."""

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
    return writers[stack](app)


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
    "react-dom": "^18.3.0"
  },
  "devDependencies": {
    "typescript": "^5.4.0",
    "@types/node": "^20.0.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0"
  }
}
""",
        "next.config.mjs": "export default { output: 'standalone' };\n",
        "tsconfig.json": """{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "jsx": "preserve",
    "incremental": true
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx"],
  "exclude": ["node_modules"]
}
""",
        "app/layout.tsx": """export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: 'Inter, system-ui, sans-serif', margin: 0, padding: '2rem' }}>
        {children}
      </body>
    </html>
  );
}
""",
        "app/page.tsx": """export default function Home() {
  return (
    <main>
      <h1>Sycord + Syte</h1>
      <p>Next.js project connected via Sycord API. Edit files and POST /sycord/api/issue_deployment.</p>
    </main>
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
RUN npm run build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
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
