import { createContext, useContext, useEffect, useState } from "react";
import { api } from "@/lib/api";

type Theme = "light" | "dark" | "system";

interface ThemeContextValue {
  theme: Theme;
  resolved: "light" | "dark";
  setTheme: (t: Theme) => void;
}

const ThemeContext = createContext<ThemeContextValue>({
  theme: "light",
  resolved: "light",
  setTheme: () => {},
});

export function useTheme() {
  return useContext(ThemeContext);
}

function getSystemTheme(): "light" | "dark" {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function resolve(theme: Theme): "light" | "dark" {
  return theme === "system" ? getSystemTheme() : theme;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    return (localStorage.getItem("receiptory-theme") as Theme) || "light";
  });
  const [resolved, setResolved] = useState<"light" | "dark">(resolve(theme));

  // Apply to <html>
  useEffect(() => {
    const r = resolve(theme);
    setResolved(r);
    document.documentElement.classList.toggle("dark", r === "dark");
  }, [theme]);

  // Listen for system theme changes when in "system" mode
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const handler = () => {
      const r = getSystemTheme();
      setResolved(r);
      document.documentElement.classList.toggle("dark", r === "dark");
    };
    mq.addEventListener("change", handler);
    return () => mq.removeEventListener("change", handler);
  }, [theme]);

  // Load saved theme from server settings on mount
  useEffect(() => {
    api.get<any>("/settings").then((s) => {
      if (s.theme && s.theme !== theme) {
        setThemeState(s.theme as Theme);
        localStorage.setItem("receiptory-theme", s.theme);
      }
    }).catch(() => {});
  }, []);

  const setTheme = (t: Theme) => {
    setThemeState(t);
    localStorage.setItem("receiptory-theme", t);
    api.patch("/settings", { settings: { theme: t } }).catch(() => {});
  };

  return (
    <ThemeContext.Provider value={{ theme, resolved, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}
