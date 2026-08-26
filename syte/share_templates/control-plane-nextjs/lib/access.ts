import { createHmac, timingSafeEqual } from "crypto";

const COOKIE = "control_plane_session";
const MAX_AGE = 60 * 60 * 12;

function secret() { return process.env.SYTE_SHARE_INSTANCE_KEY || ""; }
function sign(value: string) { return createHmac("sha256", secret()).update(value).digest("base64url"); }

export function createSession(instanceId: string) {
  const body = Buffer.from(JSON.stringify({ instanceId, exp: Date.now() + MAX_AGE * 1000 })).toString("base64url");
  return `${body}.${sign(body)}`;
}

export function verifySession(value?: string | null) {
  if (!value || !secret()) return false;
  const [body, signature] = value.split(".");
  if (!body || !signature) return false;
  const expected = sign(body);
  if (signature.length !== expected.length || !timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return false;
  try { return Number(JSON.parse(Buffer.from(body, "base64url").toString()).exp) > Date.now(); } catch { return false; }
}

export const sessionCookie = COOKIE;
export const sessionMaxAge = MAX_AGE;
