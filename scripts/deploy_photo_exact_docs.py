#!/usr/bin/env python3
"""
deploy_photo_exact_docs.py
Implements the exact API documentation design matching media_1789314620588.png
with TypeScript SVG icon from media_1789314827179.webp and prebuilt TSX files
with colored syntax highlighting for all 113 endpoints.
"""

import os
import re

APP_JS = "/root/syte/syte/static/app.js"
DOCS_CSS = "/root/syte/syte/static/pages/docs/docs.css"

# 1. High-fidelity TSX Generator and Syntax Highlighter JS code
NEW_RENDERER_JS = r'''
// TypeScript SVG icon derived from uploaded media_1789314827179.webp
const TS_ICON_SVG = `<svg class="docs-photo-ts-icon" viewBox="0 0 512 512" width="16" height="16" aria-hidden="true"><rect width="512" height="512" fill="#3178c6" rx="40"/><path fill="#ffffff" d="M115 178h138v34h-52v184h-34V212h-52v-34zm177 186c16 9 33 14 52 14 28 0 45-14 45-35 0-21-15-31-42-42-36-14-60-30-60-67 0-36 29-63 74-63 22 0 41 5 54 12l-13 33c-13-7-27-11-41-11-26 0-40 14-40 31 0 19 15 29 41 40 39 15 61 31 61 69 0 38-29 66-78 66-27 0-51-7-66-16l13-31z"/></svg>`;

// Generates a complete, tailored React/TypeScript TSX file for any endpoint
function generateTsxSnippet(ep) {
  const pathParts = ep.path.split('/').filter(p => p && p !== 'api');
  const baseName = pathParts.map(p => p.replace(/[{}:-]/g, '')).map(p => p.charAt(0).toUpperCase() + p.slice(1)).join('') || 'Service';
  const methodName = ep.method.charAt(0).toUpperCase() + ep.method.slice(1).toLowerCase();
  const componentName = `${baseName}${methodName}`;
  const lastPart = (pathParts[pathParts.length - 1] || 'api').replace(/[{}:-]/g, '');
  const fileName = `${lastPart || 'api'}.tsx`;

  const allParams = [...(ep.pathParams || []), ...(ep.queryParams || []), ...(ep.bodyParams || [])];
  const isPostOrPut = ['POST', 'PUT', 'PATCH'].includes(ep.method);

  let bodyProps = [];
  if (ep.bodyParams && ep.bodyParams.length > 0) {
    ep.bodyParams.forEach(b => {
      bodyProps.push(`    ${b.name}: ${b.type === 'integer' ? '3000' : (b.type === 'boolean' ? 'true' : `'example_${b.name}'`)},`);
    });
  }

  let code = `import React, { useState, useEffect } from 'react';\n\n`;

  if (allParams.length > 0) {
    code += `interface ${baseName}Params {\n`;
    allParams.forEach(p => {
      const tsType = p.type === 'integer' ? 'number' : (p.type === 'boolean' ? 'boolean' : (p.type === 'array' ? 'string[]' : 'string'));
      code += `  ${p.name}${p.required ? '' : '?'}: ${tsType};\n`;
    });
    code += `}\n\n`;
  }

  code += `interface ${baseName}Response {\n`;
  if (ep.responseSchema && ep.responseSchema.length > 0) {
    ep.responseSchema.forEach(s => {
      const tsType = s.type === 'integer' ? 'number' : (s.type === 'boolean' ? 'boolean' : (s.type === 'array' ? 'string[]' : 'string'));
      code += `  ${s.name}?: ${tsType};\n`;
    });
  } else {
    code += `  status: string;\n  data?: any;\n`;
  }
  code += `}\n\n`;

  code += `export function ${componentName}(): JSX.Element {\n`;
  code += `  const [data, setData] = useState<${baseName}Response | null>(null);\n`;
  code += `  const [loading, setLoading] = useState<boolean>(false);\n`;
  code += `  const [error, setError] = useState<string | null>(null);\n\n`;
  code += `  async function executeCall() {\n`;
  code += `    setLoading(true);\n`;
  code += `    setError(null);\n`;
  code += `    try {\n`;

  if (isPostOrPut && bodyProps.length > 0) {
    code += `      const payload = {\n`;
    code += bodyProps.join('\n') + '\n';
    code += `      };\n`;
    code += `+     const res = await fetch('${ep.path}', {\n`;
    code += `+       method: '${ep.method}',\n`;
    code += `+       headers: {\n`;
    code += `+         'Authorization': \`Bearer \${process.env.NEXT_PUBLIC_SYTE_KEY}\`,\n`;
    code += `+         'Content-Type': 'application/json'\n`;
    code += `+       },\n`;
    code += `+       body: JSON.stringify(payload)\n`;
    code += `+     });\n`;
  } else {
    code += `+     const res = await fetch('${ep.path}', {\n`;
    code += `+       method: '${ep.method}',\n`;
    code += `+       headers: {\n`;
    code += `+         'Authorization': \`Bearer \${process.env.NEXT_PUBLIC_SYTE_KEY}\`,\n`;
    code += `+         'Content-Type': 'application/json'\n`;
    code += `+       }\n`;
    code += `+     });\n`;
  }

  code += `      if (!res.ok) throw new Error(\`HTTP \${res.status}: \${res.statusText}\`);\n`;
  code += `      const json: ${baseName}Response = await res.json();\n`;
  code += `      setData(json);\n`;
  code += `    } catch (err: any) {\n`;
  code += `      setError(err.message || 'Call failed');\n`;
  code += `    } finally {\n`;
  code += `      setLoading(false);\n`;
  code += `    }\n`;
  code += `  }\n\n`;
  code += `  return (\n`;
  code += `    <div className="p-4 rounded-xl border bg-white shadow-sm">\n`;
  code += `      <div className="flex items-center justify-between mb-3">\n`;
  code += `        <span className="font-mono text-sm font-semibold">${ep.method} ${ep.path}</span>\n`;
  code += `        <button onClick={executeCall} disabled={loading} className="btn-primary">\n`;
  code += `          {loading ? 'Running...' : 'Execute'}\n`;
  code += `        </button>\n`;
  code += `      </div>\n`;
  code += `      {error && <p className="text-red-500 text-xs mt-2">{error}</p>}\n`;
  code += `      {data && <pre className="text-xs bg-gray-50 p-2 rounded mt-2">{JSON.stringify(data, null, 2)}</pre>}\n`;
  code += `    </div>\n`;
  code += `  );\n`;
  code += `}\n`;

  return { code, fileName };
}

// Syntax highlighter matching the colors in media_1789314620588.png
function highlightTsx(code) {
  const tokenSpec = [
    { type: 'COMMENT', regex: /^\/\/[^\n]*/ },
    { type: 'STRING',  regex: /^('[^'\\]*(?:\\.[^'\\]*)*'|"[^"\\]*(?:\\.[^"\\]*)*"|`[^`\\]*(?:\\.[^`\\]*)*`)/ },
    { type: 'KEYWORD', regex: /^(?:import|export|from|const|let|var|function|return|async|await|try|catch|finally|if|else|type|interface|as|new|typeof|default)\b/ },
    { type: 'BOOLEAN', regex: /^(?:true|false|null|undefined)\b/ },
    { type: 'NUMBER',  regex: /^\d+(?:\.\d+)?\b/ },
    { type: 'JSX_TAG', regex: /^<\/?[a-zA-Z0-9_]+/ },
    { type: 'IDENT',   regex: /^[a-zA-Z_$][a-zA-Z0-9_$]*/ },
    { type: 'PUNCT',   regex: /^[{}()[\];,.:<>=+\-*/?&|!~]/ },
    { type: 'SPACE',   regex: /^[ \t]+/ }
  ];

  const lines = code.split('\n');
  return lines.map(rawLine => {
    let isAdd = false;
    let lineContent = rawLine;
    if (lineContent.startsWith('+ ')) {
      isAdd = true;
      lineContent = lineContent.substring(2);
    } else if (lineContent.startsWith('+')) {
      isAdd = true;
      lineContent = lineContent.substring(1);
    }

    let outHtml = '';
    let remaining = lineContent;

    while (remaining.length > 0) {
      let matched = false;
      for (const spec of tokenSpec) {
        const m = remaining.match(spec.regex);
        if (m) {
          const val = m[0];
          remaining = remaining.substring(val.length);
          matched = true;
          const esc = escapeHtml(val);

          if (spec.type === 'COMMENT') {
            outHtml += '<span class="ts-comm">' + esc + '</span>';
          } else if (spec.type === 'STRING') {
            outHtml += '<span class="ts-str">' + esc + '</span>';
          } else if (spec.type === 'KEYWORD') {
            outHtml += '<span class="ts-kw">' + esc + '</span>';
          } else if (spec.type === 'BOOLEAN') {
            outHtml += '<span class="ts-bool">' + esc + '</span>';
          } else if (spec.type === 'NUMBER') {
            outHtml += '<span class="ts-num">' + esc + '</span>';
          } else if (spec.type === 'JSX_TAG') {
            outHtml += '<span class="ts-tag">' + esc + '</span>';
          } else if (spec.type === 'IDENT') {
            const nextTrimmed = remaining.trimStart();
            if (nextTrimmed.startsWith('(')) {
              outHtml += '<span class="ts-fn">' + esc + '</span>';
            } else if (val[0] === val[0].toUpperCase() && val[0] !== val[0].toLowerCase()) {
              outHtml += '<span class="ts-type">' + esc + '</span>';
            } else {
              outHtml += '<span class="ts-var">' + esc + '</span>';
            }
          } else {
            outHtml += esc;
          }
          break;
        }
      }
      if (!matched) {
        outHtml += escapeHtml(remaining[0]);
        remaining = remaining.substring(1);
      }
    }

    if (isAdd) {
      return '<div class="ts-line ts-add"><span class="ts-sign">+</span> ' + outHtml + '</div>';
    }
    return '<div class="ts-line">' + outHtml + '</div>';
  }).join('\n');
}

window.generateTsxSnippet = generateTsxSnippet;
window.highlightTsx = highlightTsx;

// Interactive handlers for photo layout
window.togglePhotoStatus = function(btn) {
  const item = btn.closest('.docs-photo-status-item');
  if (!item) return;
  item.classList.toggle('is-open');
  const body = item.querySelector('.docs-photo-status-body');
  if (body) {
    body.style.display = item.classList.contains('is-open') ? 'block' : 'none';
  }
};

window.copyPhotoTsx = function(btn, pageKey) {
  const ep = API_CATALOG[pageKey];
  if (!ep) return;
  const tsxData = generateTsxSnippet(ep);
  navigator.clipboard?.writeText(tsxData.code).then(() => {
    toast('Copied ' + tsxData.fileName + ' to clipboard');
  }).catch(() => {
    toast('Failed to copy');
  });
};

window.handleDocsReport = function(pageKey) {
  const ep = API_CATALOG[pageKey];
  toast('Feedback report submitted for ' + (ep ? ep.path : pageKey));
};

window.toggleDocsMoreMenu = function(btn, pageKey) {
  const ep = API_CATALOG[pageKey];
  if (!ep) return;
  const existing = document.getElementById('docs-more-dropdown-menu');
  if (existing) {
    existing.remove();
    return;
  }
  const rect = btn.getBoundingClientRect();
  const menu = document.createElement('div');
  menu.id = 'docs-more-dropdown-menu';
  menu.className = 'docs-photo-more-dropdown';
  menu.style.top = (rect.bottom + window.scrollY + 6) + 'px';
  menu.style.left = (rect.left + window.scrollX) + 'px';
  menu.innerHTML = `
    <button type="button" onclick="copySnippet(this, '${escapeHtml(ep.path)}'); document.getElementById('docs-more-dropdown-menu')?.remove();">
      <i data-lucide="copy" style="width:13px;height:13px;"></i> Copy Path
    </button>
    <button type="button" onclick="copySnippet(this, window.location.origin + '${escapeHtml(ep.path)}'); document.getElementById('docs-more-dropdown-menu')?.remove();">
      <i data-lucide="link" style="width:13px;height:13px;"></i> Copy Full URL
    </button>
    <button type="button" onclick="runInteractiveApiTest('${ep.key}'); document.getElementById('docs-more-dropdown-menu')?.remove();">
      <i data-lucide="play" style="width:13px;height:13px;"></i> Test in Console
    </button>
  `;
  document.body.appendChild(menu);
  refreshIcons();

  const closeMenu = (e) => {
    if (!menu.contains(e.target) && e.target !== btn) {
      menu.remove();
      document.removeEventListener('click', closeMenu);
    }
  };
  setTimeout(() => document.addEventListener('click', closeMenu), 50);
};

window.openDocsSearchModal = function() {
  const searchInput = document.getElementById('docs-search-input');
  if (searchInput) {
    searchInput.focus();
    searchInput.select();
  }
};

window.switchPhotoCodeTab = function(btn, lang, pageKey) {
  const block = btn.closest('.docs-photo-codeblock');
  if (!block) return;
  block.querySelectorAll('.docs-photo-code-tab').forEach(t => t.classList.toggle('active', t === btn));
  block.querySelectorAll('.docs-photo-code-body-pane').forEach(p => p.classList.toggle('hidden', p.dataset.lang !== lang));
};

// Exact implementation of the API documentation page matching media_1789314620588.png
window.renderApiDocPage = function(ep) {
  const container = document.getElementById('docs-main-content');
  if (!container || !ep) return;

  // 1. Determine Previous & Next endpoints in API_CATALOG
  const allKeys = Object.keys(API_CATALOG);
  const curIndex = allKeys.indexOf(ep.key);
  const prevKey = allKeys[(curIndex - 1 + allKeys.length) % allKeys.length];
  const nextKey = allKeys[(curIndex + 1) % allKeys.length];

  // 2. Build parameters row (aild* string)
  const allParams = [];
  if (ep.pathParams) ep.pathParams.forEach(p => allParams.push({ ...p, kind: 'path' }));
  if (ep.queryParams) ep.queryParams.forEach(p => allParams.push({ ...p, kind: 'query' }));
  if (ep.bodyParams) ep.bodyParams.forEach(p => allParams.push({ ...p, kind: 'body' }));

  let paramsHtml = '';
  if (allParams.length > 0) {
    paramsHtml = `
      <div class="docs-photo-params-row">
        ${allParams.map(p => `
          <div class="docs-photo-param">
            <span class="docs-photo-param-name">${escapeHtml(p.name)}</span><span class="docs-photo-param-star">${p.required !== false ? '*' : ''}</span>
            <span class="docs-photo-param-type">${escapeHtml(p.type || 'string')}</span>
          </div>
        `).join('')}
      </div>
    `;
  } else {
    paramsHtml = `
      <div class="docs-photo-params-row">
        <div class="docs-photo-param">
          <span class="docs-photo-param-name">none</span>
          <span class="docs-photo-param-type">no required parameters</span>
        </div>
      </div>
    `;
  }

  // 3. Build Status codes disclosure list (> 200  application/json)
  let statusCodesList = ep.statusCodes && ep.statusCodes.length > 0 ? ep.statusCodes : [
    { code: 200, status: '200 OK', desc: 'Successful response returning data payload.' },
    { code: 400, status: '400 Bad Request', desc: 'Invalid parameter shape or request body validation error.' },
    { code: 401, status: '401 Unauthorized', desc: 'Missing or expired Bearer authentication token.' }
  ];

  let statusCodesHtml = `
    <div class="docs-photo-status-list">
      ${statusCodesList.map((s, idx) => `
        <div class="docs-photo-status-item ${idx === 0 ? 'is-open' : ''}">
          <button type="button" class="docs-photo-status-head" onclick="togglePhotoStatus(this)">
            <div class="docs-photo-status-left">
              <i data-lucide="chevron-right" class="docs-photo-status-chev"></i>
              <span class="docs-photo-status-code">${s.code}</span>
            </div>
            <span class="docs-photo-status-mime">application/json</span>
          </button>
          <div class="docs-photo-status-body" style="${idx === 0 ? 'display:block;' : 'display:none;'}">
            <div class="docs-photo-status-desc">${escapeHtml(s.desc || s.status || '')}</div>
            ${s.code === 200 && ep.responseSchema && ep.responseSchema.length > 0 ? `
              <div class="docs-photo-schema-mini">
                ${ep.responseSchema.map(rs => `
                  <div class="docs-photo-schema-field">
                    <code>${escapeHtml(rs.name)}</code>: <span class="ts-type">${escapeHtml(rs.type)}</span> &mdash; <span class="desc">${escapeHtml(rs.desc || '')}</span>
                  </div>
                `).join('')}
              </div>
            ` : ''}
          </div>
        </div>
      `).join('')}
    </div>
  `;

  // 4. Generate Prebuilt TSX Codeblock
  const tsxData = generateTsxSnippet(ep);
  const highlightedTsxHtml = highlightTsx(tsxData.code);

  // cURL & Fetch alternative snippets for secondary toggle
  const host = (typeof window !== 'undefined' && window.location && window.location.origin) ? window.location.origin : 'https://sycord.site:8787';
  const fullUrl = `${host}${ep.path}`;
  let curlCmd = `curl -X ${ep.method} "${fullUrl}"`;
  if (!ep.auth.includes('Public')) curlCmd += ` \\\n  -H "Authorization: Bearer <your_token>"`;
  curlCmd += ` \\\n  -H "Content-Type: application/json"`;

  const methodClass = ep.method.toLowerCase();

  // 5. Render Full Exact Photo Layout
  container.innerHTML = `
    <div class="docs-photo-page-wrap">
      <!-- 1. Top GET / API searchbar (optimized) -->
      <div class="docs-photo-top-bar">
        <div class="docs-photo-top-left">
          <span class="docs-photo-method-badge ${methodClass}">${escapeHtml(ep.method)}</span>
          <span class="docs-photo-path">${escapeHtml(ep.path)}</span>
        </div>
        <div class="docs-photo-top-actions">
          <button type="button" class="docs-photo-search-btn" onclick="openDocsSearchModal()" title="Search endpoints (Ctrl+K)">
            <i data-lucide="search"></i>
            <span class="search-label">Search API...</span>
            <kbd>⌘K</kbd>
          </button>
          <button type="button" class="docs-photo-copy-path-btn" onclick="copySnippet(this, '${escapeHtml(ep.path)}')" title="Copy path">
            <i data-lucide="copy"></i>
          </button>
        </div>
      </div>

      <!-- 2. Dual button (Previous / Next) + Report + More -->
      <div class="docs-photo-btn-bar">
        <div class="docs-photo-dual-btn">
          <button type="button" class="docs-photo-btn-prev" onclick="showDocsPage('${prevKey}')" title="Previous endpoint: ${prevKey}">
            Previous
          </button>
          <button type="button" class="docs-photo-btn-next" onclick="showDocsPage('${nextKey}')" title="Next endpoint: ${nextKey}">
            Next
          </button>
        </div>
        <button type="button" class="docs-photo-btn-single" onclick="handleDocsReport('${ep.key}')">
          Report
        </button>
        <button type="button" class="docs-photo-btn-more" onclick="toggleDocsMoreMenu(this, '${ep.key}')" title="More actions">
          &bull;&bull;&bull;
        </button>
      </div>

      <!-- 3. Exact Description Paragraph -->
      <p class="docs-photo-description">
        ${escapeHtml(ep.summary || '')} ${escapeHtml(ep.desc || 'Returns detailed information and manages configuration, metadata, and runtime state associated with this resource.')}
      </p>

      <!-- 4. Thin Divider Line -->
      <hr class="docs-photo-divider" />

      <!-- 5. Parameters Row (aild* string) -->
      ${paramsHtml}

      <!-- 6. Status Codes List (> 200 application/json) -->
      ${statusCodesHtml}

      <!-- 7. Prebuilt TSX Codeblock with TS SVG icon and colored syntax -->
      <div class="docs-photo-codeblock">
        <div class="docs-photo-code-header">
          <div class="docs-photo-code-header-left">
            <span class="docs-photo-ts-icon-wrap">${TS_ICON_SVG}</span>
            <span class="docs-photo-filename">${escapeHtml(tsxData.fileName)}</span>
          </div>
          <div class="docs-photo-code-header-right">
            <button type="button" class="docs-photo-code-tab active" onclick="switchPhotoCodeTab(this, 'tsx', '${ep.key}')">TSX</button>
            <button type="button" class="docs-photo-code-tab" onclick="switchPhotoCodeTab(this, 'curl', '${ep.key}')">cURL</button>
            <button type="button" class="docs-photo-code-copy" onclick="copyPhotoTsx(this, '${ep.key}')" title="Copy code">
              <i data-lucide="copy"></i>
            </button>
          </div>
        </div>

        <div class="docs-photo-code-body-pane" data-lang="tsx">
          <div class="docs-photo-code-body">${highlightTsx(tsxData.code)}</div>
        </div>
        <div class="docs-photo-code-body-pane hidden" data-lang="curl">
          <div class="docs-photo-code-body"><pre><code>${escapeHtml(curlCmd)}</code></pre></div>
        </div>
      </div>

      <!-- Secondary interactive request tester -->
      <div class="docs-photo-test-runner-wrap">
        <button type="button" class="docs-photo-run-test-btn" onclick="runInteractiveApiTest('${ep.key}')">
          <i data-lucide="play" style="width:13px;height:13px;"></i>
          <span>Send Test Request</span>
        </button>
        <div class="docs-api-live-test-output hidden" id="docs-api-live-test-box"></div>
      </div>

      <!-- Feedback & Updated date -->
      <div class="docs-feedback-row" style="margin-top:36px;">
        <span class="docs-feedback-title">How is this API documentation?</span>
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

      <p class="docs-last-updated">Last updated on 03/09/2026</p>
    </div>
  `;

  document.getElementById('docs-feedback-good')?.addEventListener('click', () => {
    docsFeedbackState = 'good';
    showDocsPage(ep.key);
    toast('Thanks for your feedback!');
  });

  document.getElementById('docs-feedback-bad')?.addEventListener('click', () => {
    docsFeedbackState = 'bad';
    showDocsPage(ep.key);
    toast('Feedback recorded.');
  });

  container.scrollTop = 0;
  refreshIcons();
};
'''

# 2. CSS Styles matching media_1789314620588.png
PHOTO_CSS = r'''
/* ==========================================================================
   EXACT DESIGN STYLING MATCHING media_1789314620588.png
   ========================================================================== */

.docs-photo-page-wrap {
  width: 100%;
  max-width: 820px;
  margin: 0 auto;
  padding: 8px 4px 60px 4px;
}

/* 1. Top GET / API searchbar (optimized) */
.docs-photo-top-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #ffffff;
  border: 1px solid #e5e7eb;
  border-radius: 16px;
  padding: 12px 18px;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: all 0.2s ease;
}

body.dark .docs-photo-top-bar {
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.08);
  box-shadow: none;
}

.docs-photo-top-left {
  display: flex;
  align-items: center;
  gap: 14px;
  min-width: 0;
  overflow: hidden;
}

.docs-photo-method-badge {
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: 0.02em;
  flex-shrink: 0;
}

.docs-photo-method-badge.get { color: #16a34a; }
.docs-photo-method-badge.post { color: #2563eb; }
.docs-photo-method-badge.put { color: #d97706; }
.docs-photo-method-badge.delete { color: #dc2626; }
.docs-photo-method-badge.patch { color: #7c3aed; }

body.dark .docs-photo-method-badge.get { color: #22c55e; }
body.dark .docs-photo-method-badge.post { color: #3b82f6; }
body.dark .docs-photo-method-badge.put { color: #f59e0b; }
body.dark .docs-photo-method-badge.delete { color: #ef4444; }
body.dark .docs-photo-method-badge.patch { color: #a855f7; }

.docs-photo-path {
  font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
  font-size: 15.5px;
  font-weight: 700;
  color: #1f2937;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

body.dark .docs-photo-path {
  color: #f4f4f5;
}

.docs-photo-top-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.docs-photo-search-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 8px;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  color: #6b7280;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}

body.dark .docs-photo-search-btn {
  background: #27272a;
  border-color: rgba(255, 255, 255, 0.08);
  color: #a1a1aa;
}

.docs-photo-search-btn:hover {
  background: #e5e7eb;
  color: #111827;
}

body.dark .docs-photo-search-btn:hover {
  background: #3f3f46;
  color: #ffffff;
}

.docs-photo-search-btn kbd {
  font-size: 10px;
  font-family: inherit;
  padding: 1px 4px;
  border-radius: 4px;
  background: #ffffff;
  border: 1px solid #d1d5db;
  color: #4b5563;
}

body.dark .docs-photo-search-btn kbd {
  background: #18181b;
  border-color: #3f3f46;
  color: #d4d4d8;
}

.docs-photo-copy-path-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: #f3f4f6;
  border: 1px solid #e5e7eb;
  color: #6b7280;
  cursor: pointer;
  transition: all 0.15s ease;
}

body.dark .docs-photo-copy-path-btn {
  background: #27272a;
  border-color: rgba(255, 255, 255, 0.08);
  color: #a1a1aa;
}

.docs-photo-copy-path-btn:hover {
  background: #e5e7eb;
  color: #111827;
}

body.dark .docs-photo-copy-path-btn:hover {
  background: #3f3f46;
  color: #ffffff;
}

/* 2. Action Buttons Row */
.docs-photo-btn-bar {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.docs-photo-dual-btn {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #ececec;
  overflow: hidden;
}

body.dark .docs-photo-dual-btn {
  border-color: #3f3f46;
  background: #27272a;
}

.docs-photo-btn-prev,
.docs-photo-btn-next {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1f2937;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-prev,
body.dark .docs-photo-btn-next {
  color: #f4f4f5;
}

.docs-photo-btn-prev {
  border-right: 1px solid #d1d5db;
}

body.dark .docs-photo-btn-prev {
  border-right-color: #3f3f46;
}

.docs-photo-btn-prev:hover,
.docs-photo-btn-next:hover {
  background: rgba(0, 0, 0, 0.06);
}

body.dark .docs-photo-btn-prev:hover,
body.dark .docs-photo-btn-next:hover {
  background: rgba(255, 255, 255, 0.08);
}

.docs-photo-btn-single {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1f2937;
  background: #ececec;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-single {
  color: #f4f4f5;
  background: #27272a;
  border-color: #3f3f46;
}

.docs-photo-btn-single:hover {
  background: #e2e5e9;
}

body.dark .docs-photo-btn-single:hover {
  background: #3f3f46;
}

.docs-photo-btn-more {
  padding: 7px 13px;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #1f2937;
  background: #ececec;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-more {
  color: #f4f4f5;
  background: #27272a;
  border-color: #3f3f46;
}

.docs-photo-btn-more:hover {
  background: #e2e5e9;
}

body.dark .docs-photo-btn-more:hover {
  background: #3f3f46;
}

/* More Dropdown Menu */
.docs-photo-more-dropdown {
  position: absolute;
  z-index: 1000;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 9px;
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1), 0 8px 10px -6px rgba(0, 0, 0, 0.1);
  padding: 6px;
  min-width: 170px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: docsModalIn 0.15s ease;
}

body.dark .docs-photo-more-dropdown {
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
}

.docs-photo-more-dropdown button {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border-radius: 6px;
  border: none;
  background: transparent;
  color: #334155;
  font-size: 12.5px;
  font-weight: 500;
  text-align: left;
  cursor: pointer;
  transition: all 0.12s ease;
}

body.dark .docs-photo-more-dropdown button {
  color: #d4d4d8;
}

.docs-photo-more-dropdown button:hover {
  background: #f1f5f9;
  color: #0f172a;
}

body.dark .docs-photo-more-dropdown button:hover {
  background: #27272a;
  color: #ffffff;
}

/* 3. Description Paragraph */
.docs-photo-description {
  margin-top: 26px;
  font-size: 14.5px;
  line-height: 1.68;
  color: #8a929e;
  font-weight: 400;
}

body.dark .docs-photo-description {
  color: #a1a1aa;
}

/* 4. Thin Divider Line */
.docs-photo-divider {
  margin: 28px 0 22px 0;
  border: none;
  border-top: 1px solid #e5e7eb;
}

body.dark .docs-photo-divider {
  border-top-color: rgba(255, 255, 255, 0.08);
}

/* 5. Parameters Row (aild* string) */
.docs-photo-params-row {
  display: flex;
  align-items: center;
  gap: 32px;
  flex-wrap: wrap;
  margin-bottom: 24px;
}

.docs-photo-param {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  font-size: 14px;
}

.docs-photo-param-name {
  font-weight: 700;
  color: #0f172a;
}

body.dark .docs-photo-param-name {
  color: #f4f4f5;
}

.docs-photo-param-star {
  color: #ef4444;
  font-weight: 700;
}

.docs-photo-param-type {
  color: #94a3b8;
  font-size: 13.5px;
  font-weight: 400;
}

body.dark .docs-photo-param-type {
  color: #71717a;
}

/* 6. Status Codes List (> 200 application/json) */
.docs-photo-status-list {
  display: flex;
  flex-direction: column;
  margin-bottom: 28px;
}

.docs-photo-status-item {
  border-top: 1px solid #f1f5f9;
}

.docs-photo-status-item:last-child {
  border-bottom: 1px solid #f1f5f9;
}

body.dark .docs-photo-status-item {
  border-top-color: rgba(255, 255, 255, 0.06);
}

body.dark .docs-photo-status-item:last-child {
  border-bottom-color: rgba(255, 255, 255, 0.06);
}

.docs-photo-status-head {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 0;
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
}

.docs-photo-status-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-photo-status-chev {
  width: 14px;
  height: 14px;
  color: #374151;
  transition: transform 0.2s ease;
}

body.dark .docs-photo-status-chev {
  color: #9ca3af;
}

.docs-photo-status-item.is-open .docs-photo-status-chev {
  transform: rotate(90deg);
}

.docs-photo-status-code {
  font-weight: 700;
  font-size: 14.5px;
  color: #0f172a;
}

body.dark .docs-photo-status-code {
  color: #f4f4f5;
}

.docs-photo-status-mime {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  color: #64748b;
}

body.dark .docs-photo-status-mime {
  color: #a1a1aa;
}

.docs-photo-status-body {
  padding: 0 0 14px 22px;
  font-size: 13px;
  color: #475569;
  line-height: 1.6;
}

body.dark .docs-photo-status-body {
  color: #cbd5e1;
}

.docs-photo-status-desc {
  margin-bottom: 6px;
}

.docs-photo-schema-mini {
  display: flex;
  flex-direction: column;
  gap: 3px;
  margin-top: 6px;
  padding: 8px 12px;
  border-radius: 6px;
  background: #f8fafc;
  border: 1px solid #e2e8f0;
  font-size: 12px;
}

body.dark .docs-photo-schema-mini {
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.08);
}

.docs-photo-schema-field code {
  font-weight: 600;
  color: #0f172a;
}

body.dark .docs-photo-schema-field code {
  color: #f4f4f5;
}

/* 7. Codeblock matching photo */
.docs-photo-codeblock {
  border-radius: 14px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  background: #f3f4f6;
  margin-top: 8px;
}

body.dark .docs-photo-codeblock {
  background: #161619;
  border-color: rgba(255, 255, 255, 0.08);
}

.docs-photo-code-header {
  background: #e9edf0;
  padding: 9px 14px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
}

body.dark .docs-photo-code-header {
  background: #1e1e23;
  border-bottom-color: rgba(255, 255, 255, 0.06);
}

.docs-photo-code-header-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-photo-ts-icon-wrap {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
}

.docs-photo-ts-icon {
  width: 16px;
  height: 16px;
  border-radius: 3px;
}

.docs-photo-filename {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  font-weight: 500;
  color: #475569;
}

body.dark .docs-photo-filename {
  color: #d4d4d8;
}

.docs-photo-code-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-photo-code-tab {
  padding: 3px 8px;
  font-size: 11px;
  font-weight: 600;
  border-radius: 5px;
  border: none;
  background: transparent;
  color: #64748b;
  cursor: pointer;
  transition: all 0.12s ease;
}

body.dark .docs-photo-code-tab {
  color: #a1a1aa;
}

.docs-photo-code-tab.active {
  background: #ffffff;
  color: #0f172a;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

body.dark .docs-photo-code-tab.active {
  background: #27272a;
  color: #ffffff;
}

.docs-photo-code-copy {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  border-radius: 5px;
  background: transparent;
  border: none;
  color: #64748b;
  cursor: pointer;
  transition: color 0.15s ease;
}

body.dark .docs-photo-code-copy {
  color: #a1a1aa;
}

.docs-photo-code-copy:hover {
  color: #0f172a;
}

body.dark .docs-photo-code-copy:hover {
  color: #ffffff;
}

.docs-photo-code-body {
  padding: 16px 18px 20px 18px;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
  font-size: 12.8px;
  line-height: 1.68;
  color: #1e293b;
}

body.dark .docs-photo-code-body {
  color: #f1f5f9;
}

.docs-photo-code-body pre {
  margin: 0;
  padding: 0;
  background: transparent;
  border: none;
}

/* Syntax Highlighting Colors matching media_1789314620588.png */
.ts-line {
  display: block;
  white-space: pre;
  min-height: 1.68em;
}

.ts-line.ts-add {
  background: rgba(34, 197, 94, 0.12);
  margin: 0 -18px;
  padding: 0 18px;
  border-left: 3px solid #22c55e;
}

body.dark .ts-line.ts-add {
  background: rgba(34, 197, 94, 0.18);
  border-left-color: #4ade80;
}

.ts-sign {
  color: #16a34a;
  font-weight: 700;
  margin-right: 4px;
  user-select: none;
}

body.dark .ts-sign {
  color: #4ade80;
}

.ts-kw {
  color: #9333ea;
  font-weight: 500;
}

body.dark .ts-kw {
  color: #c084fc;
}

.ts-fn {
  color: #2563eb;
  font-style: italic;
}

body.dark .ts-fn {
  color: #60a5fa;
}

.ts-str {
  color: #16a34a;
}

body.dark .ts-str {
  color: #4ade80;
}

.ts-bool {
  color: #ea580c;
}

body.dark .ts-bool {
  color: #fb923c;
}

.ts-num {
  color: #d97706;
}

body.dark .ts-num {
  color: #f59e0b;
}

.ts-type {
  color: #0284c7;
}

body.dark .ts-type {
  color: #38bdf8;
}

.ts-tag {
  color: #2563eb;
}

body.dark .ts-tag {
  color: #60a5fa;
}

.ts-var {
  color: #1e293b;
}

body.dark .ts-var {
  color: #f1f5f9;
}

.ts-comm {
  color: #94a3b8;
  font-style: italic;
}

body.dark .ts-comm {
  color: #71717a;
}

/* Secondary interactive runner */
.docs-photo-test-runner-wrap {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.docs-photo-run-test-btn {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  border-radius: 8px;
  background: #0f172a;
  color: #ffffff;
  border: none;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

body.dark .docs-photo-run-test-btn {
  background: #ffffff;
  color: #0f172a;
}

.docs-photo-run-test-btn:hover {
  opacity: 0.9;
}

/* Mobile Responsiveness */
@media (max-width: 640px) {
  .docs-photo-top-bar {
    padding: 10px 14px;
    border-radius: 14px;
  }
  .docs-photo-search-btn .search-label,
  .docs-photo-search-btn kbd {
    display: none;
  }
  .docs-photo-search-btn {
    padding: 7px;
  }
  .docs-photo-params-row {
    gap: 16px;
  }
}
'''

def main():
    print("Reading", APP_JS)
    with open(APP_JS, "r") as f:
        app_code = f.read()

    # Find where renderApiDocPage is defined
    target_pattern = r"// Global unified renderer (?:for any API endpoint|matching media_1789314620588\.png)[\s\S]*?\nwindow\.renderGuideDocPage"
    
    match = re.search(target_pattern, app_code)
    if not match:
        print("ERROR: Could not find renderApiDocPage block in app.js")
        return

    replacement = "// Global unified renderer matching media_1789314620588.png\n" + NEW_RENDERER_JS.strip() + "\n\nwindow.renderGuideDocPage"
    app_code_new = app_code[:match.start()] + replacement + app_code[match.end() - len("\nwindow.renderGuideDocPage"):]

    print("Writing updated app.js...")
    with open(APP_JS, "w") as f:
        f.write(app_code_new)
    print("Updated app.js successfully!")

    print("Reading", DOCS_CSS)
    with open(DOCS_CSS, "r") as f:
        css_code = f.read()

    # Append PHOTO_CSS if not already present
    if "/* EXACT DESIGN STYLING MATCHING media_1789314620588.png */" in css_code:
        # replace existing block
        css_code = re.sub(r"/\* ==========================================================================\n   EXACT DESIGN STYLING MATCHING media_1789314620588\.png[\s\S]*", PHOTO_CSS.strip() + "\n", css_code)
    else:
        css_code = css_code + "\n\n" + PHOTO_CSS.strip() + "\n"

    print("Writing updated docs.css...")
    with open(DOCS_CSS, "w") as f:
        f.write(css_code)
    print("Updated docs.css successfully!")

if __name__ == "__main__":
    main()
