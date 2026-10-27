import { SelectBase } from "@/components/ui/nativeBases";
import type { Settings } from "@/types";
import SettingsRow from "./SettingsRow";
import { SETTINGS_SELECT_CLASS } from "./settingsControls";

interface Props {
  settings: Settings;
  isAdmin: boolean;
  onPersist: (settings: Settings) => void | Promise<void>;
}

export default function LiveTextSettlingSettings({ settings, isAdmin, onPersist }: Props) {
  return (
    <>
      <SettingsRow
        id="transcription-live-settling"
        label="Live word settling"
        controlId="live-text-settling"
        description="Revises recent words as more speech arrives, then locks them. Auto requires a GPU and applies to the next capture episode."
      >
        <SelectBase
          id="live-text-settling"
          aria-describedby="live-text-settling-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={settings.live_text_settling ?? "off"}
          onChange={(event) => void onPersist({ ...settings, live_text_settling: event.target.value as "auto" | "off" })}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="off">Off</option>
          <option value="auto">Auto (GPU)</option>
        </SelectBase>
      </SettingsRow>
      <SettingsRow
        id="transcription-live-settling-k"
        label="Unchanged comparisons"
        controlId="live-text-settling-k"
        description="Three comparisons require four successful observations of the same wording."
      >
        <SelectBase
          id="live-text-settling-k"
          aria-describedby="live-text-settling-k-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={settings.live_text_settling_k ?? 3}
          onChange={(event) => void onPersist({ ...settings, live_text_settling_k: Number(event.target.value) })}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          {Array.from({ length: 10 }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value}</option>)}
        </SelectBase>
      </SettingsRow>
      <SettingsRow
        id="transcription-live-settling-window"
        label="Settling window"
        controlId="live-text-settling-window"
        description="Total audio per pass, including the configured lead-up. Words leaving this window lock as they are."
      >
        <SelectBase
          id="live-text-settling-window"
          aria-describedby="live-text-settling-window-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={settings.live_text_settling_window_s ?? 10}
          onChange={(event) => void onPersist({ ...settings, live_text_settling_window_s: Number(event.target.value) })}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          {Array.from(new Set([2, 5, 10, 15, 20, 30, settings.live_text_settling_window_s ?? 10])).sort((a, b) => a - b).map((value) => <option key={value} value={value}>{value} seconds</option>)}
        </SelectBase>
      </SettingsRow>
    </>
  );
}
