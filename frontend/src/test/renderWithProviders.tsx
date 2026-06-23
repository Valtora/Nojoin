import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import { type ReactElement, type ReactNode } from "react";

import { ThemeProvider } from "@/lib/ThemeProvider";
import { ViewportDensityProvider } from "@/components/ViewportDensityProvider";

/**
 * Options controlling which application contexts wrap the component under test.
 *
 * Navigation and notification state live in global Zustand stores
 * (`@/lib/store`, `@/lib/notificationStore`) rather than React context
 * providers, so tests interact with them by mocking those modules. The
 * genuine React contexts the app mounts at the root are the theme and
 * viewport-density providers, which are wrapped here by default.
 */
export interface RenderWithProvidersOptions extends Omit<RenderOptions, "wrapper"> {
  /** Wrap with {@link ViewportDensityProvider}. Defaults to `true`. */
  withViewportDensity?: boolean;
  /**
   * Wrap with {@link ThemeProvider}. Defaults to `false` because the provider
   * reads `window.matchMedia`, which jsdom does not implement; opt in only when
   * the component under test consumes the theme context and the test stubs
   * `matchMedia`.
   */
  withTheme?: boolean;
}

/**
 * Render a component wrapped with the application contexts that tests commonly
 * require. Shared across the frontend test suite so individual tests do not
 * duplicate provider setup.
 */
export function renderWithProviders(
  ui: ReactElement,
  {
    withViewportDensity = true,
    withTheme = false,
    ...renderOptions
  }: RenderWithProvidersOptions = {},
): RenderResult {
  function Wrapper({ children }: { children: ReactNode }) {
    let tree = <>{children}</>;

    if (withViewportDensity) {
      tree = <ViewportDensityProvider>{tree}</ViewportDensityProvider>;
    }

    if (withTheme) {
      tree = <ThemeProvider>{tree}</ThemeProvider>;
    }

    return tree;
  }

  return render(ui, { wrapper: Wrapper, ...renderOptions });
}

export * from "@testing-library/react";
