import type { ChangeEvent } from "react";

import { useViewportDensity } from "@/components/ViewportDensityProvider";
import {
  CORNER_STYLES,
  CORNER_STYLE_LABELS,
  DENSITY_PREFERENCES,
  DENSITY_PREFERENCE_LABELS,
  PALETTES,
  PALETTE_LABELS,
  type CornerStyle,
  type DensityPreference,
  type Palette,
} from "@/lib/appearance";
import { useTheme, type Theme } from "@/lib/ThemeProvider";

import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_SELECT_CLASS } from "./settingsControls";

/**
 * The live palette, drawn from the tokens themselves rather than from a copy
 * of their values, so the preview cannot disagree with what the app renders.
 */
function PaletteSwatch() {
  return (
    <span className="inline-flex items-center gap-1" aria-hidden="true">
      <span className="h-3 w-3 rounded-full bg-action" />
      <span className="h-3 w-3 rounded-full border border-rail-border bg-rail" />
      <span className="h-3 w-3 rounded-full border border-action-border bg-action-tint" />
    </span>
  );
}

/**
 * How Nojoin looks in this browser. Every control here is a per-browser
 * preference held in local storage, not a server setting, so it applies the
 * moment it changes and never reaches the autosave.
 */
export default function AppearanceSettings() {
  const { theme, setTheme, palette, setPalette, cornerStyle, setCornerStyle } =
    useTheme();
  const { densityPreference, setDensityPreference } = useViewportDensity();

  return (
    <SettingsCard
      id="appearance-theme"
      title="Appearance"
      description="How Nojoin looks in this browser. Each choice is saved in this browser only, not with your account."
    >
      <SettingsRow label="Theme">
        <select
          aria-label="Theme"
          value={theme}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setTheme(event.target.value as Theme)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="system">System default</option>
          <option value="light">Light</option>
          <option value="dark">Dark</option>
        </select>
      </SettingsRow>

      <SettingsRow
        label="Colour palette"
        description="The accent colour and the tint of the page, cards and navigation. Status colours keep their meaning in every palette."
        badge={<PaletteSwatch />}
      >
        <select
          aria-label="Colour palette"
          value={palette}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setPalette(event.target.value as Palette)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          {PALETTES.map((option) => (
            <option key={option} value={option}>
              {PALETTE_LABELS[option]}
            </option>
          ))}
        </select>
      </SettingsRow>

      <SettingsRow
        label="Corner style"
        description="How rounded cards, buttons and fields are. Avatars and status dots stay round."
      >
        <select
          aria-label="Corner style"
          value={cornerStyle}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setCornerStyle(event.target.value as CornerStyle)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          {CORNER_STYLES.map((option) => (
            <option key={option} value={option}>
              {CORNER_STYLE_LABELS[option]}
            </option>
          ))}
        </select>
      </SettingsRow>

      <SettingsRow
        label="Density"
        description="Automatic uses compact spacing on desktop windows up to 1920 by 1080 and comfortable spacing everywhere else. Dense is tighter still for mouse and keyboard; on a touch screen it keeps compact-sized controls."
      >
        <select
          aria-label="Density"
          value={densityPreference}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setDensityPreference(event.target.value as DensityPreference)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          {DENSITY_PREFERENCES.map((option) => (
            <option key={option} value={option}>
              {DENSITY_PREFERENCE_LABELS[option]}
            </option>
          ))}
        </select>
      </SettingsRow>
    </SettingsCard>
  );
}
