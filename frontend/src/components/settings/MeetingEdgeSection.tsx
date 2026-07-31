import { HelpCircle } from "lucide-react";

import { Settings } from "@/types";
import {
  clampMeetingEdgeContextLevel,
  MEETING_EDGE_CONTEXT_OPTIONS,
} from "@/lib/meetingEdgeContext";
import Tooltip from "@/components/ui/Tooltip";
import { Switch } from "@/components/ui/Switch";
import SettingsCallout from "./SettingsCallout";
import SettingsBlock from "./SettingsBlock";
import SettingsCard from "./SettingsCard";
import SettingsStatusBadge from "./SettingsStatusBadge";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  getModelOptionsForProvider,
  getSelectedModelForProvider,
  withSelectedModelForProvider,
} from "./aiSettingsModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-control-border bg-control-bg text-foreground focus:ring-2 focus:ring-action outline-none transition-all disabled:opacity-50";

interface MeetingEdgeSectionProps {
  settings: Settings;
  /** Debounced apply (the technical-context slider). */
  onUpdate: (newSettings: Settings) => void;
  /** Apply and save immediately (the enable toggle and model select). */
  onPersist: (newSettings: Settings) => void;
  isAdmin: boolean;
  models: AISettingsModels;
}

/**
 * "Meeting Edge" section. Mixed scope, so scope is labelled per control:
 * `enable_meeting_edge` and the live model are install-wide (admin only, hidden
 * for non-admins), while `meeting_edge_context_level` is a genuine per-user
 * preference shown to everyone.
 */
export default function MeetingEdgeSection({
  settings,
  onUpdate,
  onPersist,
  isAdmin,
  models,
}: MeetingEdgeSectionProps) {
  const enabled = settings.enable_meeting_edge !== false;
  const contextLevel = clampMeetingEdgeContextLevel(
    settings.meeting_edge_context_level,
  );
  const selectedOption =
    MEETING_EDGE_CONTEXT_OPTIONS.find(
      (option) => option.value === contextLevel,
    ) ?? MEETING_EDGE_CONTEXT_OPTIONS[1];
  const liveModelOptions = getModelOptionsForProvider(
    settings,
    models.availableModels,
    "live",
  );
  const selectedLiveModel = getSelectedModelForProvider(settings, "live");

  return (
    <SettingsCard
      title="Meeting Edge"
      description="Live, in-meeting guidance that surfaces context and clarifies terms while you record."
    >
      <div className="space-y-4">
        <SettingsBlock className="space-y-6">
          {isAdmin ? (
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-foreground">
                  Enable Meeting Edge
                </div>
                <p className="mt-1 text-xs contrast-helper">
                  Turns the live advisory card and its model calls on for every
                  account on this server.
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                <SettingsStatusBadge tone="neutral">Admin-wide</SettingsStatusBadge>
                <Switch
                  checked={enabled}
                  onCheckedChange={(checked) =>
                    onPersist({ ...settings, enable_meeting_edge: checked })
                  }
                />
              </div>
            </div>
          ) : (
            !enabled && (
              <SettingsCallout tone="neutral">
                Meeting Edge is currently turned off by your server
                administrator.
              </SettingsCallout>
            )
          )}

          {isAdmin && (
            <div>
              <label className="text-sm font-medium text-contrast-muted mb-2 flex items-center gap-2">
                <Tooltip
                  content="Optional separate model for live Meeting Edge guidance. Leave blank to reuse the main server model."
                  position="right"
                >
                  <span className="flex items-center gap-1 cursor-help">
                    Meeting Edge model{" "}
                    <HelpCircle className="w-3 h-3 text-contrast-helper" />
                  </span>
                </Tooltip>
                <SettingsStatusBadge tone="neutral">Admin-wide</SettingsStatusBadge>
              </label>
              <select
                value={selectedLiveModel}
                onChange={(e) =>
                  onPersist(withSelectedModelForProvider(settings, "live", e.target.value))
                }
                disabled={!enabled || liveModelOptions.length === 0}
                className={SELECT_CLASS}
              >
                <option value="">Use main model</option>
                {liveModelOptions.map((model) => (
                  <option key={`meeting-edge-${model}`} value={model}>
                    {model}
                  </option>
                ))}
              </select>
              <p className="mt-1 text-xs contrast-helper">
                A faster model keeps live guidance responsive.
              </p>
            </div>
          )}

          <div className="rounded-xl border border-action-border bg-action-tint p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-foreground">
                  Technical context
                  <span className="text-[11px] font-semibold uppercase tracking-[0.14em] contrast-helper">
                    Your preference
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-contrast-helper">
                  Control how readily Meeting Edge explains terms in the Technical
                  Context section.
                </p>
              </div>
              <span className="inline-flex items-center rounded-full border border-action-border bg-surface-card px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-action-text">
                {selectedOption.label || `Level ${selectedOption.value}`}
              </span>
            </div>

            <input
              type="range"
              min={1}
              max={5}
              step={1}
              value={contextLevel}
              onChange={(e) =>
                onUpdate({
                  ...settings,
                  meeting_edge_context_level: Number(e.target.value),
                })
              }
              disabled={!enabled}
              aria-label="Meeting Edge Technical Context sensitivity"
              className="mt-4 w-full accent-action disabled:cursor-not-allowed disabled:opacity-50"
            />

            <div className="mt-3 grid grid-cols-5 gap-2 text-center text-[11px] font-medium text-contrast-helper">
              {MEETING_EDGE_CONTEXT_OPTIONS.map((option) => (
                <span key={option.value}>{option.label}</span>
              ))}
            </div>

            <p className="mt-3 text-xs leading-5 text-contrast-helper">
              {selectedOption.description}
            </p>
          </div>
        </SettingsBlock>
      </div>
    </SettingsCard>
  );
}
