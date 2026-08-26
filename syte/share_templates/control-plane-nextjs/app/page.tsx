import ControlPlane from "./control-plane-client";

export const dynamic = "force-dynamic";

async function initialOverview() {
  const base = process.env.SYTE_SHARE_API_BASE;
  const instance = process.env.SYTE_SHARE_INSTANCE_ID;
  const key = process.env.SYTE_SHARE_INSTANCE_KEY;
  if (!base || !instance || !key) return null;
  try {
    const response = await fetch(`${base}/api/share/instances/${instance}/overview`, {
      headers: { "x-share-instance-key": key },
      cache: "no-store",
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

export default async function Page() {
  return <ControlPlane initialOverview={await initialOverview()} />;
}
