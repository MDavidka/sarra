const labels = {
  home: ["Workspace", "Home"],
  projects: ["Workspace", "Projects"],
  "remote-servers": ["Administration", "Remote Servers"],
  settings: ["Administration", "Settings"],
};

export function setupNavigation(onViewChange) {
  const buttons = [...document.querySelectorAll("[data-view]")];
  const panels = [...document.querySelectorAll("[data-view-panel]")];
  const title = document.querySelector("#page-title");
  const kicker = document.querySelector("#view-kicker");

  function select(view) {
    const [nextKicker, nextTitle] = labels[view] || labels.home;
    buttons.forEach((button) => button.classList.toggle("is-active", button.dataset.view === view));
    panels.forEach((panel) => {
      const active = panel.dataset.viewPanel === view;
      panel.hidden = !active;
      panel.classList.toggle("is-active", active);
    });
    title.textContent = nextTitle;
    kicker.textContent = nextKicker;
    history.replaceState(null, "", `#${view}`);
    onViewChange?.(view);
  }

  buttons.forEach((button) => button.addEventListener("click", () => select(button.dataset.view)));
  document.querySelectorAll("[data-go]").forEach((button) => button.addEventListener("click", () => select(button.dataset.go)));
  select(location.hash.slice(1) in labels ? location.hash.slice(1) : "home");
  return { select };
}
