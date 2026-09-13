#!/usr/bin/env python3
import json
import re

from scripts.generate_full_docs import API_ENDPOINTS

def escape_html(s):
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")

def generate_css():
    return """
/* ==========================================================================
   Fumadocs Modern API Dual-Card Layout Styles (matching media_1789305750389.png)
   ========================================================================== */

.docs-api-top-pill-row {
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  background: #f4f4f5;
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 8px;
  padding: 6px 12px;
  margin-bottom: 20px;
  max-width: 100%;
}

body.dark .docs-api-top-pill-row {
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.08);
}

.docs-api-top-pill-left {
  display: flex;
  align-items: center;
  gap: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.docs-api-top-path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  color: #18181b;
}

body.dark .docs-api-top-path {
  color: #f4f4f5;
}

.docs-api-top-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 6px;
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.1);
  color: #52525b;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

body.dark .docs-api-top-copy-btn {
  background: #27272a;
  border-color: rgba(255, 255, 255, 0.12);
  color: #d4d4d8;
}

.docs-api-top-copy-btn:hover {
  background: #f4f4f5;
  color: #09090b;
}

body.dark .docs-api-top-copy-btn:hover {
  background: #3f3f46;
  color: #ffffff;
}

.docs-api-page-title {
  font-size: 28px;
  font-weight: 700;
  letter-spacing: -0.025em;
  color: #09090b;
  margin: 0 0 8px 0;
  line-height: 1.25;
}

body.dark .docs-api-page-title {
  color: #f4f4f5;
}

.docs-api-page-lead {
  font-size: 15px;
  line-height: 1.6;
  color: #71717a;
  margin: 0 0 28px 0;
}

body.dark .docs-api-page-lead {
  color: #a1a1aa;
}

/* Dual-Card Container */
.docs-api-spec-card {
  background: #ffffff;
  border: 1px solid rgba(0, 0, 0, 0.09);
  border-radius: 12px;
  margin-bottom: 24px;
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
  transition: border-color 0.2s ease;
}

body.dark .docs-api-spec-card {
  background: #111114;
  border-color: rgba(255, 255, 255, 0.08);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
}

.docs-api-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  background: #fafafa;
  border-bottom: 1px solid rgba(0, 0, 0, 0.06);
}

body.dark .docs-api-card-header {
  background: #16161a;
  border-bottom-color: rgba(255, 255, 255, 0.06);
}

.docs-api-card-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-api-arrow-icon {
  width: 15px;
  height: 15px;
}

.docs-api-arrow-icon.req {
  color: #2563eb;
}

.docs-api-arrow-icon.res {
  color: #10b981;
}

.docs-api-card-heading {
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  color: #27272a;
}

body.dark .docs-api-card-heading {
  color: #e4e4e7;
}

.docs-api-content-tag {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 6px;
  background: #f4f4f5;
  color: #52525b;
  border: 1px solid rgba(0, 0, 0, 0.06);
}

body.dark .docs-api-content-tag {
  background: #27272a;
  color: #a1a1aa;
  border-color: rgba(255, 255, 255, 0.08);
}

.docs-api-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  font-weight: 700;
  padding: 3px 9px;
  border-radius: 9999px;
  background: rgba(16, 185, 129, 0.12);
  color: #10b981;
  border: 1px solid rgba(16, 185, 129, 0.25);
}

.docs-api-status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
}

.docs-api-card-body {
  padding: 18px;
}

.docs-api-section-subhead {
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #71717a;
  margin: 16px 0 10px 0;
}

.docs-api-section-subhead:first-child {
  margin-top: 0;
}

body.dark .docs-api-section-subhead {
  color: #a1a1aa;
}

/* Params List */
.docs-api-params-table {
  display: flex;
  flex-direction: column;
  border-top: 1px solid rgba(0, 0, 0, 0.05);
  margin-bottom: 20px;
}

body.dark .docs-api-params-table {
  border-top-color: rgba(255, 255, 255, 0.06);
}

.docs-api-param-row {
  padding: 11px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.05);
  display: flex;
  flex-direction: column;
  gap: 5px;
}

body.dark .docs-api-param-row {
  border-bottom-color: rgba(255, 255, 255, 0.06);
}

.docs-api-param-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.docs-api-param-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  font-weight: 700;
  color: #09090b;
}

body.dark .docs-api-param-name {
  color: #f4f4f5;
}

.docs-api-param-type {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  color: #71717a;
}

body.dark .docs-api-param-type {
  color: #a1a1aa;
}

.docs-api-param-badge {
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.04em;
  padding: 2px 6px;
  border-radius: 4px;
  text-transform: uppercase;
}

.docs-api-param-badge.required {
  background: rgba(239, 68, 68, 0.1);
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.2);
}

.docs-api-param-badge.optional {
  background: rgba(113, 113, 122, 0.1);
  color: #71717a;
  border: 1px solid rgba(113, 113, 122, 0.2);
}

.docs-api-param-desc {
  font-size: 13.5px;
  line-height: 1.5;
  color: #52525b;
}

body.dark .docs-api-param-desc {
  color: #a1a1aa;
}

.docs-api-no-params {
  font-size: 13px;
  color: #71717a;
  font-style: italic;
  padding: 10px 0;
  margin-bottom: 12px;
}

body.dark .docs-api-no-params {
  color: #a1a1aa;
}

/* Terminal Snippet Inside Card */
.docs-api-code-terminal {
  background: #09090b;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  overflow: hidden;
  margin-top: 14px;
}

.docs-api-code-terminal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 14px;
  background: #141417;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.docs-api-code-terminal-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.docs-api-terminal-lang {
  font-size: 12px;
  font-weight: 700;
  color: #e4e4e7;
}

.docs-api-terminal-sub {
  font-size: 11px;
  color: #71717a;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.docs-api-terminal-status-tag {
  font-size: 10.5px;
  font-weight: 700;
  color: #10b981;
  background: rgba(16, 185, 129, 0.15);
  padding: 2px 6px;
  border-radius: 4px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}

.docs-api-terminal-copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.12);
  color: #d4d4d8;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}

.docs-api-terminal-copy-btn:hover {
  background: rgba(255, 255, 255, 0.16);
  color: #ffffff;
}

.docs-api-code-terminal-content {
  padding: 14px 16px;
  overflow-x: auto;
}

.docs-api-code-terminal-content pre {
  margin: 0;
  padding: 0;
  background: transparent;
  border: none;
}

.docs-api-code-terminal-content code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12.5px;
  line-height: 1.55;
  color: #f4f4f5;
  white-space: pre;
}
"""

print("CSS generator ready")
