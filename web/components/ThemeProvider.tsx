"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { ReactNode } from "react";

type Theme = "dark" | "light" | "system";

const ThemeContext = createContext<{ theme: Theme; setTheme: (t: Theme) => void }>({
  theme: "system",
  setTheme: () => {},
});

export const useTheme = () => useContext(ThemeContext);

const KEY = "signal-theme";

/**
 * One provider for the whole site.
 *
 * Three states, not two. "System" is the default and is a real state: a visitor who
 * has never touched the toggle should follow their OS, and an explicit choice must
 * beat the OS in BOTH directions - a light OS with dark chosen stays dark. That is
 * why the attribute is removed rather than set for "system": the CSS is written so
 * the absence of `data-theme` means "ask the media query".
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("system");

  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY) as Theme | null;
      if (saved === "dark" || saved === "light") setThemeState(saved);
    } catch {
      // Private browsing, or site data blocked. The default is correct anyway.
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // Not being able to remember the choice is not a reason to refuse it.
    }
  }, []);

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
  );
}
