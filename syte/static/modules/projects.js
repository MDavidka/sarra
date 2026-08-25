import { escapeHtml, request } from "./api.js";

function card(project) {
  const name = escapeHtml(project.name || project.id);
  const status = escapeHtml(project.status || (project.running ? "running" : "stopped"));
  const destination = escapeHtml(project.domain_url || project.url || project.git_url || "No public URL configured");
  return `<article class="project-card" data-project-id="${escapeHtml(project.id)}">
    <div><h3>${name}</h3><p class="project-meta">${destination}</p><span class="project-status">${status}</span></div>
    <div class="project-actions">
      <button class="button button-secondary" type="button" data-project-action="deploy">Deploy</button>
      <button class="button button-secondary" type="button" data-project-action="start">Start</button>
      <button class="button button-secondary" type="button" data-project-action="stop">Stop</button>
      <button class="button button-danger" type="button" data-project-action="delete">Delete</button>
    </div>
  </article>`;
}

export function setupProjects({ notify }) {
  const homeList = document.querySelector("#home-project-list");
  const projectList = document.querySelector("#project-list");
  const form = document.querySelector("#project-create-form");
  let projects = [];

  function render() {
    const markup = projects.length ? projects.map(card).join("") : '<div class="empty-state">No projects have been registered yet.</div>';
    homeList.innerHTML = markup;
    projectList.innerHTML = markup;
  }

  async function refresh() {
    try {
      projects = await request("/api/projects");
      render();
    } catch (error) {
      notify(error.message, true);
    }
  }

  async function act(button) {
    const projectId = button.closest("[data-project-id]")?.dataset.projectId;
    const action = button.dataset.projectAction;
    if (!projectId || !action) return;
    const paths = { deploy: "/api/issue_deploy", start: "/api/start_service", stop: "/api/stop_service", delete: "/api/delete_project" };
    if (action === "delete" && !confirm("Delete this project record? Workspace files are retained.")) return;
    button.disabled = true;
    try {
      const result = await request(paths[action], { method: "POST", body: JSON.stringify({ uuid: projectId }) });
      notify(result.message || `Project ${action} request completed.`);
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-project-action]");
    if (button) act(button);
    if (event.target.closest("[data-refresh='projects']")) refresh();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const body = Object.fromEntries([...formData].filter(([, value]) => String(value).trim()));
    const submit = form.querySelector("button[type='submit']");
    submit.disabled = true;
    try {
      const result = await request("/api/projects", { method: "POST", body: JSON.stringify(body) });
      notify(result.message || "Project created.");
      form.reset();
      await refresh();
    } catch (error) {
      notify(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  return { refresh };
}
