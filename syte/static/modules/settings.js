import { request } from "./api.js";

const fields = ["public_ip", "admin_email", "gui_domain", "preview_base_domain", "preview_wildcard_tls", "custom_tls_host", "custom_tls_port"];

export function setupSettings({ notify }) {
  const form = document.querySelector("#settings-form");

  async function load() {
    try {
      const settings = await request("/api/settings");
      fields.forEach((field) => {
        if (field in settings && form.elements[field]) form.elements[field].value = settings[field] ?? "";
      });
    } catch (error) {
      notify(error.message, true);
    }
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const formData = new FormData(form);
    const body = Object.fromEntries(formData);
    if (!body.cloudflare_api_token) delete body.cloudflare_api_token;
    const submit = form.querySelector("button[type='submit']");
    submit.disabled = true;
    try {
      const result = await request("/api/settings", { method: "PUT", body: JSON.stringify(body) });
      notify((result.messages || ["Settings saved."]).join(" "));
      form.elements.cloudflare_api_token.value = "";
      await load();
    } catch (error) {
      notify(error.message, true);
    } finally {
      submit.disabled = false;
    }
  });

  return { load };
}
