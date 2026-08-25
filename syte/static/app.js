import { request } from "./modules/api.js";
import { setupNavigation } from "./modules/navigation.js";
import { setupProjects } from "./modules/projects.js";
import { setupRemoteServers } from "./modules/remote-servers.js";
import { setupSettings } from "./modules/settings.js";

const flashRegion = document.querySelector("#flash-region");
const connectionState = document.querySelector("#connection-state");

function notify(message, isError = false) {
  flashRegion.innerHTML = `<div class="flash${isError ? " is-error" : ""}">${String(message)}</div>`;
  window.setTimeout(() => { flashRegion.innerHTML = ""; }, 5500);
}

async function verifyConnection() {
  try {
    await request("/api/health");
    connectionState.textContent = "Connected";
    connectionState.classList.add("is-ready");
  } catch {
    connectionState.textContent = "Unavailable";
  }
}

const projects = setupProjects({ notify });
const remoteServers = setupRemoteServers({ notify });
const settings = setupSettings({ notify });

setupNavigation((view) => {
  if (view === "home" || view === "projects") projects.refresh();
  if (view === "remote-servers") remoteServers.load();
  if (view === "settings") settings.load();
});

verifyConnection();
