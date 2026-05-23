"use client";

import {
  GlobalSpeaker,
  RecordingSpeaker,
  SpeakerCorrectionScope,
  TranscriptSpeakerAssignment,
} from "@/types";
import { ArrowRightLeft, Search, User, Users, Plus } from "lucide-react";
import { useState, useEffect, useLayoutEffect, useMemo, useRef } from "react";
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

const POPOVER_MARGIN = 8;
const POPOVER_GAP = 6;
const POPOVER_WIDTH = 360;
const ESTIMATED_POPOVER_HEIGHT = 340;
const MIN_POPOVER_HEIGHT = 180;

interface PopoverPosition {
  top: number;
  left: number;
  width: number;
  maxHeight: number;
}

const clamp = (value: number, min: number, max: number) => {
  return Math.min(Math.max(value, min), max);
};

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
  const [position, setPosition] = useState<PopoverPosition>({
    top: POPOVER_MARGIN,
    left: POPOVER_MARGIN,
    width: POPOVER_WIDTH,
    maxHeight: ESTIMATED_POPOVER_HEIGHT,
  });
  const [selectedScope, setSelectedScope] =
    useState<SpeakerCorrectionScope>("utterance_only");
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

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

  useLayoutEffect(() => {
    if (!targetElement) {
      return;
    }

    const updatePosition = () => {
      const rect = targetElement.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const width = Math.min(
        POPOVER_WIDTH,
        Math.max(0, viewportWidth - POPOVER_MARGIN * 2),
      );
      const measuredHeight =
        containerRef.current?.offsetHeight || ESTIMATED_POPOVER_HEIGHT;
      const availableBelow = Math.max(
        0,
        viewportHeight - rect.bottom - POPOVER_GAP - POPOVER_MARGIN,
      );
      const availableAbove = Math.max(
        0,
        rect.top - POPOVER_GAP - POPOVER_MARGIN,
      );
      const placeAbove =
        availableBelow < measuredHeight && availableAbove > availableBelow;
      const availableHeight = placeAbove ? availableAbove : availableBelow;
      const maxHeight = Math.min(
        viewportHeight - POPOVER_MARGIN * 2,
        Math.max(MIN_POPOVER_HEIGHT, availableHeight),
      );
      const renderedHeight = Math.min(measuredHeight, maxHeight);
      const nextPosition = {
        top: clamp(
          placeAbove ? rect.top - POPOVER_GAP - renderedHeight : rect.bottom + POPOVER_GAP,
          POPOVER_MARGIN,
          Math.max(POPOVER_MARGIN, viewportHeight - renderedHeight - POPOVER_MARGIN),
        ),
        left: clamp(
          rect.left,
          POPOVER_MARGIN,
          Math.max(POPOVER_MARGIN, viewportWidth - width - POPOVER_MARGIN),
        ),
        width,
        maxHeight,
      };

      setPosition((current) =>
        current.top === nextPosition.top &&
        current.left === nextPosition.left &&
        current.width === nextPosition.width &&
        current.maxHeight === nextPosition.maxHeight
          ? current
          : nextPosition,
      );
    };

    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);

    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [filteredAvailable.length, filteredGlobal.length, search, targetElement]);

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
    Icon: typeof ArrowRightLeft;
  }> = [
    {
      value: "utterance_only",
      label: "This utterance",
      Icon: ArrowRightLeft,
    },
    {
      value: "speaker_everywhere_in_recording",
      label: "Whole transcript",
      Icon: Users,
    },
  ];

  if (!targetElement) return null;

  return createPortal(
    <div
      ref={containerRef}
      className="fixed z-9999 flex flex-col overflow-hidden rounded-lg border-2 border-gray-300 bg-white/95 shadow-2xl backdrop-blur-sm animate-in fade-in zoom-in-95 duration-100 dark:border-gray-600 dark:bg-gray-800/95"
      style={{
        top: position.top,
        left: position.left,
        width: position.width,
        maxHeight: position.maxHeight,
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

      <div className="grid grid-cols-2 gap-1 border-b border-gray-100 bg-gray-50/80 p-2 dark:border-gray-700 dark:bg-gray-900/70">
        {scopeOptions.map(({ value, label, Icon }) => {
          const isSelected = selectedScope === value;

          return (
            <button
              key={value}
              type="button"
              aria-pressed={isSelected}
              onClick={() => setSelectedScope(value)}
              className={`flex min-h-8 items-center justify-center gap-1.5 rounded-md border px-2 text-xs font-semibold transition-colors ${
                isSelected
                  ? "border-orange-400 bg-orange-50 text-orange-700 dark:border-orange-500 dark:bg-orange-950/50 dark:text-orange-300"
                  : "border-gray-200 bg-white text-gray-600 hover:border-gray-300 hover:bg-gray-50 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-300 dark:hover:border-gray-600 dark:hover:bg-gray-700"
              }`}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{label}</span>
            </button>
          );
        })}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto py-1">
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
                  type="button"
                  onClick={() =>
                    onSelect({
                      name: option.displayName,
                      diarizationLabel: representativeLabel,
                      scope: selectedScope,
                    })
                  }
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
                >
                  <div className={`h-2 w-2 rounded-full ${colorOption.dot}`} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{option.displayName}</div>
                    <div className="truncate text-[11px] text-gray-400">
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
                type="button"
                onClick={() =>
                  onSelect({
                    name: s.name,
                    globalSpeakerId: s.id,
                    scope: selectedScope,
                  })
                }
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-700"
              >
                <User className="h-3 w-3 text-gray-400" />
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
                  onSelect({
                    name: search.trim(),
                    scope: selectedScope,
                  })
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
