import { Cpu } from "lucide-react";

import { Settings } from "@/types";
import { Switch } from "@/components/ui/Switch";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";

interface AiAutomaticEnhancementSectionProps {
  settings: Settings;
  /** Apply and save immediately (the switch is a discrete control). */
  onPersist: (newSettings: Settings) => void;
}

/** "Automatic enhancement" AI section (per-user). */
export default function AiAutomaticEnhancementSection({
  settings,
  onPersist,
}: AiAutomaticEnhancementSectionProps) {
  return (
    <SettingsCard
      title="Automatic Enhancement"
      description="Control how AI-generated titles are written for your meetings and summaries."
    >
      <SettingsBlock
        className="flex items-start gap-3"
      >
        <div className="flex-1">
          <div className="flex items-center gap-2 text-sm font-medium text-foreground">
            <Cpu className="h-4 w-4 text-action-text" />
            Prefer short titles
          </div>
          <p className="mt-2 text-xs contrast-helper">
            Use concise 3-5 word AI-generated meeting titles instead of longer
            descriptive ones.
          </p>
        </div>
        <Switch
          checked={settings.prefer_short_titles !== false}
          onCheckedChange={(checked) =>
            onPersist({ ...settings, prefer_short_titles: checked })
          }
        />
      </SettingsBlock>
    </SettingsCard>
  );
}
