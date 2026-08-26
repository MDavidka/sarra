import { NextRequest, NextResponse } from "next/server";
import { sessionCookie, verifySession } from "../../../lib/access";

const base = process.env.SYTE_SHARE_API_BASE;
const instance = process.env.SYTE_SHARE_INSTANCE_ID;
const key = process.env.SYTE_SHARE_INSTANCE_KEY;

export async function POST(request: NextRequest) {
  if (!verifySession(request.cookies.get(sessionCookie)?.value)) return NextResponse.json({ error: "Sign in to use the project terminal." }, { status: 401 });
  const { command } = await request.json().catch(() => ({}));
  if (!['status', 'logs', 'health'].includes(command)) return NextResponse.json({ error: "Use one of: status, logs, health." }, { status: 400 });
  if (!base || !instance || !key) return NextResponse.json({ error: "Hosted workspace configuration is unavailable." }, { status: 503 });
  const response = await fetch(`${base}/api/share/instances/${instance}/terminal`, { method: "POST", headers: { "content-type": "application/json", "x-share-instance-key": key }, body: JSON.stringify({ command }), cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  return NextResponse.json(data, { status: response.status });
}
