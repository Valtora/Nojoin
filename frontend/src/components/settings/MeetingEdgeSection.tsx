import { InputBase } from "@/components/ui/nativeBases";
import Link from "next/link";

import { Settings } from "@/types";
import {
  clampMeetingEdgeContextLevel,
  MEETING_EDGE_CONTEXT_OPTIONS,
} from "@/lib/meetingEdgeContext";
import SettingsBlock from "./SettingsBlock";
import SettingsCallout from "./SettingsCallout";
import SettingsCard from "./SettingsCard";
import SettingsRow from "./SettingsRow";
import { settingsCategoryHref } from "./settingsCategories";

interface MeetingEdgeSectionProps {
  settings: Settings;
  /** Debounced apply (the technical-context slider). */
  onUpdate: (newSettings: Settings) => void;
  isAdmin: boolean;
}

/**
 * Each person's Meeting Edge preference. Whether Meeting Edge runs at all, and
 * on which model, is install-wide and lives under AI providers.
 */
export default function MeetingEdgeSection({
  settings,
  onUpdate,
  isAdmin,
}: MeetingEdgeSectionProps) {
  const enabled = settings.enable_meeting_edge !== false;
  const contextLevel = clampMeetingEdgeContextLevel(
    settings.meeting_edge_context_level,
  );
  const selected =
    MEETING_EDGE_CONTEXT_OPTIONS.find((option) => option.value === contextLevel) ??
    MEETING_EDGE_CONTEXT_OPTIONS[1];

  return (
    <SettingsCard
      id="notes-meeting-edge"
      title="Meeting Edge"
      description="Live guidance during a recording: questions to ask, points missed, and terms explained."
    >
      {!enabled && (
        <SettingsBlock>
          <SettingsCallout tone="neutral">
            {isAdmin ? (
              <>
                Meeting Edge is off for this installation.{" "}
                <Link
                  href={`${settingsCategoryHref("ai-providers")}#ai-providers-meeting-edge`}
                  className="font-medium text-action-text underline underline-offset-2 hover:text-action-text-hover"
                >
                  Turn it on under AI providers
                </Link>
                .
              </>
            ) : (
              "Meeting Edge is off for this installation. Your administrator can turn it on."
            )}
          </SettingsCallout>
        </SettingsBlock>
      )}

      <SettingsRow
        id="notes-meeting-edge-context"
        label="Technical context"
        controlId="meeting-edge-context-level"
        description={selected.description}
      >
        <div className="space-y-1">
          <InputBase
            id="meeting-edge-context-level"
            aria-describedby="meeting-edge-context-level-help"
            aria-valuetext={selected.description}
            type="range"
            min={1}
            max={5}
            step={1}
            value={contextLevel}
            onChange={(event) =>
              onUpdate({
                ...settings,
                meeting_edge_context_level: Number(event.target.value),
              })
            }
            disabled={!enabled}
            className="w-full accent-action disabled:cursor-not-allowed disabled:opacity-60"
          />
          <div className="flex justify-between text-xs contrast-helper" aria-hidden="true">
            <span>Fewer terms</span>
            <span>More terms</span>
          </div>
        </div>
      </SettingsRow>
    </SettingsCard>
  );
}
