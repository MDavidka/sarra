const http = require("node:http");

const port = Number(process.env.PORT || 3000);
const instanceId = process.env.SYTE_SHARE_INSTANCE_ID || "";
const instanceKey = process.env.SYTE_SHARE_INSTANCE_KEY || "";
const apiBase = (process.env.SYTE_SHARE_API_BASE || "").replace(/\/$/, "");

function send(response, status, body, contentType = "application/json; charset=utf-8") {
  response.writeHead(status, { "content-type": contentType, "cache-control": "no-store" });
  response.end(typeof body === "string" ? body : JSON.stringify(body));
}

async function scopedOverview() {
  if (!instanceId || !instanceKey || !apiBase) throw new Error("Diagnostics has not been configured by Syte.");
  const response = await fetch(`${apiBase}/api/share/instances/${encodeURIComponent(instanceId)}`, {
    headers: { "x-share-instance-key": instanceKey, accept: "application/json" },
    signal: AbortSignal.timeout(8000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "The scoped project overview is unavailable.");
  const project = payload.project || {};
  return {
    checkedAt: new Date().toISOString(),
    project: {
      id: String(project.id || ""),
      name: String(project.name || "Hosted project"),
      domain: String(project.domain || "Not assigned"),
      status: String(project.status || "unknown"),
      running: Boolean(project.running),
      url: String(project.url || ""),
      variables: Number(project.variables || 0),
    },
  };
}

const page = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Diagnostics Beacon</title><style>
:root{color-scheme:dark;--bg:#0c0c0d;--surface:#151517;--line:#2d2d31;--text:#f3f3f4;--muted:#97979e;--ok:#a7f3d0;--accent:#e4e4e7}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(1020px,100%);margin:0 auto;padding:24px}.top{display:flex;align-items:center;justify-content:space-between;padding:5px 0 32px}.brand{display:flex;align-items:center;gap:9px;font-size:12px;font-weight:700;letter-spacing:.05em}.mark{display:grid;place-items:center;width:28px;height:28px;border:1px solid #45454b;border-radius:8px;font-size:10px}.badge{display:inline-flex;gap:7px;align-items:center;border:1px solid var(--line);border-radius:99px;padding:7px 10px;color:var(--muted);font-size:11px}.dot{width:7px;height:7px;border-radius:50%;background:#73737b}.dot.ok{background:#86efac;box-shadow:0 0 0 3px #86efac18}.hero{display:grid;grid-template-columns:1.15fr .85fr;gap:12px}.panel{border:1px solid var(--line);border-radius:14px;background:var(--surface);padding:28px}.eyebrow{margin:0 0 12px;color:var(--muted);font-size:10px;font-weight:700;letter-spacing:.12em}.hero h1{max-width:530px;margin:0;font-size:clamp(34px,5vw,64px);line-height:.98;letter-spacing:-.06em}.hero p{max-width:470px;margin:17px 0 0;color:var(--muted);font-size:13px;line-height:1.65}.status-card{display:flex;flex-direction:column;justify-content:space-between}.status-card strong{font-size:24px;letter-spacing:-.04em}.status-card small{color:var(--muted);font-size:11px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:12px}.metric{border:1px solid var(--line);border-radius:12px;padding:18px;background:#111113}.metric p{margin:0 0 9px;color:var(--muted);font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.metric b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:15px}.footer{display:flex;justify-content:space-between;gap:12px;padding:24px 0 0;color:#67676d;font-size:10px}.error{border-color:#7f1d1d}.error strong{color:#fecaca}@media(max-width:680px){.shell{padding:16px}.top{padding-bottom:22px}.hero{grid-template-columns:1fr}.panel{padding:22px}.grid{grid-template-columns:1fr}.status-card{min-height:140px}.footer{align-items:flex-start;flex-direction:column}}
</style></head><body><main class="shell"><header class="top"><div class="brand"><span class="mark">DB</span>DIAGNOSTICS BEACON</div><div class="badge"><span id="dot" class="dot"></span><span id="state">Checking service</span></div></header><section class="hero"><div class="panel"><p class="eyebrow">SYTE-HOSTED TEST TEMPLATE</p><h1>One clear signal for your hosted service.</h1><p>This generated template reads the bound project overview through a server-side scoped channel. Its platform credential is never included in this page or browser requests.</p></div><aside id="status-panel" class="panel status-card"><div><p class="eyebrow">SERVICE STATE</p><strong id="status">Loading</strong></div><small id="checked">Contacting scoped project overview…</small></aside></section><section class="grid"><article class="metric"><p>Project</p><b id="project">—</b></article><article class="metric"><p>Endpoint</p><b id="domain">—</b></article><article class="metric"><p>Environment</p><b id="variables">—</b></article></section><footer class="footer"><span>Server-side scoped access · Read-only diagnostics</span><span id="clock">—</span></footer></main><script>
async function refresh(){const state=document.querySelector('#state'),dot=document.querySelector('#dot'),panel=document.querySelector('#status-panel');try{const r=await fetch('/api/overview',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error||'Diagnostics unavailable');const p=d.project;document.querySelector('#status').textContent=p.running?'Running':'Not running';document.querySelector('#project').textContent=p.name||'Hosted project';document.querySelector('#domain').textContent=p.domain||'Not assigned';document.querySelector('#variables').textContent=String(p.variables||0)+' configured';document.querySelector('#checked').textContent='Project: '+(p.id||'unknown');document.querySelector('#clock').textContent='Checked '+new Date(d.checkedAt).toLocaleTimeString();state.textContent='Scoped signal ready';dot.classList.add('ok')}catch(error){panel.classList.add('error');document.querySelector('#status').textContent='Unavailable';document.querySelector('#checked').textContent='The scoped overview could not be loaded.';state.textContent='Needs attention';document.querySelector('#clock').textContent='Retry when the project is ready'}}refresh();setInterval(refresh,30000);
</script></body></html>`;

http.createServer(async (request, response) => {
  if (request.url === "/api/health") return send(response, 200, { status: "ok", service: "diagnostics-beacon" });
  if (request.url === "/api/overview") {
    try { return send(response, 200, await scopedOverview()); }
    catch (error) { return send(response, 502, { error: error instanceof Error ? error.message : "Diagnostics unavailable." }); }
  }
  if (request.url === "/" || request.url === "/index.html") return send(response, 200, page, "text/html; charset=utf-8");
  return send(response, 404, { error: "Not found" });
}).listen(port, "0.0.0.0", () => console.log(`Diagnostics Beacon listening on ${port}`));
