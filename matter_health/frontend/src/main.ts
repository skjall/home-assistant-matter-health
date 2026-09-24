// Entry point: pick language and theme, then show the page.
//
// Behind ingress the page runs in an iframe of the Home Assistant frontend on
// the same origin, so the user's language and dark-mode choice can be read
// from there. Opened on its own, the browser's preferences apply instead.

import { loadLanguage } from "./i18n";
import "./components/mh-app";

interface HassLike {
  language?: string;
  locale?: { language?: string };
  themes?: { darkMode?: boolean };
}

function homeAssistant(): HassLike | undefined {
  try {
    const root = window.parent?.document?.querySelector("home-assistant") as
      | (Element & { hass?: HassLike })
      | null;
    return root?.hass;
  } catch {
    // A parent on another origin refuses access; that is fine.
    return undefined;
  }
}

function applyTheme(app: HTMLElement): void {
  const hass = homeAssistant();
  const dark =
    hass?.themes?.darkMode ?? window.matchMedia("(prefers-color-scheme: dark)").matches;
  app.setAttribute("theme", dark ? "dark" : "light");
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

async function start(): Promise<void> {
  const hass = homeAssistant();
  const preferred = [
    hass?.locale?.language,
    hass?.language,
    ...navigator.languages,
  ].filter((code): code is string => Boolean(code));
  try {
    await loadLanguage(preferred);
  } catch {
    // The page still works with the keys shown; better than an empty page.
  }
  const app = document.createElement("mh-app");
  applyTheme(app);
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => applyTheme(app));
  // Home Assistant does not tell embedded pages about theme switches.
  window.setInterval(() => applyTheme(app), 5000);
  document.body.replaceChildren(app);
}

void start();
