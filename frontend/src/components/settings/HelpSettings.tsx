import { PlayCircle, RefreshCw, Bug } from "lucide-react";
import { useNavigationStore } from "@/lib/store";
import { seedDemoData } from "@/lib/api";
import { useState } from "react";
import { useNotificationStore } from "@/lib/notificationStore";
import { fuzzyMatch } from "@/lib/searchUtils";
import SettingsCallout from "./SettingsCallout";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";

const ACTION_BUTTON_STYLES =
  "inline-flex items-center gap-2 self-start rounded-xl bg-orange-100 px-3 py-2 text-sm font-medium text-orange-700 transition hover:bg-orange-200 disabled:opacity-50 dark:bg-orange-900/20 dark:text-orange-400 dark:hover:bg-orange-900/30 sm:self-auto";

interface HelpSettingsProps {
  userId: number | null;
  searchQuery?: string;
}

export default function HelpSettings({
  userId,
  searchQuery = "",
}: HelpSettingsProps) {
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
          "Tours reset. Go to the dashboard to start the Welcome Tour again.",
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

  const showTours = fuzzyMatch(searchQuery, [
    "tour",
    "demo",
    "welcome",
    "tutorial",
    "guide",
    "help",
  ]);

  if (!showTours && searchQuery)
    return (
      <SettingsCallout
        tone="neutral"
        title="No matching settings"
        message="Try a broader search term for tours, demos, or issue reporting."
      />
    );

  return (
    <div className="space-y-6">
      <SettingsSection
        eyebrow="Guidance"
        title="Tours and demos"
        description="Reset onboarding helpers or recreate the sample meeting used for first-run guidance."
        width="regular"
      >
        <div className="mx-auto max-w-2xl space-y-4">
          <SettingsPanel
            variant="subtle"
            className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <h4 className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                <PlayCircle className="h-4 w-4 text-orange-500" />
                Restart Welcome Tour
              </h4>
              <p className="mt-1 text-xs contrast-helper">
                Reset the Dashboard welcome tour, the Recordings walkthrough,
                and the transcript walkthrough.
              </p>
            </div>
            <button
              onClick={handleRestartTour}
              disabled={!userId}
              className={ACTION_BUTTON_STYLES}
            >
              Restart Tour
            </button>
          </SettingsPanel>

          <SettingsPanel
            variant="subtle"
            className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <h4 className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                <RefreshCw className="h-4 w-4 text-orange-500" />
                Re-create Demo Meeting
              </h4>
              <p className="mt-1 text-xs contrast-helper">
                If you deleted the &quot;Welcome to Nojoin&quot; meeting, this
                will create it again.
              </p>
            </div>
            <button
              onClick={handleRecreateDemo}
              disabled={isSeeding}
              className={ACTION_BUTTON_STYLES}
            >
              {isSeeding && <RefreshCw className="w-3 h-3 animate-spin" />}
              {isSeeding ? "Creating..." : "Re-create Meeting"}
            </button>
          </SettingsPanel>
        </div>
      </SettingsSection>

      <SettingsSection
        eyebrow="Support"
        title="Report a bug"
        description="Open the project issue tracker when you hit a reproducible problem or need to share diagnostics with the team."
        width="regular"
      >
        <div className="mx-auto max-w-2xl space-y-4">
          <SettingsPanel
            variant="subtle"
            className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between"
          >
            <div>
              <h4 className="flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                <Bug className="h-4 w-4 text-orange-500" />
                Report an Issue
              </h4>
              <p className="mt-1 text-xs contrast-helper">
                Found a bug? Report it on our GitHub Issues page.
              </p>
            </div>
            <a
              href="https://github.com/Valtora/Nojoin/issues"
              target="_blank"
              rel="noopener noreferrer"
              className={ACTION_BUTTON_STYLES}
            >
              Report Issue
            </a>
          </SettingsPanel>
        </div>
      </SettingsSection>
    </div>
  );
}
