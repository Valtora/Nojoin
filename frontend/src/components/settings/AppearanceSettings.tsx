import { SelectBase } from "@/components/ui/nativeBases";
import type { ChangeEvent } from "react";

import { useViewportDensity } from "@/components/ViewportDensityProvider";
import {
  CORNER_PREFERENCES,
  CORNER_PREFERENCE_LABELS,
  DENSITY_PREFERENCES,
  DENSITY_PREFERENCE_LABELS,
  PALETTES,
  PALETTE_LABELS,
  UI_STYLES,
  UI_STYLE_LABELS,
  type CornerPreference,
  type DensityPreference,
  type Palette,
  type UiStyle,
} from "@/lib/appearance";
import { useTheme, type Theme } from "@/lib/ThemeProvider";

import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_COLUMN_WIDE, SETTINGS_SELECT_CLASS } from "./settingsControls";

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
  const {
    theme, setTheme, palette, setPalette, cornerPreference, setCornerPreference, uiStyle, setUiStyle,
  } = useTheme();
  const { densityPreference, setDensityPreference } = useViewportDensity();

  return (
    <SettingsCard
      id="appearance-theme"
      title="Look and feel"
      description="How Nojoin looks in this browser. Each choice is saved in this browser only, not with your account."
    >
      <SettingsRow label="Theme" controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}>
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
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
        </SelectBase>
      </SettingsRow>

      <SettingsRow
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        label="Colour palette"
        description="The accent colour and the tint of the page, cards and navigation. Status colours keep their meaning in every palette."
        badge={<PaletteSwatch />}
      >
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
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
        </SelectBase>
      </SettingsRow>

      <SettingsRow
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        id="appearance-style"
        label="Style"
        controlId="appearance-style-select"
        description="The shape and depth of controls and panels, with corners to match."
      >
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          id="appearance-style-select"
          aria-describedby="appearance-style-select-help"
          value={uiStyle}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setUiStyle(event.target.value as UiStyle)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          {UI_STYLES.map((option) => (
            <option key={option} value={option}>
              {UI_STYLE_LABELS[option]}
            </option>
          ))}
        </SelectBase>
      </SettingsRow>

      <SettingsRow
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        label="Corner style"
        description="How rounded cards, buttons and fields are. Automatic takes them from Style. Avatars and status dots stay round."
      >
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          aria-label="Corner style"
          value={cornerPreference}
          onChange={(event: ChangeEvent<HTMLSelectElement>) =>
            setCornerPreference(event.target.value as CornerPreference)
          }
          className={SETTINGS_SELECT_CLASS}
        >
          {CORNER_PREFERENCES.map((option) => (
            <option key={option} value={option}>
              {CORNER_PREFERENCE_LABELS[option]}
            </option>
          ))}
        </SelectBase>
      </SettingsRow>

      <SettingsRow
        controlClassName={SETTINGS_CONTROL_COLUMN_WIDE}
        label="Density"
        description="How closely lists, panels and controls are spaced. Automatic fits the spacing to your window."
      >
        <SelectBase
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
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
        </SelectBase>
      </SettingsRow>
    </SettingsCard>
  );
}
