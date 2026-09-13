#!/usr/bin/env python3
import json
import re
import sys
import os

sys.path.insert(0, "/root/syte")
from scripts.generate_full_docs import API_ENDPOINTS
from scripts.generate_full_docs_css import generate_css

def escape_html(s):
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def build():
    # 1. Update docs.css
    css_content = generate_css()
    with open("/root/syte/syte/static/pages/docs/docs.css", "r", encoding="utf-8") as f:
        existing_css = f.read()

    marker = "/* ==========================================================================\n   Fumadocs Modern API Dual-Card Layout Styles"
    if marker in existing_css:
        parts = existing_css.split(marker)
        updated_css = parts[0].rstrip() + "\n\n" + css_content.strip() + "\n"
    else:
        updated_css = existing_css.rstrip() + "\n\n" + css_content.strip() + "\n"

    with open("/root/syte/syte/static/pages/docs/docs.css", "w", encoding="utf-8") as f:
        f.write(updated_css)
    print("Updated docs.css")

    # 2. Group API endpoints by category
    categories = {}
    for ep in API_ENDPOINTS:
        cat = ep["group"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(ep)

    # 3. Generate Sidebar Navigation HTML for index.html
    with open("/root/syte/syte/static/index.html", "r", encoding="utf-8") as f:
        index_html = f.read()

    sidebar_api_groups_html = []
    cat_slug_map = {
        "System & Health": "api-system-health",
        "Notifications": "api-notifications",
        "Auth & Operator": "api-auth-op",
        "Settings & GitHub": "api-settings-gh",
        "SSL & Certificates": "api-ssl-certs",
        "Projects & Lifecycle": "api-projects-life",
        "Builds & Deployments": "api-builds-deploy",
        "Redirects & Routing": "api-redirects-route",
        "Analytics & Telemetry": "api-analytics-telem",
        "Release & Previews": "api-release-prev",
        "Git & Workspace Files": "api-git-workspace"
    }

    for cat_name, eps in categories.items():
        cat_slug = cat_slug_map.get(cat_name, "api-" + cat_name.lower().replace(" ", "-").replace("&", ""))
        items_html = []
        for ep in eps:
            m_cls = ep["method"].lower()
            items_html.append(f'                        <a class="docs-nav-item" data-docs-page="{ep["key"]}" onclick="showDocsPage(\'{ep["key"]}\')"><span class="docs-api-method-badge {m_cls}">{ep["method"]}</span><span>{escape_html(ep["title"])}</span></a>')
        items_joined = "\n".join(items_html)
        group_html = f"""                      <div class="docs-nav-parent" data-parent="{cat_slug}">
                        <span>{escape_html(cat_name)}</span>
                        <i data-lucide="chevron-right" class="docs-parent-chevron"></i>
                      </div>
                      <div class="docs-nav-subitems" data-subitems-for="{cat_slug}">
{items_joined}
                      </div>"""
        sidebar_api_groups_html.append(group_html)

    all_api_nav_html = "\n".join(sidebar_api_groups_html)

    # Match lines from `<div class="docs-nav-group-title">API Reference</div>` to `</div>\n                  </div>\n                </nav>`
    api_ref_pattern = re.compile(r'<div class="docs-nav-group">\s*<div class="docs-nav-group-title">API Reference</div>\s*<div class="docs-nav-links">.*?</div>\s*</div>\s*</nav>', re.DOTALL)
    
    new_api_group_chunk = f"""<div class="docs-nav-group">
                    <div class="docs-nav-group-title">API Reference ({len(API_ENDPOINTS)} Endpoints)</div>
                    <div class="docs-nav-links">
{all_api_nav_html}
                    </div>
                  </div>
                </nav>"""

    if api_ref_pattern.search(index_html):
        index_html = api_ref_pattern.sub(new_api_group_chunk, index_html)
        with open("/root/syte/syte/static/index.html", "w", encoding="utf-8") as f:
            f.write(index_html)
        print("Updated index.html with all 113 API endpoints in API Reference")
    else:
        print("ERROR: Could not find API Reference group in index.html")

    # 4. Generate JavaScript Data & Renderer in app.js
    with open("/root/syte/syte/static/app.js", "r", encoding="utf-8") as f:
        app_js = f.read()

    docs_data_start_idx = app_js.find("const DOCS_DATA = {")
    if docs_data_start_idx == -1:
        print("Could not find DOCS_DATA in app.js")
        return

    standard_guides = {
        "qs-install": {
            "title": "Quickstart & Installation",
            "lead": "Deploy, configure, and manage high-performance web applications and reverse proxy endpoints with Syte.",
            "hasHero": True,
            "updated": "03/09/2026",
            "content": """
      <p>Syte is a modern self-hosted deployment engine built for high-performance Node.js, Python, Static, and Docker applications with native SSL provisioning and edge routing.</p>
      
      <div class="docs-cmd-card">
        <div class="docs-cmd-header">
          <div class="docs-cmd-tabs">
            <button class="docs-cmd-tab active" onclick="switchCmdTab(this, 'npm')">npm</button>
            <button class="docs-cmd-tab" onclick="switchCmdTab(this, 'pnpm')">pnpm</button>
            <button class="docs-cmd-tab" onclick="switchCmdTab(this, 'yarn')">yarn</button>
            <button class="docs-cmd-tab" onclick="switchCmdTab(this, 'bun')">bun</button>
          </div>
          <button class="docs-cmd-copy-btn" onclick="copySnippet(this, 'npm install -g @syte/cli')" title="Copy command">
            <i data-lucide="clipboard" style="width:14px;height:14px;"></i>
          </button>
        </div>
        <div class="docs-cmd-body">
          <pre class="docs-cmd-snippet active" data-content="npm"><code>npm install -g @syte/cli</code></pre>
          <pre class="docs-cmd-snippet" data-content="pnpm"><code>pnpm add -g @syte/cli</code></pre>
          <pre class="docs-cmd-snippet" data-content="yarn"><code>yarn global add @syte/cli</code></pre>
          <pre class="docs-cmd-snippet" data-content="bun"><code>bun add -g @syte/cli</code></pre>
        </div>
      </div>

      <h2 class="docs-section-h2">Production Host Installation</h2>
      <p>Run the automated setup script on your Ubuntu/Debian Linux virtual machine:</p>
      <div class="docs-cmd-card">
        <div class="docs-cmd-header">
          <div class="docs-cmd-tabs"><button class="docs-cmd-tab active">bash</button></div>
          <button class="docs-cmd-copy-btn" onclick="copySnippet(this, 'curl -sSL https://get.syte.dev | bash')" title="Copy command">
            <i data-lucide="clipboard" style="width:14px;height:14px;"></i>
          </button>
        </div>
        <div class="docs-cmd-body">
          <pre class="docs-cmd-snippet active"><code>curl -sSL https://get.syte.dev | bash</code></pre>
        </div>
      </div>
            """
        },
        "qs-deploy": {
            "title": "Deploying Your First App",
            "lead": "Step-by-step instructions to import, build, and deploy your web app.",
            "updated": "03/09/2026",
            "content": """
      <p>Follow these steps to connect your Git repository and deploy in seconds:</p>
      <div class="docs-step-item">
        <div class="docs-step-num">1</div>
        <div class="docs-step-content">
          <h4>Connect Git or Upload ZIP</h4>
          <p>Navigate to the Projects dashboard and click <strong>Create Project</strong> or import directly from GitHub.</p>
        </div>
      </div>
      <div class="docs-step-item">
        <div class="docs-step-num">2</div>
        <div class="docs-step-content">
          <h4>Auto Framework Detection</h4>
          <p>Syte detects Next.js, Vite, Remix, Astro, FastAPI, Django, Express, and Dockerfile projects automatically.</p>
        </div>
      </div>
      <div class="docs-step-item">
        <div class="docs-step-num">3</div>
        <div class="docs-step-content">
          <h4>Live Production Deployment</h4>
          <p>Click <strong>Deploy</strong> to trigger automated container building and edge proxy configuration.</p>
        </div>
      </div>
            """
        },
        "qs-custom-domain": {
            "title": "Custom Domains & DNS",
            "lead": "Configure custom apex or subdomains with automated Let's Encrypt TLS certificates.",
            "updated": "03/09/2026",
            "content": """
      <p>Syte manages automated SSL provisioning through ACME HTTP-01 challenges.</p>
      <h2 class="docs-section-h2">1. Add DNS Record</h2>
      <p>In your DNS provider (Cloudflare, Namecheap, Route53), add a <code>CNAME</code> or <code>A</code> record:</p>
      <div class="docs-code-gray-card">
        <div class="docs-code-gray-header">
          <div class="docs-code-file-label"><span>DNS Settings</span></div>
        </div>
        <div class="docs-code-gray-body">
          <pre><code>Type: CNAME
Name: app
Target: cname.sycord.site
TTL: Auto / 300</code></pre>
        </div>
      </div>
      <h2 class="docs-section-h2">2. Bind Domain in Syte</h2>
      <p>Use the Project Settings &gt; Domain interface or the <code>/api/projects/{id}/domain</code> endpoint.</p>
            """
        },
        "core-architecture": {
            "title": "Platform Architecture",
            "lead": "Under the hood: Reverse proxying, isolated container runtimes, and distributed state.",
            "updated": "03/09/2026",
            "content": """
      <p>Syte combines a low-latency Caddy/Nginx reverse proxy layer with lightweight systemd-isolated container environments.</p>
      <ul>
        <li><strong>Proxy Layer:</strong> Handles SSL termination, gzip/brotli compression, and path redirects.</li>
        <li><strong>Runtime Daemon:</strong> FastAPI controller on port 8787 orchestrating build pipelines and system telemetry.</li>
        <li><strong>Process Isolation:</strong> Zero-overhead execution with per-project resource throttling and memory limits.</li>
      </ul>
            """
        },
        "core-config": {
            "title": "Configuration Reference",
            "lead": "Complete specification for syte.config.json and environment options.",
            "updated": "03/09/2026",
            "content": """
      <p>You can commit a <code>syte.config.json</code> file in your repository root to configure build steps:</p>
      <div class="docs-code-gray-card">
        <div class="docs-code-gray-header">
          <div class="docs-code-file-label"><span>syte.config.json</span></div>
        </div>
        <div class="docs-code-gray-body">
          <pre><code>{
  "framework": "nextjs",
  "buildCommand": "npm run build",
  "startCommand": "npm run start",
  "port": 3000,
  "environment": {
    "NODE_ENV": "production"
  }
}</code></pre>
        </div>
      </div>
            """
        },
        "core-ssl-security": {
            "title": "SSL & Security Hardening",
            "lead": "Automatic TLS renewal, HSTS headers, and rate limiting protections.",
            "updated": "03/09/2026",
            "content": """
      <p>Syte provides automated security policies out of the box:</p>
      <ul>
        <li>Automatic Let's Encrypt certificate renewal every 60 days.</li>
        <li>Native TLS 1.3 encryption with strict cipher suites.</li>
        <li>Built-in rate limiting and DDoS protection at edge proxy.</li>
      </ul>
            """
        },
        "core-git-sync": {
            "title": "Continuous Git Sync & Webhooks",
            "lead": "Automate deployments on git push and pull request preview environments.",
            "updated": "03/09/2026",
            "content": """
      <p>Syte listens for GitHub webhook events. When you push to your default branch, Syte pulls the latest commits, triggers a build, and performs a zero-downtime traffic swap.</p>
            """
        },
        "core-monitoring": {
            "title": "Monitoring & Telemetry",
            "lead": "Real-time metrics, HTTP status codes, p95 latency, and SSE log streaming.",
            "updated": "03/09/2026",
            "content": """
      <p>Every project provides real-time CPU, RAM, disk, and visitor analytics. You can also stream live container stdout logs using the SSE streaming endpoint.</p>
            """
        }
    }

    js_docs_data = {}
    for k, v in standard_guides.items():
        js_docs_data[k] = {
            "title": v["title"],
            "lead": v["lead"],
            "updated": v["updated"],
            "content": v["content"],
            "hasHero": v.get("hasHero", False),
            "isApi": False
        }

    for ep in API_ENDPOINTS:
        js_docs_data[ep["key"]] = {
            "isApi": True,
            "groupName": ep["group"],
            "title": ep["title"],
            "lead": ep["summary"],
            "summary": ep["summary"],
            "method": ep["method"],
            "path": ep["path"],
            "contentType": ep["contentType"],
            "pathParams": ep.get("pathParams", []),
            "queryParams": ep.get("queryParams", []),
            "bodyParams": ep.get("bodyParams", []),
            "curlCommand": ep["curlCommand"],
            "responseStatus": ep["responseStatus"],
            "responseSchema": ep.get("responseSchema", []),
            "responseJson": ep["responseJson"],
            "updated": "03/09/2026"
        }

    show_docs_page_js = """
function showDocsPage(pageKey) {
  activeDocsPage = pageKey;
  const data = DOCS_DATA[pageKey] || DOCS_DATA['qs-install'];

  // Update active sidebar nav item and auto-open only its parent category drawer
  document.querySelectorAll('.docs-nav-subitems').forEach(sub => sub.classList.remove('is-open'));
  document.querySelectorAll('.docs-nav-parent').forEach(p => p.classList.remove('is-open'));

  document.querySelectorAll('.docs-nav-item').forEach(item => {
    const isActive = item.dataset.docsPage === pageKey;
    item.classList.toggle('active', isActive);
    if (isActive) {
      const parentContainer = item.closest('.docs-nav-subitems');
      if (parentContainer) {
        parentContainer.classList.add('is-open');
        const parentHeader = parentContainer.previousElementSibling;
        if (parentHeader && parentHeader.classList.contains('docs-nav-parent')) {
          parentHeader.classList.add('is-open');
        }
      }
    }
  });

  const container = document.getElementById('docs-main-content');
  if (!container) return;

  if (data.isApi) {
    // Exact Dual-Card Layout matching media_1789305750389.png
    
    // Path Params HTML
    let pathParamsHtml = '';
    if (data.pathParams && data.pathParams.length > 0) {
      pathParamsHtml = `
        <div class="docs-api-section-subhead">PATH PARAMETERS</div>
        <div class="docs-api-params-table">
          ${data.pathParams.map(p => `
            <div class="docs-api-param-row">
              <div class="docs-api-param-meta">
                <span class="docs-api-param-name">${escapeHtml(p.name)}</span>
                <span class="docs-api-param-type">${escapeHtml(p.type)}</span>
                <span class="docs-api-param-badge ${p.required ? 'required' : 'optional'}">${p.required ? 'REQUIRED' : 'OPTIONAL'}</span>
              </div>
              <div class="docs-api-param-desc">${escapeHtml(p.desc)}</div>
            </div>
          `).join('')}
        </div>
      `;
    }

    // Query Params HTML
    let queryParamsHtml = '';
    if (data.queryParams && data.queryParams.length > 0) {
      queryParamsHtml = `
        <div class="docs-api-section-subhead">QUERY PARAMETERS</div>
        <div class="docs-api-params-table">
          ${data.queryParams.map(p => `
            <div class="docs-api-param-row">
              <div class="docs-api-param-meta">
                <span class="docs-api-param-name">${escapeHtml(p.name)}</span>
                <span class="docs-api-param-type">${escapeHtml(p.type)}</span>
                <span class="docs-api-param-badge ${p.required ? 'required' : 'optional'}">${p.required ? 'REQUIRED' : 'OPTIONAL'}</span>
              </div>
              <div class="docs-api-param-desc">${escapeHtml(p.desc)}</div>
            </div>
          `).join('')}
        </div>
      `;
    }

    // Body Params HTML
    let bodyParamsHtml = '';
    if (data.bodyParams && data.bodyParams.length > 0) {
      bodyParamsHtml = `
        <div class="docs-api-section-subhead">REQUEST BODY</div>
        <div class="docs-api-params-table">
          ${data.bodyParams.map(p => `
            <div class="docs-api-param-row">
              <div class="docs-api-param-meta">
                <span class="docs-api-param-name">${escapeHtml(p.name)}</span>
                <span class="docs-api-param-type">${escapeHtml(p.type)}</span>
                <span class="docs-api-param-badge ${p.required ? 'required' : 'optional'}">${p.required ? 'REQUIRED' : 'OPTIONAL'}</span>
              </div>
              <div class="docs-api-param-desc">${escapeHtml(p.desc)}</div>
            </div>
          `).join('')}
        </div>
      `;
    }

    let noParamsNotice = '';
    if (!pathParamsHtml && !queryParamsHtml && !bodyParamsHtml) {
      noParamsNotice = '<div class="docs-api-no-params">No request parameters or body required.</div>';
    }

    // Response Schema HTML
    let responseSchemaHtml = '';
    if (data.responseSchema && data.responseSchema.length > 0) {
      responseSchemaHtml = `
        <div class="docs-api-section-subhead">RESPONSE SCHEMA</div>
        <div class="docs-api-params-table">
          ${data.responseSchema.map(p => `
            <div class="docs-api-param-row">
              <div class="docs-api-param-meta">
                <span class="docs-api-param-name">${escapeHtml(p.name)}</span>
                <span class="docs-api-param-type">${escapeHtml(p.type)}</span>
              </div>
              <div class="docs-api-param-desc">${escapeHtml(p.desc)}</div>
            </div>
          `).join('')}
        </div>
      `;
    }

    const curlEscaped = (data.curlCommand || '').replace(/`/g, '\\`').replace(/\\$/g, '\\\\$');
    const responseEscaped = (data.responseJson || '{}').replace(/`/g, '\\`').replace(/\\$/g, '\\\\$');

    container.innerHTML = `
      <!-- Top header row matching media_1789305750389.png -->
      <div class="docs-api-top-pill-row">
        <div class="docs-api-top-pill-left">
          <span class="docs-api-method-badge ${(data.method || 'GET').toLowerCase()}">${escapeHtml(data.method || 'GET')}</span>
          <span class="docs-api-top-path">${escapeHtml(data.path || '')}</span>
        </div>
        <button type="button" class="docs-api-top-copy-btn" onclick="copySnippet(this, '${escapeHtml(data.path || '')}')" title="Copy endpoint path">
          <i data-lucide="copy" style="width:13px;height:13px;"></i>
          <span>Copy</span>
        </button>
      </div>

      <h1 class="docs-api-page-title">${escapeHtml(data.title)}</h1>
      <p class="docs-api-page-lead">${escapeHtml(data.summary || data.lead || '')}</p>

      <!-- Card 1: REQUEST PARAMETERS -->
      <div class="docs-api-spec-card">
        <div class="docs-api-card-header">
          <div class="docs-api-card-title-group">
            <i data-lucide="arrow-up-right" class="docs-api-arrow-icon req"></i>
            <span class="docs-api-card-heading">REQUEST PARAMETERS</span>
          </div>
          <span class="docs-api-content-tag">${escapeHtml(data.contentType || 'none')}</span>
        </div>
        <div class="docs-api-card-body">
          ${pathParamsHtml}
          ${queryParamsHtml}
          ${bodyParamsHtml}
          ${noParamsNotice}

          <!-- Terminal cURL block -->
          <div class="docs-api-code-terminal">
            <div class="docs-api-code-terminal-header">
              <div class="docs-api-code-terminal-left">
                <span class="docs-api-terminal-lang">cURL</span>
                <span class="docs-api-terminal-sub">${data.contentType === 'application/json' ? 'JSON Body' : escapeHtml(data.contentType)}</span>
              </div>
              <button type="button" class="docs-api-terminal-copy-btn" onclick="copySnippet(this, \`${curlEscaped}\`)" title="Copy cURL snippet">
                <i data-lucide="copy" style="width:12px;height:12px;"></i>
                <span>Copy</span>
              </button>
            </div>
            <div class="docs-api-code-terminal-content">
              <pre><code>${escapeHtml(data.curlCommand || '')}</code></pre>
            </div>
          </div>
        </div>
      </div>

      <!-- Card 2: RESPONSE (200 OK) -->
      <div class="docs-api-spec-card">
        <div class="docs-api-card-header">
          <div class="docs-api-card-title-group">
            <i data-lucide="arrow-down-left" class="docs-api-arrow-icon res"></i>
            <span class="docs-api-card-heading">RESPONSE (${escapeHtml(data.responseStatus || '200 OK')})</span>
          </div>
          <span class="docs-api-status-badge">
            <span class="docs-api-status-dot"></span>
            ${escapeHtml(data.responseStatus || '200 OK')}
          </span>
        </div>
        <div class="docs-api-card-body">
          ${responseSchemaHtml}

          <!-- Terminal response block -->
          <div class="docs-api-code-terminal">
            <div class="docs-api-code-terminal-header">
              <div class="docs-api-code-terminal-left">
                <span class="docs-api-terminal-lang">Example Payload</span>
                <span class="docs-api-terminal-status-tag">${escapeHtml(data.responseStatus || '200 OK')}</span>
              </div>
              <button type="button" class="docs-api-terminal-copy-btn" onclick="copySnippet(this, \`${responseEscaped}\`)" title="Copy response JSON">
                <i data-lucide="copy" style="width:12px;height:12px;"></i>
                <span>Copy</span>
              </button>
            </div>
            <div class="docs-api-code-terminal-content">
              <pre><code>${escapeHtml(data.responseJson || '{}')}</code></pre>
            </div>
          </div>
        </div>
      </div>

      <div class="docs-feedback-row" style="margin-top:36px;">
        <span class="docs-feedback-title">How is this guide?</span>
        <div class="docs-feedback-btns">
          <button type="button" class="docs-feedback-btn ${docsFeedbackState === 'good' ? 'active' : ''}" id="docs-feedback-good">
            <i data-lucide="thumbs-up" style="width:13px;height:13px;"></i>
            <span>Good</span>
          </button>
          <button type="button" class="docs-feedback-btn ${docsFeedbackState === 'bad' ? 'active' : ''}" id="docs-feedback-bad">
            <i data-lucide="thumbs-down" style="width:13px;height:13px;"></i>
            <span>Bad</span>
          </button>
        </div>
      </div>

      <p class="docs-last-updated">Last updated on ${data.updated || '03/09/2026'}</p>
    `;
  } else {
    // Standard prose guides
    let heroHtml = '';
    if (data.hasHero || pageKey === 'qs-install') {
      heroHtml = `
        <div class="docs-hero-panel">
          <img src="/static/syte-hero.png" alt="Syte deployment platform">
        </div>
      `;
    }

    container.innerHTML = `
      <h1 class="docs-article-title">${escapeHtml(data.title)}</h1>
      <p class="docs-article-lead">${data.lead || ''}</p>

      ${heroHtml}

      <div class="docs-prose">
        ${data.content}
      </div>

      <div class="docs-feedback-row">
        <span class="docs-feedback-title">How is this guide?</span>
        <div class="docs-feedback-btns">
          <button type="button" class="docs-feedback-btn ${docsFeedbackState === 'good' ? 'active' : ''}" id="docs-feedback-good">
            <i data-lucide="thumbs-up" style="width:13px;height:13px;"></i>
            <span>Good</span>
          </button>
          <button type="button" class="docs-feedback-btn ${docsFeedbackState === 'bad' ? 'active' : ''}" id="docs-feedback-bad">
            <i data-lucide="thumbs-down" style="width:13px;height:13px;"></i>
            <span>Bad</span>
          </button>
        </div>
      </div>

      <p class="docs-last-updated">Last updated on ${data.updated || '03/09/2026'}</p>
    `;
  }

  document.getElementById('docs-feedback-good')?.addEventListener('click', () => {
    docsFeedbackState = 'good';
    showDocsPage(pageKey);
    toast('Thanks for your feedback!');
  });

  document.getElementById('docs-feedback-bad')?.addEventListener('click', () => {
    docsFeedbackState = 'bad';
    showDocsPage(pageKey);
    toast('Feedback recorded. We will improve this guide.');
  });

  container.scrollTop = 0;
  refreshIcons();
}
"""

    end_marker = "let docsEventsInitialized = false;"
    end_idx = app_js.find(end_marker)
    if end_idx == -1:
        print("Could not find end marker in app.js")
        return

    docs_data_serialized = json.dumps(js_docs_data, indent=2)
    new_chunk = f"const DOCS_DATA = {docs_data_serialized};\n\n{show_docs_page_js.strip()}\n\n"

    app_js_updated = app_js[:docs_data_start_idx] + new_chunk + app_js[end_idx:]

    with open("/root/syte/syte/static/app.js", "w", encoding="utf-8") as f:
        f.write(app_js_updated)
    print("Updated app.js with 113 API routes DOCS_DATA and showDocsPage")

if __name__ == "__main__":
    build()
