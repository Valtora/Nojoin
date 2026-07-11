"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  DESKTOP_BREAKPOINT,
  resolveViewportDensity,
  type ViewportDensity,
} from "@/lib/viewportDensity";

interface ViewportDensityContextValue {
  density: ViewportDensity;
  isCompact: boolean;
  /** True below the desktop breakpoint (1024px) — the app-wide mobile boundary. */
  isMobile: boolean;
  /** True at or above the desktop breakpoint (1024px). */
  isDesktop: boolean;
  viewportHeight: number;
  viewportWidth: number;
}

const ViewportDensityContext = createContext<
  ViewportDensityContextValue | undefined
>(undefined);

function readViewport() {
  if (typeof window === "undefined") {
    return { width: 0, height: 0 };
  }

  return {
    width: window.innerWidth,
    height: window.innerHeight,
  };
}

function applyDensityAttribute(density: ViewportDensity) {
  document.documentElement.dataset.uiDensity = density;
}

export function ViewportDensityProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [{ width, height }, setViewport] = useState(readViewport);

  useEffect(() => {
    const syncViewport = () => {
      setViewport(readViewport());
    };

    syncViewport();
    window.addEventListener("resize", syncViewport);

    return () => window.removeEventListener("resize", syncViewport);
  }, []);

  const density = useMemo(
    () => resolveViewportDensity(width, height),
    [height, width],
  );

  useEffect(() => {
    applyDensityAttribute(density);

    return () => {
      delete document.documentElement.dataset.uiDensity;
    };
  }, [density]);

  const value = useMemo(() => {
    const isDesktop = width >= DESKTOP_BREAKPOINT;

    return {
      density,
      isCompact: density === "compact",
      isMobile: !isDesktop,
      isDesktop,
      viewportHeight: height,
      viewportWidth: width,
    };
  }, [density, height, width]);

  return (
    <ViewportDensityContext.Provider value={value}>
      {children}
    </ViewportDensityContext.Provider>
  );
}

export function useViewportDensity() {
  const context = useContext(ViewportDensityContext);

  if (!context) {
    throw new Error(
      "useViewportDensity must be used within a ViewportDensityProvider",
    );
  }

  return context;
}
