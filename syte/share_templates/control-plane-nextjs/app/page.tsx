import { cookies } from "next/headers";
import ControlPlane from "./control-plane-client";
import { sessionCookie, verifySession } from "../lib/access";

export const dynamic = "force-dynamic";

async function initialOverview(authenticated: boolean) {
  const base = process.env.SYTE_SHARE_API_BASE;
  const instance = process.env.SYTE_SHARE_INSTANCE_ID;
  const key = process.env.SYTE_SHARE_INSTANCE_KEY;
  if (!authenticated || !base || !instance || !key) return null;
  try {
    const response = await fetch(`${base}/api/share/instances/${instance}/overview`, { headers: { "x-share-instance-key": key }, cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch { return null; }
}

export default async function Page() {
  const store = await cookies();
  const authenticated = verifySession(store.get(sessionCookie)?.value);
  return <ControlPlane authenticated={authenticated} initialOverview={await initialOverview(authenticated)} />;
}
