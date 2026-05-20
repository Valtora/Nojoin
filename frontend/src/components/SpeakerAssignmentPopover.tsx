"use client";

import {
  GlobalSpeaker,
  RecordingSpeaker,
  SpeakerCorrectionScope,
  TranscriptSpeakerAssignment,
} from "@/types";
import { Search, User, Plus } from "lucide-react";
import { useState, useEffect, useMemo, useRef } from "react";
import { createPortal } from "react-dom";
import { getColorByKey } from "@/lib/constants";
import {
  buildGlobalSpeakerById,
  buildUniqueGlobalSpeakerIdByName,
  getRecordingSpeakerDisplayName,
  getRecordingSpeakerGroupKey,
  getResolvedGlobalSpeakerId,
} from "@/lib/recordingSpeakerUtils";

interface RecordingSpeakerOption {
  key: string;
  representative: RecordingSpeaker;
  members: RecordingSpeaker[];
  displayName: string;
  isGlobalLinked: boolean;
}

interface SpeakerAssignmentPopoverProps {
  availableSpeakers: RecordingSpeaker[];
  globalSpeakers: GlobalSpeaker[];
  currentSpeakerLabel: string;
  onSelect: (assignment: TranscriptSpeakerAssignment) => void;
  onClose: () => void;
  speakerColors: Record<string, string>;
  targetElement: HTMLElement | null;
}

export default function SpeakerAssignmentPopover({
  availableSpeakers,
  globalSpeakers,
  currentSpeakerLabel,
  onSelect,
  onClose,
  speakerColors,
  targetElement,
}: SpeakerAssignmentPopoverProps) {
  const [search, setSearch] = useState("");
  const [position, setPosition] = useState({ top: 0, left: 0 });
  const defaultScope = useMemo<SpeakerCorrectionScope>(
    () =>
      currentSpeakerLabel.startsWith("LIVE_")
        ? "from_this_utterance_forward"
        : "speaker_everywhere_in_recording",
    [currentSpeakerLabel],
  );
  const [selectedScope, setSelectedScope] =
    useState<SpeakerCorrectionScope>(defaultScope);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setSelectedScope(defaultScope);
  }, [defaultScope]);

  useEffect(() => {
    if (targetElement) {
      const updatePosition = () => {
        const rect = targetElement.getBoundingClientRect();
        setPosition({
          top: rect.bottom + window.scrollY,
          left: rect.left + window.scrollX,
        });
      };

      updatePosition();
      // Re-calculates position on scroll and resize to keep the popover anchored.
      window.addEventListener("resize", updatePosition);
      window.addEventListener("scroll", updatePosition, true);

      return () => {
        window.removeEventListener("resize", updatePosition);
        window.removeEventListener("scroll", updatePosition, true);
      };
    }
  }, [targetElement]);

  useEffect(() => {
    // Focus input after a short delay to ensure render
    setTimeout(() => inputRef.current?.focus(), 50);

    // Click outside to close
    const handleClickOutside = (event: MouseEvent) => {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        // Also check if the click was on the target element itself (to avoid immediate reopen/close loop if handled in parent)
        if (targetElement && targetElement.contains(event.target as Node)) {
          return;
        }
        onClose();
      }
    };

    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [onClose, targetElement]);

  const globalSpeakerById = useMemo(
    () => buildGlobalSpeakerById(globalSpeakers),
    [globalSpeakers],
  );

  const uniqueGlobalSpeakerIdByName = useMemo(
    () => buildUniqueGlobalSpeakerIdByName(availableSpeakers, globalSpeakerById),
    [availableSpeakers, globalSpeakerById],
  );

  const filteredAvailable = useMemo(() => {
    const groupedOptions = new Map<string, RecordingSpeaker[]>();
    const normalisedSearch = search.toLowerCase();

    availableSpeakers
      .filter((speaker) => speaker.diarization_label !== currentSpeakerLabel)
      .forEach((speaker) => {
        const key = getRecordingSpeakerGroupKey(
          speaker,
          globalSpeakerById,
          uniqueGlobalSpeakerIdByName,
        );
        const existingGroup = groupedOptions.get(key);

        if (existingGroup) {
          existingGroup.push(speaker);
          return;
        }

        groupedOptions.set(key, [speaker]);
      });

    return Array.from(groupedOptions.entries())
      .map(([key, members]) => {
        const representative =
          members.find((speaker) => getResolvedGlobalSpeakerId(speaker)) ||
          members[0];

        return {
          key,
          representative,
          members,
          displayName: getRecordingSpeakerDisplayName(
            representative,
            globalSpeakerById,
          ),
          isGlobalLinked: members.some((speaker) =>
            Boolean(getResolvedGlobalSpeakerId(speaker)),
          ),
        } satisfies RecordingSpeakerOption;
      })
      .filter((option) =>
        option.displayName.toLowerCase().includes(normalisedSearch),
      )
      .sort((left, right) => left.displayName.localeCompare(right.displayName));
  }, [
    availableSpeakers,
    currentSpeakerLabel,
    globalSpeakerById,
    search,
    uniqueGlobalSpeakerIdByName,
  ]);

  const filteredGlobal = globalSpeakers.filter(
    (s) =>
      s.name.toLowerCase().includes(search.toLowerCase()) &&
      !availableSpeakers.some(
        (as) => as.global_speaker_id === s.id || as.global_speaker?.id === s.id,
      ),
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (search.trim()) {
        onSelect({ name: search.trim(), scope: selectedScope });
      }
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  const scopeOptions: Array<{
    value: SpeakerCorrectionScope;
    label: string;
    description: string;
  }> = [
    {
      value: "utterance_only",
      label: "This utterance",
      description: "Change only this line.",
    },
    {
      value: "speaker_everywhere_in_recording",
      label: "Whole recording",
      description: "Apply to every matching turn in this recording.",
    },
    {
      value: "from_this_utterance_forward",
      label: "From here forward",
      description: "Update this point onward and future live matches.",
    },
  ];

  if (!targetElement) return null;

  return createPortal(
    <div
      ref={containerRef}
      className="absolute z-9999 mt-1 w-64 bg-white/95 dark:bg-gray-800/95 backdrop-blur-sm rounded-lg shadow-2xl border-2 border-gray-300 dark:border-gray-600 overflow-hidden flex flex-col animate-in fade-in zoom-in-95 duration-100"
      style={{
        top: position.top,
        left: position.left,
        minWidth: "200px",
      }}
    >
      <div className="p-2 border-b border-gray-100 dark:border-gray-700">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            ref={inputRef}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search or add..."
            className="w-full pl-7 pr-2 py-1.5 text-sm bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-md focus:outline-none focus:ring-2 focus:ring-orange-500 text-gray-900 dark:text-gray-100"
          />
        </div>
      </div>

      <div className="p-2 border-b border-gray-100 dark:border-gray-700 bg-gray-50/80 dark:bg-gray-900/70">
        <div className="text-[11px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider mb-2">
          Apply change to
        </div>
        <div className="grid gap-1">
          {scopeOptions.map((option) => {
            const isSelected = selectedScope === option.value;

            return (
              <button
                key={option.value}
                type="button"
                aria-pressed={isSelected}
                onClick={() => setSelectedScope(option.value)}
                className={`w-full rounded-md border px-2 py-1.5 text-left transition-colors ${
                  isSelected
                    ? "border-orange-400 bg-orange-50 text-orange-700 dark:border-orange-500 dark:bg-orange-950/50 dark:text-orange-300"
                    : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600 dark:hover:bg-gray-700"
                }`}
              >
                <div className="text-sm font-medium">{option.label}</div>
                <div className="text-[11px] leading-4 opacity-80">
                  {option.description}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="max-h-60 overflow-y-auto py-1">
        {/* Current Recording Speakers */}
        {filteredAvailable.length > 0 && (
          <div className="px-2 py-1">
            <div className="text-xs font-semibold text-gray-400 mb-1 uppercase tracking-wider">
              In this recording
            </div>
            {filteredAvailable.map((option) => {
              const representativeLabel = option.representative.diarization_label;
              const colorKey =
                speakerColors[representativeLabel] ||
                speakerColors[option.displayName] ||
                "gray";
              const colorOption = getColorByKey(colorKey);

              return (
                <button
                  key={option.key}
                  onClick={() =>
                    onSelect({
                      name: option.displayName,
                      diarizationLabel: representativeLabel,
                      scope: selectedScope,
                    })
                  }
                  className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 text-gray-700 dark:text-gray-200"
                >
                  <div className={`w-2 h-2 rounded-full ${colorOption.dot}`} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{option.displayName}</div>
                    <div className="text-[11px] text-gray-400 truncate">
                      {option.isGlobalLinked
                        ? "Linked to People library"
                        : "Recording only"}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        )}

        {/* Global Speakers */}
        {filteredGlobal.length > 0 && (
          <div className="px-2 py-1 border-t border-gray-100 dark:border-gray-700 mt-1 pt-2">
            <div className="text-xs font-semibold text-gray-400 mb-1 uppercase tracking-wider">
              Global Library
            </div>
            {filteredGlobal.map((s) => (
              <button
                key={s.id}
                onClick={() =>
                  onSelect({
                    name: s.name,
                    globalSpeakerId: s.id,
                    scope: selectedScope,
                  })
                }
                className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-gray-100 dark:hover:bg-gray-700 flex items-center gap-2 text-gray-700 dark:text-gray-200"
              >
                <User className="w-3 h-3 text-gray-400" />
                <span className="truncate">{s.name}</span>
              </button>
            ))}
          </div>
        )}

        {/* Create New */}
        {search.trim() &&
          !filteredAvailable.some(
            (option) =>
              option.displayName.toLowerCase() === search.trim().toLowerCase(),
          ) &&
          !filteredGlobal.some(
            (s) => s.name.toLowerCase() === search.trim().toLowerCase(),
          ) && (
            <div className="px-2 py-1 border-t border-gray-100 dark:border-gray-700 mt-1 pt-2">
              <button
                onClick={() =>
                  onSelect({ name: search.trim(), scope: selectedScope })
                }
                className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-orange-50 dark:hover:bg-orange-900/20 text-orange-600 dark:text-orange-400 flex items-center gap-2"
              >
                <Plus className="w-3 h-3" />
                <span className="truncate">Create local &quot;{search}&quot;</span>
              </button>
            </div>
          )}

        {filteredAvailable.length === 0 &&
          filteredGlobal.length === 0 &&
          !search.trim() && (
            <div className="px-4 py-2 text-xs text-gray-400 text-center">
              Type to search or add
            </div>
          )}
      </div>
    </div>,
    document.body,
  );
}
