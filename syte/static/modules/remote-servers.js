import { escapeHtml, request } from "./api.js";

export function setupRemoteServers({ notify }) {
  const container = document.querySelector("#server-card");

  async function load() {
    try {
      const system = await request("/api/system");
      const rows = [
        ["Server", system.gui_domain || system.public_ip || "Not configured"],
        ["Direct address", system.direct_url || "Not available"],
        ["Workspace directory", system.workspaces_dir || "Not available"],
        ["Syte version", system.version || "Unknown"],
      ];
      container.innerHTML = `<dl>${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join("")}</dl>`;
    } catch (error) {
      container.innerHTML = '<p class="empty-state">Server information is not available.</p>';
      notify(error.message, true);
    }
  }

  return { load };
}
