"""File parser and extractor for Syte AI Builder.

Handles parsing, content extraction, and structured AI summaries for:
- .zip archives (directory tree, text extraction, safe unpacking)
- Excel spreadsheets (.xlsx, .xls) (sheet names, row/column counts, table markdown)
- Word documents (.docx, .doc) (headings, paragraphs, tables)
- PDF documents (.pdf) (page-by-page text)
- CSV / TSV tabular files
- Source code, text, markdown, JSON, YAML, XML, configs
"""

from __future__ import annotations

import csv
import io
import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional
import zipfile

logger = logging.getLogger("syte.ai.file_parser")

ZIP_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".next", "dist", "build", ".turbo", ".cache",
}

TEXT_EXTENSIONS = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".css", ".scss", ".sass", ".less", ".js", ".mjs", ".cjs", ".ts",
    ".tsx", ".jsx", ".py", ".sh", ".bash", ".sql", ".env", ".toml",
    ".ini", ".cfg", ".conf", ".rs", ".go", ".c", ".cpp", ".h", ".hpp",
    ".java", ".kt", ".swift", ".php", ".rb", ".graphql", ".gql",
    ".prisma", ".vue", ".svelte", ".astro", ".csv", ".tsv", ".log",
    ".dockerfile", ".makefile", ".lock",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svgz",
    ".mp3", ".wav", ".ogg", ".mp4", ".mov", ".avi", ".webm",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".7z", ".rar",
    ".exe", ".bin", ".dll", ".so", ".dylib", ".wasm",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".iso", ".dmg", ".pkg", ".deb", ".rpm",
    ".pyc", ".pyo", ".pyd", ".class", ".o", ".obj",
}

IMAGE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_EXTRACT_CHARS = 100000


def is_binary_bytes(data: bytes) -> bool:
    if not data:
        return False
    if b"\x00" in data[:4096]:
        return True
    try:
        sample = data[:2048].decode("utf-8")
        control_chars = sum(1 for c in sample if ord(c) < 32 and c not in "\n\r\t")
        if len(sample) > 0 and (control_chars / len(sample)) > 0.05:
            return True
        return False
    except UnicodeDecodeError:
        return True


def parse_csv_content(content_bytes: bytes, max_rows: int = 50) -> Dict[str, Any]:
    try:
        text = content_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = content_bytes.decode("latin-1", errors="replace")

    sample = text[:2048]
    delimiter = "\t" if "\t" in sample and sample.count("\t") > sample.count(",") else ","
    
    rows = []
    try:
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in reader:
            if row:
                rows.append([c.strip() for c in row])
    except Exception as e:
        return {
            "summary": f"CSV parse error: {e}",
            "text": text[:MAX_EXTRACT_CHARS],
            "rows_count": 0,
            "columns_count": 0,
        }

    total_rows = len(rows)
    if not rows:
        return {"summary": "Empty CSV file", "text": "", "rows_count": 0, "columns_count": 0}

    headers = rows[0]
    col_count = len(headers)
    preview_rows = rows[:max_rows]

    md_lines = []
    md_lines.append("| " + " | ".join(headers) + " |")
    md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
    for r in preview_rows[1:]:
        padded = (r + [""] * col_count)[:col_count]
        md_lines.append("| " + " | ".join(c.replace("\n", " ") for c in padded) + " |")

    table_md = "\n".join(md_lines)
    summary = f"{total_rows} rows, {col_count} columns"
    if total_rows > max_rows:
        summary += f" (previewing first {max_rows} rows)"

    return {
        "summary": summary,
        "text": table_md,
        "rows_count": total_rows,
        "columns_count": col_count,
        "headers": headers,
    }


def parse_excel_content(content_bytes: bytes, max_rows_per_sheet: int = 40) -> Dict[str, Any]:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content_bytes), data_only=True, read_only=True)
        sheets_data = []
        full_text_blocks = []

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_rows = []
            row_count = 0
            for row in ws.iter_rows(values_only=True):
                if row is None:
                    continue
                if not any(c is not None and str(c).strip() != "" for c in row):
                    continue
                row_count += 1
                if row_count <= max_rows_per_sheet:
                    sheet_rows.append([str(c) if c is not None else "" for c in row])

            if not sheet_rows:
                sheets_data.append({"sheet_name": sheet_name, "rows_count": 0, "cols_count": 0, "summary": "Empty sheet"})
                continue

            headers = sheet_rows[0]
            col_count = len(headers)
            
            md_lines = [f"### Sheet: {sheet_name} ({row_count} rows, {col_count} columns)\n"]
            md_lines.append("| " + " | ".join(h if h else f"Col{i+1}" for i, h in enumerate(headers)) + " |")
            md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
            for r in sheet_rows[1:]:
                padded = (r + [""] * col_count)[:col_count]
                md_lines.append("| " + " | ".join(c.replace("\n", " ").replace("|", "\\|") for c in padded) + " |")

            sheet_md = "\n".join(md_lines)
            full_text_blocks.append(sheet_md)
            sheets_data.append({
                "sheet_name": sheet_name,
                "rows_count": row_count,
                "cols_count": col_count,
                "headers": headers,
            })

        wb.close()
        combined_text = "\n\n".join(full_text_blocks)
        return {
            "summary": f"Excel Workbook with {len(sheets_data)} sheet(s): " + ", ".join(f"{s["sheet_name"]} ({s["rows_count"]} rows)" for s in sheets_data),
            "text": combined_text,
            "sheets": sheets_data,
        }
    except Exception as e:
        logger.warning("Error parsing Excel file: %s", e)
        return {
            "summary": f"Excel file (could not parse fully: {e})",
            "text": f"[Excel workbook binary data - {len(content_bytes)} bytes]",
            "sheets": [],
            "error": str(e),
        }


def parse_word_content(content_bytes: bytes) -> Dict[str, Any]:
    try:
        import docx
        doc = docx.Document(io.BytesIO(content_bytes))
        paragraphs = []
        
        for p in doc.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            style_name = p.style.name.lower() if p.style else ""
            if "heading 1" in style_name:
                paragraphs.append(f"# {text}")
            elif "heading 2" in style_name:
                paragraphs.append(f"## {text}")
            elif "heading 3" in style_name:
                paragraphs.append(f"### {text}")
            else:
                paragraphs.append(text)

        table_blocks = []
        for t_idx, table in enumerate(doc.tables, 1):
            t_rows = []
            for row in table.rows:
                t_rows.append([cell.text.strip().replace("\n", " ") for cell in row.cells])
            if t_rows:
                headers = t_rows[0]
                cols = len(headers)
                t_md = [f"\n**Table {t_idx}**:\n", "| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * cols) + " |"]
                for r in t_rows[1:]:
                    padded = (r + [""] * cols)[:cols]
                    t_md.append("| " + " | ".join(padded) + " |")
                table_blocks.append("\n".join(t_md))

        all_text = "\n\n".join(paragraphs)
        if table_blocks:
            all_text += "\n\n" + "\n\n".join(table_blocks)

        summary = f"Word Document with {len(paragraphs)} paragraph(s) and {len(doc.tables)} table(s)"
        return {
            "summary": summary,
            "text": all_text[:MAX_EXTRACT_CHARS],
            "paragraphs_count": len(paragraphs),
            "tables_count": len(doc.tables),
        }
    except Exception as e:
        logger.warning("Error parsing Word document: %s", e)
        return {
            "summary": f"Word document (parsing fallback: {e})",
            "text": f"[Word document binary data - {len(content_bytes)} bytes]",
            "error": str(e),
        }


def parse_pdf_content(content_bytes: bytes, max_pages: int = 50) -> Dict[str, Any]:
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(content_bytes))
        total_pages = len(reader.pages)
        pages_text = []

        for p_idx in range(min(total_pages, max_pages)):
            page = reader.pages[p_idx]
            txt = page.extract_text() or ""
            if txt.strip():
                pages_text.append(f"--- Page {p_idx + 1} ---\n{txt.strip()}")

        combined_text = "\n\n".join(pages_text)
        summary = f"PDF Document with {total_pages} page(s)"
        if total_pages > max_pages:
            summary += f" (extracted first {max_pages} pages)"

        return {
            "summary": summary,
            "text": combined_text[:MAX_EXTRACT_CHARS],
            "pages_count": total_pages,
        }
    except Exception as e:
        logger.warning("Error parsing PDF file: %s", e)
        return {
            "summary": f"PDF document (parse fallback: {e})",
            "text": f"[PDF binary data - {len(content_bytes)} bytes]",
            "error": str(e),
        }


def parse_zip_content(content_bytes: bytes, max_files: int = 50) -> Dict[str, Any]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content_bytes))
        all_entries = zf.infolist()
        total_entries = len(all_entries)

        file_tree: List[str] = []
        extracted_files: List[Dict[str, Any]] = []
        total_uncompressed_bytes = 0

        for info in all_entries:
            name = info.filename
            parts = Path(name).parts
            if any(p.startswith(".") and p not in (".", "..") for p in parts) and not any(p in (".env", ".gitignore") for p in parts):
                if any(ign in parts for ign in ZIP_IGNORE_DIRS):
                    continue
            if any(ign in parts for ign in ZIP_IGNORE_DIRS):
                continue

            if info.is_dir():
                file_tree.append(f"📁 {name}")
                continue

            file_tree.append(f"📄 {name} ({info.file_size} bytes)")
            total_uncompressed_bytes += info.file_size

            ext = Path(name).suffix.lower()
            if ext in TEXT_EXTENSIONS and ext not in BINARY_EXTENSIONS:
                if len(extracted_files) < max_files and info.file_size < 250000:
                    try:
                        raw = zf.read(info.filename)
                        if not is_binary_bytes(raw[:1024]):
                            try:
                                decoded = raw.decode("utf-8")
                            except UnicodeDecodeError:
                                decoded = raw.decode("utf-8", errors="replace")
                            
                            if len(decoded) > 12000:
                                sample = decoded[:8000] + f"\n... [truncated {len(decoded)} chars] ...\n" + decoded[-2000:]
                            else:
                                sample = decoded

                            extracted_files.append({
                                "path": name,
                                "size_bytes": info.file_size,
                                "content": sample,
                            })
                    except Exception:
                        pass

        summary = f"Zip archive containing {total_entries} files ({round(total_uncompressed_bytes / 1024, 1)} KB unpacked)"
        
        preview_blocks = [
            f"# Archive Contents: {summary}\n",
            "## File Structure:",
            "\n".join(file_tree[:60]),
        ]
        if len(file_tree) > 60:
            preview_blocks.append(f"... and {len(file_tree) - 60} more files.")

        if extracted_files:
            preview_blocks.append(f"\n## Extracted Key Files ({len(extracted_files)} files):")
            for ef in extracted_files:
                ext_name = Path(ef["path"]).suffix.lstrip(".") or "text"
                preview_blocks.append(f"\n### File: `{ef['path']}`\n```{ext_name}\n{ef['content']}\n```")

        return {
            "summary": summary,
            "text": "\n".join(preview_blocks)[:MAX_EXTRACT_CHARS],
            "total_files": total_entries,
            "extracted_count": len(extracted_files),
            "file_tree": file_tree[:100],
            "files": extracted_files,
        }
    except Exception as e:
        logger.warning("Error parsing Zip archive: %s", e)
        return {
            "summary": f"Zip archive (could not extract: {e})",
            "text": f"[Zip archive binary data - {len(content_bytes)} bytes]",
            "error": str(e),
        }


def extract_zip_to_workspace(zip_bytes: bytes, target_dir: Path) -> Dict[str, Any]:
    target_dir.mkdir(parents=True, exist_ok=True)
    resolved_target = target_dir.resolve()
    extracted_paths = []
    skipped_paths = []

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        for member in zf.infolist():
            member_path = member.filename.replace("\\", "/")
            dest_path = (resolved_target / member_path).resolve()
            if not dest_path.is_relative_to(resolved_target):
                skipped_paths.append(member.filename)
                continue

            if member.is_dir():
                dest_path.mkdir(parents=True, exist_ok=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, open(dest_path, "wb") as target:
                    shutil.copyfileobj(source, target)
                extracted_paths.append(str(dest_path.relative_to(resolved_target)))

        return {
            "ok": True,
            "extracted_count": len(extracted_paths),
            "extracted_paths": extracted_paths[:50],
            "skipped_count": len(skipped_paths),
        }
    except Exception as e:
        logger.error("Failed to extract zip to workspace: %s", e)
        return {"ok": False, "error": str(e)}


def parse_uploaded_file(filename: str, content: bytes) -> Dict[str, Any]:
    clean_name = Path(filename).name
    ext = Path(clean_name).suffix.lower()
    size_bytes = len(content)

    if ext == ".zip":
        result = parse_zip_content(content)
    elif ext in (".xlsx", ".xls"):
        result = parse_excel_content(content)
    elif ext in (".docx", ".doc"):
        result = parse_word_content(content)
    elif ext == ".pdf":
        result = parse_pdf_content(content)
    elif ext in (".csv", ".tsv"):
        result = parse_csv_content(content)
    elif ext in (".json", ".jsonc"):
        try:
            txt = content.decode("utf-8", errors="replace")
            parsed_json = json.loads(txt)
            formatted = json.dumps(parsed_json, indent=2)
            result = {
                "summary": f"JSON data with {len(parsed_json) if isinstance(parsed_json, (dict, list)) else 1} top-level items",
                "text": f"```json\n{formatted[:MAX_EXTRACT_CHARS]}\n```",
            }
        except Exception:
            txt = content.decode("utf-8", errors="replace")
            result = {"summary": "JSON file", "text": f"```json\n{txt[:MAX_EXTRACT_CHARS]}\n```"}
    elif ext in BINARY_EXTENSIONS or is_binary_bytes(content[:4096]):
        if ext in IMAGE_EXTENSIONS:
            result = {
                "summary": f"Image file: {clean_name} ({round(size_bytes/1024, 1)} KB)",
                "text": f"[Image asset: `{clean_name}` ({round(size_bytes/1024, 1)} KB)]",
                "is_image": True,
            }
        else:
            result = {
                "summary": f"Binary file: {clean_name} ({round(size_bytes/1024, 1)} KB)",
                "text": f"[Binary asset: `{clean_name}` ({size_bytes} bytes)]",
                "is_binary": True,
            }
    else:
        try:
            txt = content.decode("utf-8")
        except UnicodeDecodeError:
            txt = content.decode("utf-8", errors="replace")

        lang = ext.lstrip(".") or "text"
        lines = len(txt.splitlines())
        result = {
            "summary": f"{lang.upper()} source file ({lines} lines, {round(size_bytes/1024, 1)} KB)",
            "text": f"```{lang}\n{txt[:MAX_EXTRACT_CHARS]}\n```",
            "lines_count": lines,
        }

    return {
        "filename": clean_name,
        "extension": ext,
        "size_bytes": size_bytes,
        "summary": result.get("summary", f"{clean_name} ({size_bytes} bytes)"),
        "parsed_content": result.get("text", ""),
        "details": result,
    }
