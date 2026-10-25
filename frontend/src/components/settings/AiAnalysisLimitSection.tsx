"use client";

import { InputBase } from "@/components/ui/nativeBases";
import SettingsCard from "./SettingsCard";
import { useSettingsContext } from "./SettingsProvider";
import SettingsRow from "./SettingsRow";
import { SETTINGS_INPUT_CLASS } from "./settingsControls";

/**
 * The installation's daily cap on AI analyses per person. Lives beside the
 * users table because an individual's own cap is set by editing them there.
 */
export default function AiAnalysisLimitSection() {
  const { settings, updateSetting } = useSettingsContext();

  return (
    <SettingsCard
      id="users-ai-analysis-limit"
      title="AI analysis"
      description="How many meeting analyses each person can start in a day."
    >
      <SettingsRow
        label="Analyses per person per day"
        controlId="ai-analysis-daily-limit"
        description="Each analysis is one long call against that person's AI provider quota, counted from midnight UTC. Leave it blank or at 0 for no limit. To change one person's limit, edit them in the table above."
      >
        <InputBase
          id="ai-analysis-daily-limit"
          aria-describedby="ai-analysis-daily-limit-help"
          type="number"
          inputMode="numeric"
          min={0}
          step={1}
          value={settings.ai_analysis_daily_limit ?? ""}
          placeholder="No limit"
          onChange={(event) =>
            updateSetting({
              ...settings,
              ai_analysis_daily_limit:
                event.target.value === ""
                  ? null
                  : Math.max(0, Math.floor(Number(event.target.value))),
            })
          }
          className={SETTINGS_INPUT_CLASS}
        />
      </SettingsRow>
    </SettingsCard>
  );
}
