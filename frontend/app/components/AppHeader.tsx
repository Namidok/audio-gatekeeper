"use client";

// Product name, live system status, expert-details switch and theme choice.
import { useEffect, useState } from "react";

import { checkHealth } from "@/lib/api";
import { usePreference } from "@/lib/usePreference";

import styles from "./AppHeader.module.css";

type Theme = "system" | "dark" | "light";
const THEMES = ["system", "dark", "light"] as const;
const THEME_LABEL: Record<Theme, string> = { system: "Theme: system", dark: "Theme: dark", light: "Theme: light" };

type Status = { tone: "ok" | "warn" | "crit" | "wait"; text: string };

export default function AppHeader({ expert, onExpertChange }: { expert: boolean; onExpertChange: (on: boolean) => void }) {
  const [theme, setTheme] = usePreference<Theme>("agk-theme", "system", THEMES);
  const [status, setStatus] = useState<Status>({ tone: "wait", text: "Checking the service" });

  // Apply the theme to <html>; "system" leaves it to prefers-color-scheme
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    let alive = true;
    checkHealth(true)
      .then((h) => {
        if (!alive) return;
        if (h.status === "ok") setStatus({ tone: "ok", text: "System ready" });
        else if (!h.redis) setStatus({ tone: "crit", text: "Job database unavailable" });
        else if (!h.queue) setStatus({ tone: "crit", text: "Processing queue unavailable" });
        else setStatus({ tone: "warn", text: "File storage unavailable" });
      })
      .catch(() => {
        if (alive) setStatus({ tone: "crit", text: "Service unreachable" });
      });
    return () => {
      alive = false;
    };
  }, []);

  const nextTheme = THEMES[(THEMES.indexOf(theme) + 1) % THEMES.length];

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <span className={styles.mark} aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
        <span className={styles.name}>Audio Gatekeeper</span>
      </div>
      <div className={styles.tools}>
        <span className={`${styles.status} ${styles[status.tone]}`} role="status">
          <span className={styles.dot} aria-hidden="true" />
          {status.text}
        </span>
        <button type="button" className={styles.toggle} aria-pressed={expert} onClick={() => onExpertChange(!expert)}>
          <span className={styles.switch} aria-hidden="true" />
          Expert details
        </button>
        <button
          type="button"
          className={styles.toggle}
          onClick={() => setTheme(nextTheme)}
          aria-label={`${THEME_LABEL[theme]}. Switch to ${nextTheme}.`}
          title={`Switch to ${nextTheme} theme`}
        >
          <span aria-hidden="true">{theme === "dark" ? "☾" : theme === "light" ? "☀" : "◐"}</span>
          <span className={styles.themeText}>{THEME_LABEL[theme]}</span>
        </button>
      </div>
    </header>
  );
}
