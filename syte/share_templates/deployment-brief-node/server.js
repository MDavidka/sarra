const http = require("http");

const port = Number(process.env.PORT || 3000);
const instanceId = process.env.SYTE_SHARE_INSTANCE_ID || "";
const instanceKey = process.env.SYTE_SHARE_INSTANCE_KEY || "";
const apiBase = (process.env.SYTE_SHARE_API_BASE || "").replace(/\/$/, "");

function send(response, status, body, contentType = "application/json; charset=utf-8") {
  response.writeHead(status, { "content-type": contentType, "cache-control": "no-store" });
  response.end(typeof body === "string" ? body : JSON.stringify(body));
}

async function scopedOverview() {
  if (!instanceId || !instanceKey || !apiBase) throw new Error("Deployment Brief has not been configured by Syte.");
  const response = await fetch(`${apiBase}/api/share/instances/${encodeURIComponent(instanceId)}/overview`, {
    headers: { "x-share-instance-key": instanceKey, accept: "application/json" },
    signal: AbortSignal.timeout(8000),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "The scoped deployment overview is unavailable.");
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
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Deployment Brief</title><style>
:root{--ink:#141619;--muted:#64707c;--line:#dbe2e8;--canvas:#f6f8fa;--card:#fff;--cyan:#08b7de;--cyan-soft:#e7f8fc;--ok:#19a870;--radius:18px}*{box-sizing:border-box}body{margin:0;background:var(--canvas);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{width:min(1120px,100%);margin:auto;padding:24px}.top{display:flex;align-items:center;justify-content:space-between;padding:0 0 30px}.brand{display:flex;align-items:center;gap:10px;font-weight:760;font-size:14px;letter-spacing:-.02em}.brand i{display:grid;place-items:center;width:29px;height:29px;border-radius:9px;background:var(--ink);color:#fff;font-style:normal;font-size:11px}.crumb{color:var(--muted);font-size:12px}.live{display:flex;align-items:center;gap:8px;border:1px solid #bfe9d7;background:#f3fcf8;color:#16714f;border-radius:999px;padding:7px 11px;font-size:11px;font-weight:700}.dot{width:7px;height:7px;border-radius:50%;background:#9aa5ae}.dot.ok{background:var(--ok);box-shadow:0 0 0 4px #19a87018}.layout{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(260px,.55fr);gap:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius)}.brief{padding:30px;min-height:340px;display:flex;flex-direction:column;justify-content:space-between}.eyebrow{margin:0 0 14px;color:#687783;font-size:10px;font-weight:800;letter-spacing:.14em}.brief h1{max-width:650px;margin:0;letter-spacing:-.055em;font-size:clamp(38px,5vw,65px);line-height:.96}.brief p{max-width:620px;margin:18px 0 0;color:var(--muted);font-size:14px;line-height:1.65}.release{display:flex;align-items:center;gap:12px;margin-top:26px;padding:14px 15px;background:var(--cyan-soft);border-radius:13px}.release strong{display:block;font-size:13px}.release span{color:#47707b;font-size:11px}.rail{padding:22px;display:flex;flex-direction:column;justify-content:space-between}.rail h2{margin:4px 0 0;font-size:21px;letter-spacing:-.04em}.rail dl{margin:22px 0 0;display:grid;gap:15px}.rail dl div{padding-top:14px;border-top:1px solid var(--line)}dt{color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.12em}dd{margin:7px 0 0;font-size:14px;font-weight:750;overflow-wrap:anywhere}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin-top:16px}.metric{padding:19px 20px}.metric p{margin:0;color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.12em}.metric b{display:block;margin-top:11px;font-size:16px;letter-spacing:-.025em;overflow-wrap:anywhere}.metric em{display:block;margin-top:5px;color:#75818b;font-style:normal;font-size:11px}.footer{display:flex;justify-content:space-between;gap:16px;padding:19px 2px 0;color:#7a8690;font-size:11px}.error{border-color:#f0b8b8;background:#fffafa}.error h2{color:#b42318}@media(max-width:720px){.shell{padding:16px}.top{padding-bottom:20px}.crumb{display:none}.layout{grid-template-columns:1fr}.brief{min-height:0;padding:24px}.brief h1{font-size:42px}.rail{min-height:260px}.metrics{grid-template-columns:1fr;gap:10px}.metric{padding:17px 18px}.footer{align-items:flex-start;flex-direction:column;padding-top:17px}}
</style></head><body><main class="shell"><header class="top"><div class="brand"><i>S</i><span>Syte</span><span class="crumb">/ deployment brief</span></div><div class="live"><span id="dot" class="dot"></span><span id="signal">Checking release</span></div></header><section class="layout"><article class="card brief"><div><p class="eyebrow">SYTE-HOSTED RELEASE VIEW</p><h1>Know what is live before you ship again.</h1><p>A concise, project-bound release brief for your hosted service. Status is refreshed through Syte’s server-side scoped channel.</p></div><div class="release"><span id="release-mark">↗</span><div><strong id="release-title">Checking deployment</strong><span id="release-copy">Contacting the bound project…</span></div></div></article><aside id="rail" class="card rail"><div><p class="eyebrow">CURRENT PROJECT</p><h2 id="name">Loading</h2><dl><div><dt>Runtime state</dt><dd id="state">—</dd></div><div><dt>Production endpoint</dt><dd id="endpoint">—</dd></div></dl></div><div class="crumb" id="project-id">Awaiting scoped overview</div></aside></section><section class="metrics"><article class="card metric"><p>Deployment</p><b id="deployment">—</b><em>Current platform state</em></article><article class="card metric"><p>Environment</p><b id="variables">—</b><em>Configured runtime values</em></article><article class="card metric"><p>Connection</p><b id="connection">—</b><em>Server-side scoped access</em></article></section><footer class="footer"><span>Syte native template · Read-only project brief</span><span id="checked">—</span></footer></main><script>
async function refresh(){const signal=document.querySelector('#signal'),dot=document.querySelector('#dot'),rail=document.querySelector('#rail');try{const response=await fetch('/api/overview',{cache:'no-store'});const data=await response.json();if(!response.ok)throw Error(data.error||'Overview unavailable');const p=data.project;const status=p.running?'Running':'Not running';document.querySelector('#name').textContent=p.name||'Hosted project';document.querySelector('#state').textContent=status;document.querySelector('#endpoint').textContent=p.domain||'Not assigned';document.querySelector('#project-id').textContent='Project · '+(p.id||'unknown');document.querySelector('#deployment').textContent=p.status||'Unknown';document.querySelector('#variables').textContent=String(p.variables||0)+' configured';document.querySelector('#connection').textContent='Ready';document.querySelector('#release-title').textContent=p.running?'Deployment is live':'Deployment needs attention';document.querySelector('#release-copy').textContent=p.running?'The bound project is serving normally.':'Review the project state in Syte.';document.querySelector('#checked').textContent='Updated '+new Date(data.checkedAt).toLocaleTimeString();signal.textContent='Scoped signal ready';dot.classList.add('ok')}catch(error){rail.classList.add('error');document.querySelector('#name').textContent='Unavailable';document.querySelector('#state').textContent='Needs attention';document.querySelector('#release-title').textContent='Scoped overview unavailable';document.querySelector('#release-copy').textContent='Retry when the project is ready.';signal.textContent='Needs attention';document.querySelector('#checked').textContent='No live data'}}refresh();setInterval(refresh,30000);
</script></body></html>`;

http.createServer(async (request, response) => {
  if (request.url === "/api/health") return send(response, 200, { status: "ok", service: "deployment-brief" });
  if (request.url === "/api/overview") {
    try { return send(response, 200, await scopedOverview()); }
    catch (error) { return send(response, 502, { error: error instanceof Error ? error.message : "Deployment Brief is unavailable." }); }
  }
  if (request.url === "/" || request.url === "/index.html") return send(response, 200, page, "text/html; charset=utf-8");
  return send(response, 404, { error: "Not found" });
}).listen(port, "0.0.0.0", () => console.log(`Deployment Brief listening on ${port}`));
