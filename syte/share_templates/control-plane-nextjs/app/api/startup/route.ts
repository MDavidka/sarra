import { NextRequest, NextResponse } from "next/server";
import { sessionCookie, verifySession } from "../../../lib/access";

const base = process.env.SYTE_SHARE_API_BASE;
const instance = process.env.SYTE_SHARE_INSTANCE_ID;
const key = process.env.SYTE_SHARE_INSTANCE_KEY;
function authorized(request: NextRequest) { return verifySession(request.cookies.get(sessionCookie)?.value); }
function endpoint() { return `${base}/api/share/instances/${instance}/startup`; }

export async function GET(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "Sign in to access startup configuration." }, { status: 401 });
  if (!base || !instance || !key) return NextResponse.json({ error: "Hosted workspace configuration is unavailable." }, { status: 503 });
  const response = await fetch(endpoint(), { headers: { "x-share-instance-key": key }, cache: "no-store" });
  return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
}

export async function PATCH(request: NextRequest) {
  if (!authorized(request)) return NextResponse.json({ error: "Sign in to update startup configuration." }, { status: 401 });
  if (!base || !instance || !key) return NextResponse.json({ error: "Hosted workspace configuration is unavailable." }, { status: 503 });
  const body = await request.json().catch(() => ({}));
  const response = await fetch(endpoint(), { method: "PATCH", headers: { "content-type": "application/json", "x-share-instance-key": key }, body: JSON.stringify(body), cache: "no-store" });
  return NextResponse.json(await response.json().catch(() => ({})), { status: response.status });
}
