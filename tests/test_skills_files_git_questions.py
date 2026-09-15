"""Comprehensive test suite verifying:
1. Skills by responsibility and project skills management
2. Uploaded files persistence and context injection
3. Interactive question asking and resolution
4. Git operations with credentials injection (status, commit, diff, log)
5. MCP tools discovery and execution
"""

import asyncio
from pathlib import Path
import tempfile
import uuid
import pytest

from syte.database import (
    init_db,
    create_project,
    get_project,
    save_project_skill,
    list_project_skills,
    get_project_skill,
    set_project_skill_active,
    delete_project_skill,
    save_project_uploaded_file,
    list_project_uploaded_files,
    delete_project_uploaded_file,
)
from syte.ai.skills import (
    list_available_skills_for_project,
    get_skill_content_for_project,
    format_project_skills_for_prompt,
)
from syte.ai.tools import (
    execute_syte_tool,
    get_ai_tools_schema,
    _get_git_auth_options,
)


@pytest.mark.asyncio
async def test_skills_by_responsibility_lifecycle():
    await init_db()
    project_id = f"test_skills_{uuid.uuid4().hex[:8]}"
    await create_project({"id": project_id, "name": "Skills Test Project"})

    # 1. Save skills with different responsibilities
    skill_design = await save_project_skill(
        project_id=project_id,
        skill_id="skill_design_1",
        name="Tailwind Glassmorphism",
        responsibility="designing",
        description="Clean glassmorphism design language",
        content="# Designing Guidelines\nUse backdrop-blur-md with white/80 border.",
        active=True,
    )
    assert skill_design["responsibility"] == "designing"

    skill_integrate = await save_project_skill(
        project_id=project_id,
        skill_id="skill_stripe_1",
        name="Stripe Subscriptions",
        responsibility="integrating",
        description="Stripe checkout and webhook handling",
        content="# Stripe Guidelines\nUse webhooks with signature verification.",
        active=True,
    )
    assert skill_integrate["responsibility"] == "integrating"

    skill_build = await save_project_skill(
        project_id=project_id,
        skill_id="skill_build_1",
        name="Next.js App Router Architecture",
        responsibility="building",
        description="Server components and server actions",
        content="# Building Guidelines\nKeep client components at the leaves.",
        active=True,
    )
    assert skill_build["responsibility"] == "building"

    # 2. List available skills for project
    available = await list_available_skills_for_project(project_id)
    assert len(available) >= 3
    avail_ids = [s["id"] for s in available]
    assert "skill_design_1" in avail_ids
    assert "skill_stripe_1" in avail_ids
    assert "skill_build_1" in avail_ids

    # 3. Retrieve skill content for project
    content = await get_skill_content_for_project("Tailwind Glassmorphism", project_id)
    assert content is not None
    assert "backdrop-blur-md" in content

    # 4. Format skills for prompt grouped by responsibility
    prompt_str = await format_project_skills_for_prompt(project_id)
    assert "ACTIVE SKILLS BY RESPONSIBILITY" in prompt_str
    assert "DESIGNING" in prompt_str
    assert "INTEGRATING" in prompt_str
    assert "BUILDING" in prompt_str
    assert "Tailwind Glassmorphism" in prompt_str
    assert "Stripe Subscriptions" in prompt_str

    # 5. Disable skill and check it's excluded from active prompt
    await set_project_skill_active(project_id, "skill_design_1", False)
    prompt_after = await format_project_skills_for_prompt(project_id)
    assert "Tailwind Glassmorphism" not in prompt_after

    # 6. Delete skill
    deleted = await delete_project_skill(project_id, "skill_stripe_1")
    assert deleted is True
    skills_after = await list_project_skills(project_id)
    assert not any(s["id"] == "skill_stripe_1" for s in skills_after)


@pytest.mark.asyncio
async def test_uploaded_files_persistence_and_tools():
    await init_db()
    project_id = f"test_files_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        await create_project({"id": project_id, "name": "Files Test Project", "workspace_dir": str(tmp_path)})

        # Save an uploaded file
        uploads_dir = tmp_path / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        csv_file = uploads_dir / "sales_data.csv"
        csv_file.write_text("product,sales,revenue\nWidget,100,5000\nGadget,50,7500\n", encoding="utf-8")

        record = await save_project_uploaded_file(
            project_id=project_id,
            filename="sales_data.csv",
            file_path="uploads/sales_data.csv",
            file_size=len(csv_file.read_bytes()),
            extension=".csv",
            summary="CSV file with 2 rows, 3 columns: product, sales, revenue",
            parsed_content="Preview:\nproduct, sales, revenue\nWidget, 100, 5000",
        )
        assert record["filename"] == "sales_data.csv"

        # List files
        files = await list_project_uploaded_files(project_id)
        assert len(files) == 1
        assert files[0]["file_path"] == "uploads/sales_data.csv"

        # Agent reads the uploaded file via syte_read_file
        project = await get_project(project_id)
        project["workspace_dir"] = str(tmp_path)
        res_read = await execute_syte_tool(project, "syte_read_file", {"path": "uploads/sales_data.csv"})
        assert res_read.get("ok") is True
        assert "Widget,100,5000" in res_read.get("content", "")

        # Delete file record
        await delete_project_uploaded_file(project_id, record["id"])
        files_after = await list_project_uploaded_files(project_id)
        assert len(files_after) == 0


@pytest.mark.asyncio
async def test_git_tools_with_credentials_injection():
    await init_db()
    project_id = f"test_git_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        # Initialize a real git repo
        p_init = await asyncio.create_subprocess_exec("git", "init", cwd=str(tmp_path), stdout=asyncio.subprocess.PIPE)
        await p_init.communicate()

        await create_project({"id": project_id, "name": "Git Test Project", "workspace_dir": str(tmp_path)})
        project = await get_project(project_id)
        project["workspace_dir"] = str(tmp_path)

        # 1. Test _get_git_auth_options with custom user credentials
        creds = {
            "github_token": "ghp_mock_token_12345",
            "git_name": "Test Developer",
            "git_email": "dev@test.org",
        }
        git_auth = await _get_git_auth_options(project_id, project, credentials=creds)
        assert git_auth["token"] == "ghp_mock_token_12345"
        assert git_auth["user_name"] == "Test Developer"
        assert git_auth["user_email"] == "dev@test.org"
        assert any("user.name=Test Developer" in arg for arg in git_auth["git_config_args"])
        assert any("Authorization: token ghp_mock_token_12345" in arg for arg in git_auth["git_config_args"])

        # 2. Write a file in workspace
        test_file = tmp_path / "hello.txt"
        test_file.write_text("Initial commit test", encoding="utf-8")

        # 3. Test syte_git_status
        status_res = await execute_syte_tool(project, "syte_git_status", {}, credentials=creds)
        assert status_res.get("ok") is True
        assert "hello.txt" in status_res.get("status", "")

        # 4. Test syte_git_commit with credentials
        commit_res = await execute_syte_tool(project, "syte_git_commit", {"message": "feat: add hello.txt"}, credentials=creds)
        assert commit_res.get("ok") is True

        # 5. Test syte_git_log
        log_res = await execute_syte_tool(project, "syte_git_log", {"limit": 5}, credentials=creds)
        assert log_res.get("ok") is True
        assert log_res.get("count", 0) >= 1
        assert "feat: add hello.txt" in log_res["commits"][0]

        # 6. Make a modification and test syte_git_diff
        test_file.write_text("Modified content", encoding="utf-8")
        diff_res = await execute_syte_tool(project, "syte_git_diff", {}, credentials=creds)
        assert diff_res.get("ok") is True
        assert "Initial commit test" in diff_res.get("diff", "")
        assert "Modified content" in diff_res.get("diff", "")


@pytest.mark.asyncio
async def test_mcp_and_question_tools():
    await init_db()
    project_id = f"test_misc_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        await create_project({"id": project_id, "name": "Misc Test Project", "workspace_dir": str(tmp_path)})
        project = await get_project(project_id)

        # 1. Test syte_ask_question
        q_res = await execute_syte_tool(
            project,
            "syte_ask_question",
            {"question": "Do you want dark mode?", "options": ["Dark", "Light"], "allow_custom": True},
        )
        assert q_res.get("ok") is True
        assert q_res.get("requires_user_input") is True
        assert q_res.get("question") == "Do you want dark mode?"
        assert q_res.get("options") == ["Dark", "Light"]

        # 2. Test syte_mcp_list
        mcp_list = await execute_syte_tool(project, "syte_mcp_list", {})
        assert mcp_list.get("ok") is True
        assert len(mcp_list.get("addons", [])) >= 1

        # 3. Test tool schemas includes new tools
        schemas = get_ai_tools_schema()
        tool_names = [s["function"]["name"] for s in schemas]
        assert "syte_git_diff" in tool_names
        assert "syte_git_log" in tool_names
        assert "syte_git_clone" in tool_names
        assert "syte_mcp_list" in tool_names
        assert "syte_mcp_call" in tool_names
        assert "syte_ask_question" in tool_names


@pytest.mark.asyncio
async def test_api_endpoints_skills_files_questions():
    """Verify live API routes for skills upload/management, file uploads, and question answers."""
    await init_db()
    project_id = f"test_api_{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        await create_project({"id": project_id, "name": "API Test Project", "workspace_dir": str(tmp_path)})

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from syte import api_router
        from syte.ai import router as ai_router
        from syte.auth import verify_api_token, verify_operator_session_or_token

        app = FastAPI()
        app.include_router(api_router.router, prefix="/api")
        app.include_router(ai_router.router)
        app.dependency_overrides[verify_api_token] = lambda: {"id": "tester"}
        app.dependency_overrides[verify_operator_session_or_token] = lambda: {"id": "tester"}
        client = TestClient(app)

        # 1. POST /api/agent_skills_add
        add_res = client.post(
            "/api/agent_skills_add",
            json={
                "uuid": project_id,
                "name": "Framer Motion Animations",
                "responsibility": "designing",
                "description": "Smooth UI transitions",
                "content": "# Framer Motion\nAlways use spring physics for enters.",
                "enable": True,
            },
        )
        assert add_res.status_code == 200
        data = add_res.json()
        assert data.get("ok") is True
        assert data.get("skill", {}).get("name") == "Framer Motion Animations"
        assert data.get("skill", {}).get("responsibility") == "designing"
        skill_id = data.get("skill", {}).get("id")

        # 2. GET /api/agent_skills?uuid=...
        list_res = client.get(f"/api/agent_skills?uuid={project_id}")
        assert list_res.status_code == 200
        skills_data = list_res.json()
        assert skills_data.get("ok") is True
        names = [s["name"] for s in skills_data.get("skills", [])]
        assert "Framer Motion Animations" in names

        # 3. POST /api/agent_skills_disable
        dis_res = client.post("/api/agent_skills_disable", json={"uuid": project_id, "skill_id": skill_id})
        assert dis_res.status_code == 200
        assert dis_res.json().get("ok") is True

        # 4. POST /api/agent_skills_enable
        en_res = client.post("/api/agent_skills_enable", json={"uuid": project_id, "skill_id": skill_id})
        assert en_res.status_code == 200
        assert en_res.json().get("ok") is True

        # 5. POST /api/projects/{uuid}/ai/upload
        upload_res = client.post(
            f"/api/projects/{project_id}/ai/upload",
            files={"files": ("schema.prisma", b"model User { id String @id }", "text/plain")},
        )
        assert upload_res.status_code == 200
        upload_data = upload_res.json()
        assert upload_data.get("ok") is True
        assert upload_data.get("total_files", 0) >= 1
        assert any("schema.prisma" in f.get("filename", "") for f in upload_data.get("files", []))

        # Check file was saved in workspace uploads
        uploaded_files = await list_project_uploaded_files(project_id)
        assert len(uploaded_files) >= 1

        # 6. POST /api/agent_answer_question
        ans_res = client.post(
            "/api/agent_answer_question",
            json={
                "uuid": project_id,
                "question_id": "q_test_123",
                "answer": "Option A selected",
            },
        )
        assert ans_res.status_code == 200
        ans_data = ans_res.json()
        assert ans_data.get("ok") is True
        assert ans_data.get("question_id") == "q_test_123"
        assert ans_data.get("answer") == "Option A selected"

        # 7. POST /api/projects/{uuid}/agent/questions/{question_id}/answer
        path_ans_res = client.post(
            f"/api/projects/{project_id}/agent/questions/q_test_456/answer",
            json={"answer": ["choice1", "choice2"]},
        )
        assert path_ans_res.status_code == 200
        path_data = path_ans_res.json()
        assert path_data.get("ok") is True
        assert path_data.get("question_id") == "q_test_456"

        # 8. POST /api/agent_skills_delete
        del_res = client.post("/api/agent_skills_delete", json={"uuid": project_id, "skill_id": skill_id})
        assert del_res.status_code == 200
        assert del_res.json().get("ok") is True

        # 9. POST /api/agent_change with dictionary credentials
        change_res = client.post(
            "/api/agent_change",
            json={
                "uuid": project_id,
                "message": "Update git repo",
                "credentials": {
                    "git_name": "Dávid Márton",
                    "git_email": "dmarton336@gmail.com",
                    "github_token": "ghp_test123",
                },
            },
        )
        assert change_res.status_code == 200
        assert change_res.json().get("ok") is True

