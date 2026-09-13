#!/usr/bin/env python3
import json
import sys
import os

sys.path.insert(0, "/root/syte")
from scripts.generate_full_docs import API_ENDPOINTS

def generate_markdown():
    md = []
    md.append("# Syte Platform API Reference (v2.4.0)")
    md.append("\nThis document contains the complete developer specification for all **113 backend API endpoints** available in the Syte deployment platform, including authentication, rate limits, headers, parameters, code samples, and response schemas.\n")

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
            is_public = ep["path"] in ["/api/health", "/api/auth/setup", "/api/auth/login", "/api/notifications/push/vapid-public-key"]
            auth_str = "Public (No Auth Required)" if is_public else "Bearer Token (JWT / API Token)"
            rate_limit = "120 req/min" if is_public else "60 req/min"

            md.append(f"### `{ep['method']}` {ep['path']}")
            md.append(f"**{ep['title']}** — {ep['summary']}\n")
            md.append(f"- **Authentication**: `{auth_str}`")
            md.append(f"- **Content-Type**: `{ep.get('contentType', 'application/json')}`")
            md.append(f"- **Rate Limit**: `{rate_limit}`\n")
            
            # Headers
            md.append("#### Request Headers")
            md.append("| Header | Type | Required | Description |")
            md.append("| :--- | :--- | :--- | :--- |")
            if not is_public:
                md.append("| `Authorization` | `string` | **Yes** | Bearer authentication token (`Bearer <token>`) |")
            if ep.get("contentType") == "application/json":
                md.append("| `Content-Type` | `string` | **Yes** | `application/json` |")
            elif ep.get("contentType") == "multipart/form-data":
                md.append("| `Content-Type` | `string` | **Yes** | `multipart/form-data` |")
            md.append("| `Accept` | `string` | No | `application/json` |\n")

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

            md.append("#### HTTP Status Codes")
            md.append("| Status Code | Meaning | Description |")
            md.append("| :--- | :--- | :--- |")
            res_code = 200 if ep.get("responseStatus") == "200 OK" else (201 if "201" in ep.get("responseStatus", "") else 200)
            md.append(f"| `{res_code}` | `{ep.get('responseStatus', '200 OK')}` | Request succeeded. |")
            if not is_public:
                md.append("| `401` | `Unauthorized` | Missing or expired authorization token. |")
            if ep.get("pathParams"):
                md.append("| `404` | `Not Found` | Target resource identifier was not found. |")
            if ep.get("bodyParams"):
                md.append("| `400` | `Bad Request` | Request payload failed syntax or schema validation. |\n")
            else:
                md.append("")
            
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

    print("Updated docs/API_REFERENCE.md and API_DOCUMENTATION.md with rich developer details!")

if __name__ == "__main__":
    generate_markdown()
