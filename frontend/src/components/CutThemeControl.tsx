import { useEffect, useState } from "react";
import { Moon, Sun } from "lucide-react";

type CutTheme = "dark" | "light";
const STORAGE_KEY = "cut-improvement-auto-theme";

function readSavedTheme(): CutTheme {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark";
  }
}

// Set the mode before the first React paint to avoid a wrong-color flash.
const initialTheme: CutTheme = readSavedTheme();
document.documentElement.dataset.cutTheme = initialTheme;

export function CutThemeControl() {
  const [theme, setTheme] = useState<CutTheme>(() => readSavedTheme());

  useEffect(() => {
    document.documentElement.dataset.cutTheme = theme;
    try {
      window.localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      // Theme still works in the current tab when storage is disabled.
    }
  }, [theme]);

  return (
    <div className="cut-theme-switcher" role="group" aria-label="Тема оформления ЦУТ Improvement Auto">
      <button
        type="button"
        aria-pressed={theme === "light"}
        title="Включить светлую тему"
        onClick={() => setTheme("light")}
      >
        <Sun size={16} aria-hidden="true" />
        <span>Светлая</span>
      </button>
      <button
        type="button"
        aria-pressed={theme === "dark"}
        title="Включить тёмную тему"
        onClick={() => setTheme("dark")}
      >
        <Moon size={16} aria-hidden="true" />
        <span>Тёмная</span>
      </button>
    </div>
  );
}
