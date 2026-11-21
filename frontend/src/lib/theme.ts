/**
 * Light / dark theme: one class on <html>, remembered per device (UX_DESIGN: a real light theme,
 * dark-first). Storage can be unavailable (private mode): the theme then follows the system.
 */

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "fintrack-theme";
export const THEME_EVENT = "fintrack-theme-change";

export function isTheme(v: unknown): v is Theme {
  return v === "light" || v === "dark";
}

export function storedTheme(storage: Pick<Storage, "getItem"> | null): Theme | null {
  try {
    const v = storage?.getItem(THEME_STORAGE_KEY);
    return isTheme(v) ? v : null;
  } catch {
    return null;
  }
}

/** The saved choice, else the system preference, else dark. */
export function initialTheme(
  storage: Pick<Storage, "getItem"> | null,
  prefersLight: boolean,
): Theme {
  return storedTheme(storage) ?? (prefersLight ? "light" : "dark");
}

export function currentTheme(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.style.colorScheme = theme;
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    /* storage unavailable: the choice lasts for this page only */
  }
  window.dispatchEvent(new CustomEvent<Theme>(THEME_EVENT, { detail: theme }));
}

/** Runs in <head> before first paint, so the page never flashes the wrong theme. */
export const THEME_BOOT_SCRIPT = `(function(){try{var t=localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)});if(t!=="light"&&t!=="dark"){t=window.matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";}var r=document.documentElement;r.classList.toggle("dark",t==="dark");r.style.colorScheme=t;}catch(e){document.documentElement.classList.add("dark");}})();`;
