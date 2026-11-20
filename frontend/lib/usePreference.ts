"use client";

// A small preference saved in localStorage (theme, expert details), shared by every component
// that reads it. Server render and first paint use the fallback, so hydration always matches.
import { useCallback, useSyncExternalStore } from "react";

const EVENT = "agk-preference";

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(EVENT, onChange);
  };
}

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null; // storage blocked (private mode, policy)
  }
}

export function usePreference<T extends string>(
  key: string,
  fallback: T,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const stored = useSyncExternalStore(
    subscribe,
    () => read(key),
    () => null,
  );
  const value = stored !== null && (allowed as readonly string[]).includes(stored) ? (stored as T) : fallback;

  const set = useCallback(
    (next: T) => {
      try {
        window.localStorage.setItem(key, next);
      } catch {
        // storage blocked: the choice simply isn't remembered
      }
      window.dispatchEvent(new Event(EVENT));
    },
    [key],
  );
  return [value, set];
}
