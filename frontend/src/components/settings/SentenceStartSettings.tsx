import type { Settings } from "@/types";

import { Switch } from "../ui/Switch";
import SettingsRow from "./SettingsRow";
import { SETTINGS_CONTROL_END } from "./settingsControls";

interface Props {
  settings: Settings;
  isAdmin: boolean;
  onPersist: (settings: Settings) => void | Promise<void>;
}

/** The install-wide sentence-start correction (backend/processing/sentence_start.py). */
export default function SentenceStartSettings({ settings, isAdmin, onPersist }: Props) {
  return (
    <SettingsRow
      id="transcription-sentence-start"
      label="Reply starts"
      labelId="sentence-start-correction-label"
      description="The speaker model often hears a reply a moment late, so its first words show the previous speaker. On moves the words after the last full stop, question mark or exclamation mark to the person replying, in live labels and in processed transcripts; never a whole turn, and never words in a line someone corrected. Off leaves every word with the speaker the model gave it. A change applies from the next live step and the next processing run."
      descriptionId="sentence-start-correction-help"
      controlClassName={SETTINGS_CONTROL_END}
    >
      <Switch
        id="sentence-start-correction"
        aria-labelledby="sentence-start-correction-label"
        aria-describedby="sentence-start-correction-help"
        checked={settings.sentence_start_correction !== false}
        disabled={!isAdmin}
        onCheckedChange={(checked) => void onPersist({ ...settings, sentence_start_correction: checked })}
      />
    </SettingsRow>
  );
}
