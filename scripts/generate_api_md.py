#!/usr/bin/env python3
import json
import sys
import os

sys.path.insert(0, "/root/syte")
from scripts.generate_full_docs import API_ENDPOINTS

def generate_markdown():
    md = []
    md.append("# Syte Platform API Reference (v2.4.0)")
    md.append("\nThis document contains the complete specification of all **113 backend API endpoints** available in the Syte deployment platform.\n")

    # Group by category
    categories = {}
    for ep in API_ENDPOINTS:
        cat = ep["group"]
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(ep)

    # Table of Contents
    md.append("## Table of Contents")
    for cat, eps in categories.items():
        slug = cat.lower().replace(" ", "-").replace("&", "")
        md.append(f"- [{cat}](#{slug}) ({len(eps)} endpoints)")
    md.append("\n---\n")

    for cat, eps in categories.items():
        slug = cat.lower().replace(" ", "-").replace("&", "")
        md.append(f"## {cat}\n")
        for ep in eps:
            md.append(f"### `{ep['method']}` {ep['path']}")
            md.append(f"**{ep['title']}** — {ep['summary']}\n")
            
            if ep.get("pathParams"):
                md.append("#### Path Parameters")
                md.append("| Name | Type | Required | Description |")
                md.append("| :--- | :--- | :--- | :--- |")
                for p in ep["pathParams"]:
                    req = "**Yes**" if p.get("required") else "No"
                    md.append(f"| `{p['name']}` | `{p['type']}` | {req} | {p['desc']} |")
                md.append("")
                
            if ep.get("queryParams"):
                md.append("#### Query Parameters")
                md.append("| Name | Type | Required | Description |")
                md.append("| :--- | :--- | :--- | :--- |")
                for p in ep["queryParams"]:
                    req = "**Yes**" if p.get("required") else "No"
                    md.append(f"| `{p['name']}` | `{p['type']}` | {req} | {p['desc']} |")
                md.append("")
                
            if ep.get("bodyParams"):
                c_type = ep.get("contentType", "application/json")
                md.append(f"#### Request Body (`{c_type}`)")
                md.append("| Field | Type | Required | Description |")
                md.append("| :--- | :--- | :--- | :--- |")
                for p in ep["bodyParams"]:
                    req = "**Yes**" if p.get("required") else "No"
                    md.append(f"| `{p['name']}` | `{p['type']}` | {req} | {p['desc']} |")
                md.append("")
                
            md.append("#### Example Request (cURL)")
            md.append("```bash")
            md.append(ep.get("curlCommand", ""))
            md.append("```\n")
            
            if ep.get("responseSchema"):
                res_status = ep.get("responseStatus", "200 OK")
                md.append(f"#### Response Schema ({res_status})")
                md.append("| Field | Type | Description |")
                md.append("| :--- | :--- | :--- |")
                for s in ep["responseSchema"]:
                    md.append(f"| `{s['name']}` | `{s['type']}` | {s['desc']} |")
                md.append("")
                
            if ep.get("responseJson"):
                md.append("#### Example Response (JSON)")
                md.append("```json")
                md.append(ep["responseJson"])
                md.append("```\n")
                
            md.append("---\n")

    full_doc = "\n".join(md)
    with open("/root/syte/docs/API_REFERENCE.md", "w", encoding="utf-8") as f:
        f.write(full_doc)

    with open("/root/syte/API_DOCUMENTATION.md", "w", encoding="utf-8") as f:
        f.write(full_doc)

    print("Generated docs/API_REFERENCE.md and API_DOCUMENTATION.md successfully!")

if __name__ == "__main__":
    generate_markdown()
