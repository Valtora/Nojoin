import { SelectBase } from "@/components/ui/nativeBases";
import type { LiveTextSettlingMode, Settings } from "@/types";
import SettingsRow from "./SettingsRow";
import { SETTINGS_SELECT_CLASS } from "./settingsControls";

interface Props {
  settings: Settings;
  isAdmin: boolean;
  onPersist: (settings: Settings) => void | Promise<void>;
}

/** What the selected count of unchanged comparisons asks of a word. */
const comparisonsHelp = (k: number) =>
  `With ${k}, a word locks once it comes out the same in ${k + 1} transcriptions in a row. ` +
  "Lower values lock words sooner; higher values wait for steadier wording.";

export default function LiveTextSettlingSettings({ settings, isAdmin, onPersist }: Props) {
  const k = settings.live_text_settling_k ?? 3;
  return (
    <>
      <SettingsRow
        id="transcription-live-settling"
        label="Live word settling"
        controlId="live-text-settling"
        description="Revises the newest live words as more speech arrives, then locks them. Auto settles live text on a GPU server and shows ordinary whole lines on a CPU-only one. GPU and CPU settles on a CPU-only server too, transcribing the recent audio again every couple of seconds, which needs a lot of CPU and RAM. A change takes effect when a recording starts or resumes."
      >
        <SelectBase
          id="live-text-settling"
          aria-describedby="live-text-settling-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={settings.live_text_settling ?? "auto"}
          onChange={(event) => void onPersist({ ...settings, live_text_settling: event.target.value as LiveTextSettlingMode })}
          disabled={!isAdmin}
          className={SETTINGS_SELECT_CLASS}
        >
          <option value="off">Off</option>
          <option value="auto">Auto (GPU)</option>
          <option value="always">GPU and CPU</option>
        </SelectBase>
      </SettingsRow>
      <SettingsRow
        id="transcription-live-settling-k"
        label="Unchanged comparisons"
        controlId="live-text-settling-k"
        description={comparisonsHelp(k)}
      >
        <SelectBase
          id="live-text-settling-k"
          aria-describedby="live-text-settling-k-help"
          data-ui-fill="control"
          data-ui-disabled-fill="control-disabled"
          data-ui-border="full"
          value={k}
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
        description="How much of the most recent audio is transcribed again each time, counting the earlier audio kept for context. A word that falls outside it locks as it is."
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
