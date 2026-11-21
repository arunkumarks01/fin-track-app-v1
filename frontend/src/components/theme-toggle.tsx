"use client";

import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { applyTheme, currentTheme, type Theme } from "@/lib/theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme | null>(null);
  useEffect(() => setTheme(currentTheme()), []);
  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <Button
      variant="outline"
      size="icon"
      aria-label={`Switch to the ${next} theme`}
      title={`Switch to the ${next} theme`}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
    >
      {theme === "dark" ? <Sun aria-hidden /> : <Moon aria-hidden />}
    </Button>
  );
}
