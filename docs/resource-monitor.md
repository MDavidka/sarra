# Resource monitor

This branch adds a small backend helper for service-level resource reporting.

## Service buckets

- **Websites** — aggregate all running project processes
- **Caddy** — aggregate the reverse proxy / TLS service processes
- **Main GUI** — the Syte web app process itself

## Example output

```json
{
  "ok": true,
  "sample_ms": 120,
  "services": [
    {
      "service_type": "websites",
      "label": "Websites",
      "name": "websites",
      "cpu_percent": 12.4,
      "memory_mb": 418.2,
      "instances": 3,
      "pids": [1234, 1250, 1261],
      "children": ["marketing-site", "docs", "api"]
    },
    {
      "service_type": "caddy",
      "label": "Caddy",
      "name": "caddy",
      "cpu_percent": 1.8,
      "memory_mb": 34.0,
      "instances": 1,
      "pids": [900]
    },
    {
      "service_type": "main_gui",
      "label": "Main GUI",
      "name": "main gui",
      "cpu_percent": 4.1,
      "memory_mb": 152.0,
      "instances": 1,
      "pids": [4312]
    }
  ]
}
```

## Next step

Wire `syte.resource_monitor.get_resource_monitor_snapshot()` into the API and dashboard so the GUI can render the card view directly.
