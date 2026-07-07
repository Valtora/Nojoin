import { HelpCircle } from "lucide-react";

import { Settings } from "@/types";
import {
  clampMeetingEdgeContextLevel,
  MEETING_EDGE_CONTEXT_OPTIONS,
} from "@/lib/meetingEdgeContext";
import Tooltip from "@/components/ui/Tooltip";
import { Switch } from "@/components/ui/Switch";
import SettingsCallout from "./SettingsCallout";
import SettingsPanel from "./SettingsPanel";
import SettingsSection from "./SettingsSection";
import SettingsStatusBadge from "./SettingsStatusBadge";
import type { AISettingsModels } from "./useAISettingsModels";
import {
  getModelOptionsForProvider,
  getSelectedModelForProvider,
  withSelectedModelForProvider,
} from "./aiSettingsModels";

const SELECT_CLASS =
  "w-full p-2.5 rounded-lg border border-gray-300 dark:border-gray-600 bg-gray-50 dark:bg-gray-900 text-gray-900 dark:text-white focus:ring-2 focus:ring-orange-500 outline-none transition-all disabled:opacity-50";

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
    <SettingsSection
      eyebrow="AI"
      title="Meeting Edge"
      description="Live, in-meeting guidance that surfaces context and clarifies terms while you record."
      width="wide"
    >
      <div className="mx-auto max-w-3xl space-y-4">
        <SettingsPanel className="space-y-6">
          {isAdmin ? (
            <div className="flex items-center justify-between gap-3">
              <div>
                <div className="text-sm font-medium text-gray-900 dark:text-white">
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
              <label className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2 flex items-center gap-2">
                <Tooltip
                  content="Optional separate model for live Meeting Edge guidance. Leave blank to reuse the main server model."
                  position="right"
                >
                  <span className="flex items-center gap-1 cursor-help">
                    Meeting Edge model{" "}
                    <HelpCircle className="w-3 h-3 text-gray-500 dark:text-gray-400" />
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

          <div className="rounded-xl border border-orange-200/70 bg-orange-50/45 p-4 dark:border-orange-500/20 dark:bg-orange-500/5">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2 text-sm font-medium text-gray-900 dark:text-white">
                  Technical context
                  <span className="text-[11px] font-semibold uppercase tracking-[0.14em] contrast-helper">
                    Your preference
                  </span>
                </div>
                <p className="mt-1 text-xs leading-5 text-gray-600 dark:text-gray-300">
                  Control how readily Meeting Edge explains terms in the Technical
                  Context section.
                </p>
              </div>
              <span className="inline-flex items-center rounded-full border border-orange-200 bg-white px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-orange-700 dark:border-orange-500/20 dark:bg-gray-900 dark:text-orange-300">
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
              className="mt-4 w-full accent-orange-500 disabled:cursor-not-allowed disabled:opacity-50"
            />

            <div className="mt-3 grid grid-cols-5 gap-2 text-center text-[11px] font-medium text-gray-500 dark:text-gray-400">
              {MEETING_EDGE_CONTEXT_OPTIONS.map((option) => (
                <span key={option.value}>{option.label}</span>
              ))}
            </div>

            <p className="mt-3 text-xs leading-5 text-gray-600 dark:text-gray-300">
              {selectedOption.description}
            </p>
          </div>
        </SettingsPanel>
      </div>
    </SettingsSection>
  );
}
