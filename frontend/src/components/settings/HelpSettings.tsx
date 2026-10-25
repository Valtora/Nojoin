import { ButtonBase } from "@/components/ui/nativeBases";
import { PlayCircle, RefreshCw, Bug } from "lucide-react";
import { useNavigationStore } from "@/lib/store";
import { seedDemoData } from "@/lib/api";
import { useState } from "react";
import { useNotificationStore } from "@/lib/notificationStore";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { SETTINGS_BUTTON_SECONDARY } from "./settingsControls";

interface HelpSettingsProps {
  userId: number | null;
}

export default function HelpSettings({ userId }: HelpSettingsProps) {
  const {
    setHasSeenTour,
    setHasSeenRecordingsTour,
    setHasSeenTranscriptTour,
  } = useNavigationStore();
  const { addNotification } = useNotificationStore();
  const [isSeeding, setIsSeeding] = useState(false);

  const handleRestartTour = () => {
    if (userId) {
      setHasSeenTour(userId, false);
      setHasSeenRecordingsTour(userId, false);
      setHasSeenTranscriptTour(userId, false);
      addNotification({
        type: "success",
        message:
          "Tours reset. They start again the next time you open each page.",
      });
    }
  };

  const handleRecreateDemo = async () => {
    setIsSeeding(true);
    try {
      await seedDemoData();
      addNotification({
        type: "success",
        message:
          "Demo meeting creation started. It will appear in your recordings shortly.",
      });
    } catch {
      addNotification({
        type: "error",
        message: "Failed to create demo meeting.",
      });
    } finally {
      setIsSeeding(false);
    }
  };

  return (
    <>
      <SettingsCard
        id="help-tours"
        title="Tours and demo"
        description="Walk through Nojoin again, or bring back the sample meeting."
      >
        <SettingsRow
          label="Guided tours"
          description="Shows the dashboard, recordings and transcript tours again the next time you open each page."
        >
          <div className="flex @min-[26rem]:justify-end">
            <ButtonBase
              chrome="action"
              type="button"
              onClick={handleRestartTour}
              disabled={!userId}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              <PlayCircle className="h-4 w-4" aria-hidden="true" />
              Restart tours
            </ButtonBase>
          </div>
        </SettingsRow>

        <SettingsRow
          label="Demo meeting"
          description="Creates the Welcome to Nojoin meeting again, if you deleted it."
        >
          <div className="flex @min-[26rem]:justify-end">
            <ButtonBase
              chrome="action"
              type="button"
              onClick={handleRecreateDemo}
              disabled={isSeeding}
              className={SETTINGS_BUTTON_SECONDARY}
            >
              <RefreshCw
                className={isSeeding ? "h-4 w-4 animate-spin" : "h-4 w-4"}
                aria-hidden="true"
              />
              {isSeeding ? "Creating..." : "Create it again"}
            </ButtonBase>
          </div>
        </SettingsRow>
      </SettingsCard>

      <SettingsCard
        id="help-report-bug"
        title="Report a problem"
        description="Bugs and feature requests go to the project's issue tracker on GitHub."
      >
        <SettingsRow
          label="GitHub issues"
          description="Say what you did, what you expected and what happened."
        >
          <div className="flex @min-[26rem]:justify-end">
            <a
              href="https://github.com/Valtora/Nojoin/issues"
              target="_blank"
              rel="noopener noreferrer"
              className={SETTINGS_BUTTON_SECONDARY}
            >
              <Bug className="h-4 w-4" aria-hidden="true" />
              Open an issue
            </a>
          </div>
        </SettingsRow>
      </SettingsCard>
    </>
  );
}
