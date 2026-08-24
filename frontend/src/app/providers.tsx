"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { Toast, type ToastState } from "@/components/shell/Toast";
import { primeCsrf } from "@/lib/api";

export type Theme = "dark" | "light";
export type Density = "Comfortable" | "Compact";

interface Chrome {
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggleTheme: () => void;
  density: Density;
  setDensity: (d: Density) => void;
  dense: boolean;
  focus: boolean;
  setFocus: (f: boolean) => void;
  /**
   * The prototype's `flash` — a small confirmation, bottom-left, gone in 2.6s.
   *
   * On the chrome rather than on each screen because the actions that warrant one
   * are spread across the app, and a toast that only some views could raise would
   * be a toast estimators stop trusting.
   */
  flash: (title: string, sub?: string, warm?: boolean) => void;
}

const ChromeContext = createContext<Chrome | null>(null);

export function useChrome(): Chrome {
  const ctx = useContext(ChromeContext);
  if (!ctx) throw new Error("useChrome outside Providers");
  return ctx;
}

const STORE = "opshub.chrome";

interface Prefs {
  theme: Theme;
  density: Density;
  focus: boolean;
}

/**
 * The stored chrome preferences, as an external store.
 *
 * `localStorage` genuinely *is* an external system, and reading it after mount
 * with an effect is the pattern React now warns about: it renders the default
 * theme, then corrects itself, which is a visible flash on every load. Reading
 * it *during* render is worse — the server has no localStorage, so the markup
 * would not match and hydration would blow up.
 *
 * `useSyncExternalStore` is the API for exactly this shape, with a server
 * snapshot for the render that has no storage. Subscribing to `storage` events
 * also means changing the theme in one tab now changes it in the others, which
 * the effect version never did.
 */

const DEFAULT_PREFS: Prefs = { theme: "dark", density: "Comfortable", focus: true };

/** The parsed snapshot, cached so `getSnapshot` returns a stable reference. */
let snapshot: Prefs = DEFAULT_PREFS;
let snapshotRaw: string | null = null;
const listeners = new Set<() => void>();

function parsePrefs(raw: string | null): Prefs {
  try {
    const saved = JSON.parse(raw ?? "{}");
    return {
      theme: saved.theme === "light" || saved.theme === "dark" ? saved.theme : DEFAULT_PREFS.theme,
      density:
        saved.density === "Compact" || saved.density === "Comfortable"
          ? saved.density
          : DEFAULT_PREFS.density,
      focus: typeof saved.focus === "boolean" ? saved.focus : DEFAULT_PREFS.focus,
    };
  } catch {
    /* first visit, or a human edited localStorage by hand */
    return DEFAULT_PREFS;
  }
}

function getSnapshot(): Prefs {
  const raw = localStorage.getItem(STORE);
  // Re-parsing on every call would hand back a new object each time and spin
  // useSyncExternalStore into an infinite loop.
  if (raw !== snapshotRaw) {
    snapshotRaw = raw;
    snapshot = parsePrefs(raw);
  }
  return snapshot;
}

/** No storage on the server, so the defaults are what the first render draws. */
const getServerSnapshot = (): Prefs => DEFAULT_PREFS;

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Fires for writes from *other* tabs; writePrefs notifies this one directly.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

function writePrefs(next: Prefs): void {
  localStorage.setItem(STORE, JSON.stringify(next));
  for (const listener of listeners) listener();
}

export function Providers({ children }: { children: React.ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            // Bid data changes when a human changes it, not on a timer. Refetching
            // on every window focus would re-poll a 200-page manifest each time an
            // estimator alt-tabs back from the PDF.
            refetchOnWindowFocus: false,
            staleTime: 30_000,
            retry: 1,
          },
        },
      }),
  );

  const prefs = useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);

  // Puts the CSRF cookie in place before anything unsafe is attempted. A genuine
  // "tell an external system about us" effect, which is what effects are for.
  useEffect(() => {
    void primeCsrf();
  }, []);

  const [toast, setToast] = useState<ToastState | null>(null);
  const dismiss = useRef<ReturnType<typeof setTimeout> | null>(null);

  const flash = useCallback((title: string, sub?: string, warm?: boolean) => {
    setToast({ title, sub, warm });
    if (dismiss.current) clearTimeout(dismiss.current);
    dismiss.current = setTimeout(() => setToast(null), 2600);
  }, []);

  // A pending timer outliving the tree would setState on an unmounted provider.
  useEffect(() => () => {
    if (dismiss.current) clearTimeout(dismiss.current);
  }, []);

  const { theme } = prefs;

  // The setters are built in here rather than outside, so the dependency list is
  // the whole truth: every one of them closes over `prefs`, and defining them in
  // the component body made them new functions on each render that the memo then
  // claimed not to depend on.
  const chrome = useMemo<Chrome>(
    () => ({
      theme: prefs.theme,
      setTheme: (t: Theme) => writePrefs({ ...prefs, theme: t }),
      toggleTheme: () => writePrefs({ ...prefs, theme: prefs.theme === "dark" ? "light" : "dark" }),
      density: prefs.density,
      setDensity: (d: Density) => writePrefs({ ...prefs, density: d }),
      dense: prefs.density === "Compact",
      focus: prefs.focus,
      setFocus: (f: boolean) => writePrefs({ ...prefs, focus: f }),
      flash,
    }),
    [prefs, flash],
  );

  return (
    <QueryClientProvider client={client}>
      <ChromeContext.Provider value={chrome}>
        {/* The prototype's root element, verbatim. min-width is deliberate: this
            is a fixed-height desk tool for estimators on desktops, not a
            responsive site. */}
        <div
          data-theme={theme}
          style={{
            height: "100vh",
            minWidth: "1620px",
            background: "var(--app-bg)",
            color: "var(--app-tx)",
            fontFamily: "var(--app-font)",
            fontSize: "13.5px",
            lineHeight: 1.5,
            fontFeatureSettings: "'tnum' 1",
          }}
        >
          {children}
          <Toast toast={toast} />
        </div>
      </ChromeContext.Provider>
    </QueryClientProvider>
  );
}
