# Syte AI Agent Tools Reference

Complete reference of autonomous tools available to the Syte AI Agent.

## Planning & Orchestration
- `syte_create_plan(title, steps, rationale)`: Creates a structured multi-step plan, records it in session, and writes `plan.md` to workspace root.
- `syte_update_plan_step(step_id, status, notes)`: Updates step status (`pending`, `in_progress`, `completed`, `failed`) and synchronizes `plan.md`.

## Workspace & Filesystem
- `syte_read_file(path)`: Inspect full file contents with line counts and syntax highlighting.
- `syte_read_file_lines(path, start_line, end_line)`: Read precise line slices for token-efficient inspection.
- `syte_write_file(path, content, overwrite)`: Write complete, production-ready source code files.
- `syte_edit_file(path, target_snippet, replacement)`: Perform precise surgeon-like edits on existing files.
- `syte_search_files(query, file_pattern)`: Fast ripgrep across workspace code.
- `syte_list_workspace_files(directory, max_depth)`: Explore directory hierarchy and file trees.

## Machine Understanding & Quality
- `syte_analyze_project_structure()`: Deterministic (non-AI) project scanner detecting framework (Next.js, Vite, FastAPI, etc.), manifest dependencies, entrypoints, and syntax validity.
- `syte_security_lint_scan(path)`: Scans AST syntax and checks for security leaks or invalid syntax.

## Online Research & Documentation
- `syte_search_web(query, num_results)`: Searches public web and docs for real-time libraries, error messages, and package signatures.
- `syte_fetch_docs(url)`: Fetches clean documentation text from external technical references.

## Execution & Deployment
- `syte_run_command(command, timeout_seconds)`: Execute shell/bash terminal commands in the project VM environment.
- `syte_start_preview()`: Spin up a live hot-reloading development preview server on an isolated port.
- `syte_get_preview_status()`: Inspect running status and preview URL.
- `syte_create_deployment(branch, start_command)`: Trigger a zero-downtime production deployment (when requested by user).
- `syte_get_deployment_logs(limit, filter_keyword)`: Inspect and diagnose build/deployment logs.
- `syte_get_router_logs(search, status_code)`: Inspect incoming HTTP traffic logs.
