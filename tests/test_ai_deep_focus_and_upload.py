import io
import json
import zipfile
import pytest
from pathlib import Path

from syte.ai.file_parser import parse_uploaded_file, extract_zip_to_workspace
from syte.ai.deep_focus import build_deep_focus_index, format_deep_focus_for_prompt
from syte.database import init_db, get_project_deep_focus, save_project_deep_focus
from syte.ai.tools import get_ai_tools_schema, execute_syte_tool


def test_file_parser_csv():
    data = b"product,price,stock\nWidget A,19.99,100\nWidget B,29.99,50"
    res = parse_uploaded_file("inventory.csv", data)
    assert "3 rows, 3 columns" in res["summary"]
    assert "Widget A" in res["parsed_content"]


def test_file_parser_zip_and_safe_extract(tmp_path):
    z_buf = io.BytesIO()
    with zipfile.ZipFile(z_buf, "w") as zf:
        zf.writestr("app/main.py", "print('hello world')")
        zf.writestr("README.md", "# Project Documentation")
    
    parsed = parse_uploaded_file("bundle.zip", z_buf.getvalue())
    assert "Zip archive containing 2 files" in parsed["summary"]
    assert "app/main.py" in parsed["parsed_content"]

    ext_res = extract_zip_to_workspace(z_buf.getvalue(), tmp_path / "ws")
    assert ext_res["ok"] is True
    assert (tmp_path / "ws/app/main.py").exists()
    assert (tmp_path / "ws/README.md").exists()


@pytest.mark.asyncio
async def test_deep_focus_and_project_memory_lifecycle():
    await init_db()
    # 1. Save and retrieve deep focus memory
    saved = await save_project_deep_focus("global", custom_memory="Use Tailwind and shadcn UI components.")
    assert saved["custom_memory"] == "Use Tailwind and shadcn UI components."

    fetched = await get_project_deep_focus("global")
    assert fetched["custom_memory"] == "Use Tailwind and shadcn UI components."

    # 2. Build index
    df = await build_deep_focus_index("global", custom_memory=fetched["custom_memory"])
    assert df["project_name"] == "Global Platform Host"

    # 3. Format prompt block
    prompt_str = format_deep_focus_for_prompt(df)
    assert "--- DEEP FOCUS: GLOBAL PLATFORM MEMORY ---" in prompt_str
    assert "Use Tailwind and shadcn UI components." in prompt_str


def test_deep_focus_tool_schemas():
    schema = get_ai_tools_schema()
    names = [s["function"]["name"] for s in schema]
    assert "syte_get_deep_focus" in names
    assert "syte_update_deep_focus" in names


@pytest.mark.asyncio
async def test_deep_focus_tool_execution():
    await init_db()
    project = {"id": "global", "name": "Global Platform"}
    
    # 1. Update deep focus via tool
    res_update = await execute_syte_tool(
        project,
        "syte_update_deep_focus",
        {"notes": "Strictly enforce TypeScript strict mode."}
    )
    assert res_update["ok"] is True
    assert "Strictly enforce TypeScript strict mode." in res_update["deep_focus"]["custom_memory"]

    # 2. Get deep focus via tool
    res_get = await execute_syte_tool(
        project,
        "syte_get_deep_focus",
        {}
    )
    assert res_get["ok"] is True
    assert "Strictly enforce TypeScript strict mode." in res_get["deep_focus"]["custom_memory"]
