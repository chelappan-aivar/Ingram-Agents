// Light, dark or follow the system. The choice is remembered in this browser only.

const KEY = "live-assist-theme";
const CHOICES = ["light", "dark", "system"];
const query = window.matchMedia("(prefers-color-scheme: dark)");

function apply(choice) {
  const resolved = choice === "system" ? (query.matches ? "dark" : "light") : choice;
  const root = document.documentElement;
  root.dataset.theme = choice;
  root.dataset.resolved = resolved;
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.setAttribute("aria-pressed", String(button.dataset.themeChoice === choice));
  }
}

export function initTheme() {
  let saved = "system";
  try {
    const stored = localStorage.getItem(KEY);
    if (CHOICES.includes(stored)) saved = stored;
  } catch { /* storage may be blocked; the default is fine */ }
  apply(saved);
  query.addEventListener("change", () => {
    if (document.documentElement.dataset.theme === "system") apply("system");
  });
  for (const button of document.querySelectorAll("[data-theme-choice]")) {
    button.addEventListener("click", () => {
      const choice = button.dataset.themeChoice;
      apply(choice);
      try { localStorage.setItem(KEY, choice); } catch { /* ignore */ }
    });
  }
}
