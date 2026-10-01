import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "cvi-theme";

function apply(choice: ThemeChoice) {
  // "system" -> no attribute at all, so the prefers-color-scheme media query in index.css decides.
  if (choice === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", choice);
}

function readStored(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* localStorage unavailable (private browsing etc) -- fall back to system */
  }
  return "system";
}

/** Light/dark/system theme choice, persisted per-browser. Applied via a `data-theme` attribute on
 * <html> that index.css's `:root[data-theme="..."]` rules read -- "system" means no attribute, so
 * the OS-level `prefers-color-scheme` media query takes over instead. */
export function useTheme() {
  const [theme, setThemeState] = useState<ThemeChoice>(() => readStored());

  useEffect(() => {
    apply(theme);
  }, [theme]);

  const setTheme = useCallback((choice: ThemeChoice) => {
    setThemeState(choice);
    try {
      localStorage.setItem(KEY, choice);
    } catch {
      /* ignore -- just won't persist across reloads */
    }
  }, []);

  return { theme, setTheme };
}
