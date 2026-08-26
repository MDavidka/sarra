import { NextRequest, NextResponse } from "next/server";
import { sessionCookie, verifySession } from "../../../lib/access";

const base = process.env.SYTE_SHARE_API_BASE;
const instance = process.env.SYTE_SHARE_INSTANCE_ID;
const key = process.env.SYTE_SHARE_INSTANCE_KEY;
const headers = { "x-share-instance-key": key || "", "content-type": "application/json" };

function allowed(request: NextRequest) { return verifySession(request.cookies.get(sessionCookie)?.value); }
async function platform(path: string, init?: RequestInit) {
  if (!base || !instance || !key) throw new Error("Hosted instance configuration is unavailable.");
  const response = await fetch(`${base}/api/share/instances/${instance}${path}`, { ...init, headers: { ...headers, ...(init?.headers || {}) }, cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || "Platform request failed");
  return data;
}

export async function GET(request: NextRequest) {
  if (!allowed(request)) return NextResponse.json({ error: "Sign in to access this workspace." }, { status: 401 });
  try { return NextResponse.json(await platform("/overview")); } catch (error) { return NextResponse.json({ error: error instanceof Error ? error.message : "Configuration error" }, { status: 502 }); }
}

export async function POST(request: NextRequest) {
  if (!allowed(request)) return NextResponse.json({ error: "Sign in to manage this workspace." }, { status: 401 });
  try {
    const { action } = await request.json();
    if (!['start', 'stop', 'deploy'].includes(action)) return NextResponse.json({ error: "Unsupported action" }, { status: 400 });
    return NextResponse.json(await platform("/actions", { method: "POST", body: JSON.stringify({ action }) }));
  } catch (error) { return NextResponse.json({ error: error instanceof Error ? error.message : "Operation failed" }, { status: 502 }); }
}
