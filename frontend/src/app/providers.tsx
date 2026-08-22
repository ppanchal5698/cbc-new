"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useMemo, useState } from "react";
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
}

const ChromeContext = createContext<Chrome | null>(null);

export function useChrome(): Chrome {
  const ctx = useContext(ChromeContext);
  if (!ctx) throw new Error("useChrome outside Providers");
  return ctx;
}

const STORE = "opshub.chrome";

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

  const [theme, setTheme] = useState<Theme>("dark");
  const [density, setDensity] = useState<Density>("Comfortable");
  const [focus, setFocus] = useState(true);

  // Read the stored preference after mount: reading it during render would make
  // the server and client markup disagree and blow up hydration.
  useEffect(() => {
    void primeCsrf();
    try {
      const saved = JSON.parse(localStorage.getItem(STORE) ?? "{}");
      if (saved.theme === "light" || saved.theme === "dark") setTheme(saved.theme);
      if (saved.density === "Compact" || saved.density === "Comfortable") setDensity(saved.density);
      if (typeof saved.focus === "boolean") setFocus(saved.focus);
    } catch {
      /* first visit, or a human edited localStorage */
    }
  }, []);

  useEffect(() => {
    localStorage.setItem(STORE, JSON.stringify({ theme, density, focus }));
  }, [theme, density, focus]);

  const chrome = useMemo<Chrome>(
    () => ({
      theme,
      setTheme,
      toggleTheme: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
      density,
      setDensity,
      dense: density === "Compact",
      focus,
      setFocus,
    }),
    [theme, density, focus],
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
        </div>
      </ChromeContext.Provider>
    </QueryClientProvider>
  );
}
