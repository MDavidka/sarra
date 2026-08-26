import { NextRequest, NextResponse } from "next/server";
import { createSession, sessionCookie, sessionMaxAge } from "../../../lib/access";

const base = process.env.SYTE_SHARE_API_BASE;
const instance = process.env.SYTE_SHARE_INSTANCE_ID;
const key = process.env.SYTE_SHARE_INSTANCE_KEY;

export async function POST(request: NextRequest) {
  const { password } = await request.json().catch(() => ({}));
  if (typeof password !== "string" || password.length < 12) return NextResponse.json({ error: "Enter the workspace access password." }, { status: 400 });
  if (!base || !instance || !key) return NextResponse.json({ error: "Hosted workspace configuration is unavailable." }, { status: 503 });
  const response = await fetch(`${base}/api/share/instances/${instance}/access/login`, {
    method: "POST", headers: { "content-type": "application/json", "x-share-instance-key": key }, body: JSON.stringify({ password }), cache: "no-store",
  });
  if (!response.ok) return NextResponse.json({ error: "Access was not granted. Configure the password in Share It or try again." }, { status: response.status === 401 ? 401 : 503 });
  const reply = NextResponse.json({ ok: true });
  reply.cookies.set(sessionCookie, createSession(instance), { httpOnly: true, sameSite: "strict", secure: process.env.NODE_ENV === "production", maxAge: sessionMaxAge, path: "/" });
  return reply;
}

export async function PATCH(request: NextRequest) {
  const { currentPassword, newPassword } = await request.json().catch(() => ({}));
  if (typeof currentPassword !== "string" || typeof newPassword !== "string" || newPassword.length < 12) return NextResponse.json({ error: "Provide the current password and a new password of at least 12 characters." }, { status: 400 });
  if (!base || !instance || !key) return NextResponse.json({ error: "Hosted workspace configuration is unavailable." }, { status: 503 });
  const response = await fetch(`${base}/api/share/instances/${instance}/access/rotate`, { method: "POST", headers: { "content-type": "application/json", "x-share-instance-key": key }, body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }), cache: "no-store" });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) return NextResponse.json({ error: data.detail || "Access password could not be changed." }, { status: response.status });
  return NextResponse.json({ ok: true, message: data.message || "Workspace access password changed." });
}

export async function DELETE() {
  const reply = NextResponse.json({ ok: true });
  reply.cookies.set(sessionCookie, "", { httpOnly: true, sameSite: "strict", maxAge: 0, path: "/" });
  return reply;
}
