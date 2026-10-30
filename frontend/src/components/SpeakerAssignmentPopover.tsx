"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
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
  isPlaceholderSpeakerName,
  liveSpeakerDisplayName,
  normaliseSpeakerName,
} from "@/lib/recordingSpeakerUtils";
import { DISABLED_LOOK, loadingButtonProps } from "@/components/ui/loadingButton";
import { TOUCH_TARGET_HEIGHT } from "@/components/ui/touchTarget";

interface RecordingSpeakerOption {
  key: string;
  representative: RecordingSpeaker;
  members: RecordingSpeaker[];
  displayName: string;
  isGlobalLinked: boolean;
  /** Its name and the names of the People it is linked to, normalised. */
  names: string[];
}

interface SpeakerAssignmentPopoverProps {
  availableSpeakers: readonly RecordingSpeaker[];
  /** The capture's live labels, the current one included, with the names they show. */
  liveSpeakerLabels?: Readonly<Record<string, string>>;
  /**
   * What a live speaker is offered for. "line" (the default): one line at a
   * time, so only with This utterance, as the server takes it. "speaker": the
   * whole speaker, which the caller merges into the live speaker picked.
   */
  liveTargets?: "line" | "speaker";
  /** False for a picker of this meeting's speakers alone: no People, nothing to create. */
  offerNames?: boolean;
  globalSpeakers: GlobalSpeaker[];
  currentSpeakerLabel: string;
  /** The name the current speaker shows, which typing it doesn't offer to create. */
  currentSpeakerName?: string;
  onSelect: (assignment: TranscriptSpeakerAssignment) => void;
  onClose: () => void;
  speakerColors: Record<string, string>;
  targetElement: HTMLElement | null;
  /** A plain voiceprint name that can be rejected without confirming it. */
  dismissibleName?: string;
  onDismissName?: () => Promise<void>;
  /** Panel choices always name the entire speaker, never one utterance. */
  fixedScope?: "speaker_everywhere_in_recording";
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
  liveSpeakerLabels,
  liveTargets = "line",
  offerNames = true,
  globalSpeakers,
  currentSpeakerLabel,
  currentSpeakerName,
  onSelect,
  onClose,
  speakerColors,
  targetElement,
  dismissibleName,
  onDismissName,
  fixedScope,
}: SpeakerAssignmentPopoverProps) {
  const [search, setSearch] = useState("");
  const [dismissingName, setDismissingName] = useState(false);
  const [position, setPosition] = useState<PopoverPosition>({
    top: POPOVER_MARGIN,
    left: POPOVER_MARGIN,
    width: POPOVER_WIDTH,
    maxHeight: ESTIMATED_POPOVER_HEIGHT,
  });
  const [selectedScope, setSelectedScope] =
    useState<SpeakerCorrectionScope>("utterance_only");
  const scope = fixedScope ?? selectedScope;
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
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

  const query = search.trim();
  const normalisedQuery = normaliseSpeakerName(query);
  const filteredAvailable = useMemo(() => {
    const groupedOptions = new Map<string, RecordingSpeaker[]>();

    availableSpeakers
      .filter((speaker) => speaker.diarization_label !== currentSpeakerLabel &&
        !liveSpeakerLabels?.[speaker.diarization_label])
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

        const displayName = getRecordingSpeakerDisplayName(
          representative,
          globalSpeakerById,
        );
        const linkedNames = members.flatMap((speaker) => {
          const id = getResolvedGlobalSpeakerId(speaker);
          const person = speaker.global_speaker ?? (id ? globalSpeakerById.get(id) : undefined);
          return person ? [person.name] : [];
        });
        return {
          key,
          representative,
          members,
          displayName,
          isGlobalLinked: linkedNames.length > 0,
          names: [...new Set([displayName, ...linkedNames].map(normaliseSpeakerName))],
        } satisfies RecordingSpeakerOption;
      })
      .filter((option) =>
        option.names.some((name) => name.includes(normalisedQuery)),
      )
      .sort((left, right) => left.displayName.localeCompare(right.displayName));
  }, [
    availableSpeakers,
    currentSpeakerLabel,
    globalSpeakerById,
    normalisedQuery,
    uniqueGlobalSpeakerIdByName,
    liveSpeakerLabels,
  ]);

  // Live speakers, first heard first. For one line they are offered with
  // This utterance only: Whole transcript would move a speaker, which a merge
  // does instead.
  const liveOffered = liveTargets === "speaker" || scope === "utterance_only";
  const filteredLive = liveOffered ? Object.entries(liveSpeakerLabels ?? {}).filter(([label, name]) =>
    label !== currentSpeakerLabel && normaliseSpeakerName(name).includes(normalisedQuery)) : [];
  const currentName = currentSpeakerName ?? liveSpeakerLabels?.[currentSpeakerLabel];

  // People already in this meeting are offered as its speakers instead.
  const peopleOutsideMeeting = useMemo(
    () => globalSpeakers.filter((person) => !availableSpeakers.some(
      (speaker) => getResolvedGlobalSpeakerId(speaker) === person.id,
    )),
    [globalSpeakers, availableSpeakers],
  );
  // A People library can be large, so it is listed only for what is typed,
  // names that start with it first.
  const filteredGlobal = useMemo(() => normalisedQuery && offerNames
    ? peopleOutsideMeeting
      .filter((person) => normaliseSpeakerName(person.name).includes(normalisedQuery))
      .sort((left, right) =>
        Number(!normaliseSpeakerName(left.name).startsWith(normalisedQuery)) -
          Number(!normaliseSpeakerName(right.name).startsWith(normalisedQuery)) ||
        left.name.localeCompare(right.name))
    : [], [peopleOutsideMeeting, normalisedQuery, offerNames]);

  // Every name this meeting already has, the current speaker's included: a
  // typed one picks that speaker rather than making a second one.
  const meetingNames = useMemo(() => new Set([
    ...availableSpeakers.map((speaker) => getRecordingSpeakerDisplayName(speaker, globalSpeakerById)),
    ...Object.values(liveSpeakerLabels ?? {}),
    ...(currentName ? [currentName] : []),
  ].map(normaliseSpeakerName)), [availableSpeakers, liveSpeakerLabels, currentName, globalSpeakerById]);
  const exactOption = filteredAvailable.find((option) => option.names.includes(normalisedQuery));
  const exactLive = filteredLive.find(([, name]) => normaliseSpeakerName(name) === normalisedQuery);
  const exactPerson = filteredGlobal.find((person) => normaliseSpeakerName(person.name) === normalisedQuery);
  const isCurrentName = !!currentName && normaliseSpeakerName(currentName) === normalisedQuery;
  const isPlaceholder = isPlaceholderSpeakerName(query);
  const canCreate = offerNames && !!query && !exactOption && !exactLive && !exactPerson &&
    !meetingNames.has(normalisedQuery) && !isPlaceholder;
  // A live speaker the typed name belongs to, held back because Whole
  // transcript is chosen.
  const liveNeedsOneLine = !liveOffered && !isCurrentName && Object.entries(liveSpeakerLabels ?? {}).some(
    ([label, name]) => label !== currentSpeakerLabel && normaliseSpeakerName(name) === normalisedQuery);
  const selectOption = (option: RecordingSpeakerOption) => onSelect({
    name: option.displayName,
    diarizationLabel: option.representative.diarization_label,
    scope,
  });
  const selectLive = ([label, name]: [string, string]) => onSelect({ name, diarizationLabel: label, scope });
  const selectPerson = (person: GlobalSpeaker) => onSelect({ name: person.name, globalSpeakerId: person.id, scope });

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
  }, [filteredAvailable.length, filteredGlobal.length, filteredLive.length, search, targetElement]);

  // Enter takes what the typed name means: this meeting's speaker or the
  // person of that name, else a new name where one is offered.
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault();
      if (exactOption) selectOption(exactOption);
      else if (exactLive) selectLive(exactLive);
      else if (exactPerson) selectPerson(exactPerson);
      else if (canCreate) onSelect({ name: query, scope });
    } else if (e.key === "Escape") {
      onClose();
    }
  };

  const dismissCurrentName = async () => {
    if (!onDismissName || dismissingName) return;
    setDismissingName(true);
    try {
      await onDismissName();
      onClose();
      targetElement?.focus();
    } finally {
      setDismissingName(false);
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
    <SurfaceBase
      data-ui-fill="float"
      data-ui-border="full" kind="float"
      ref={containerRef}
      className="@container fixed z-[var(--z-popover)] flex flex-col overflow-hidden rounded-lg border border-surface-float-border bg-surface-float shadow-float animate-in fade-in zoom-in-95 duration-100"
      style={{
        top: position.top,
        left: position.left,
        width: position.width,
        maxHeight: position.maxHeight,
      }}
    >
      <div className="p-2 border-b border-surface-divider">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-contrast-icon-muted" />
          <InputBase
            data-ui-fill="inset"
            data-ui-border="full"
            autoFocus
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search or add..."
            className="w-full pl-7 pr-2 py-1.5 text-sm bg-surface-inset border border-surface-border rounded-md focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring text-foreground"
          />
        </div>
      </div>

      {dismissibleName && onDismissName && (
        <div className="border-b border-surface-divider p-2">
          <ButtonBase
            type="button"
            className={`w-full truncate rounded px-2 py-1.5 text-left text-sm text-contrast-muted hover:bg-surface-inset ${TOUCH_TARGET_HEIGHT} ${DISABLED_LOOK}`}
            {...loadingButtonProps(dismissingName, undefined, dismissCurrentName)}
          >
            Not {dismissibleName}
          </ButtonBase>
        </div>
      )}

      {/* Below 21rem the popover has been clamped to a narrow viewport and
          "Whole transcript" no longer fits beside its twin, so the scope
          buttons stack rather than truncate. */}
      {fixedScope ? <p className="border-b border-surface-divider p-2 text-xs text-contrast-helper">Whole speaker in this recording</p> : <div className="grid gap-1 @min-[21rem]:grid-cols-2 border-b border-surface-divider bg-surface-inset p-2">
        {scopeOptions.map(({ value, label, Icon }) => {
          const isSelected = selectedScope === value;

          return (
            <ButtonBase
              key={value}
              type="button"
              aria-pressed={isSelected}
              onClick={() => setSelectedScope(value)}
              className={`flex min-h-8 items-center justify-center gap-1.5 rounded-md border px-2 text-xs font-semibold transition-colors ${
                isSelected
                  ? "border-action-border bg-action-tint text-action-tint-fg"
                  : "border-surface-border bg-surface-card text-contrast-helper hover:border-action-border hover:bg-surface-inset"
              }`}
            >
              <Icon className="h-3.5 w-3.5 shrink-0" />
              <span className="truncate">{label}</span>
            </ButtonBase>
          );
        })}
      </div>}

      <div className="min-h-0 flex-1 overflow-y-auto py-1">
        {/* Current Recording Speakers */}
        {(filteredAvailable.length > 0 || filteredLive.length > 0) && (
          <div className="px-2 py-1">
            <div className="text-xs font-semibold text-contrast-icon-muted mb-1 uppercase tracking-wider">
              In this recording
            </div>
            {filteredLive.map(([label, name]) => (
              <ButtonBase key={label} type="button"
                onClick={() => selectLive([label, name])}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-contrast-muted hover:bg-surface-inset">
                <div className="min-w-0 flex-1">
                  <div className="truncate">{name}</div>
                  <div className="truncate text-[11px] text-contrast-icon-muted">
                    {name === liveSpeakerDisplayName(label) ? "Recording only" : liveSpeakerDisplayName(label)}
                  </div>
                </div>
              </ButtonBase>
            ))}
            {filteredAvailable.map((option) => {
              const representativeLabel = option.representative.diarization_label;
              const colorKey =
                speakerColors[representativeLabel] ||
                speakerColors[option.displayName] ||
                "gray";
              const colorOption = getColorByKey(colorKey);

              return (
                <ButtonBase
                  key={option.key}
                  type="button"
                  onClick={() => selectOption(option)}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-contrast-muted hover:bg-surface-inset"
                >
                  <div className={`h-2 w-2 rounded-full ${colorOption.dot}`} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate">{option.displayName}</div>
                    <div className="truncate text-[11px] text-contrast-icon-muted">
                      {option.isGlobalLinked
                        ? "Linked to People library"
                        : "Recording only"}
                    </div>
                  </div>
                </ButtonBase>
              );
            })}
          </div>
        )}

        {/* Global Speakers */}
        {filteredGlobal.length > 0 && (
          <div className="px-2 py-1 border-t border-surface-divider mt-1 pt-2">
            <div className="text-xs font-semibold text-contrast-icon-muted mb-1 uppercase tracking-wider">
              People
            </div>
            {filteredGlobal.map((s) => (
              <ButtonBase
                key={s.id}
                type="button"
                onClick={() => selectPerson(s)}
                className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-contrast-muted hover:bg-surface-inset"
              >
                <User className="h-3 w-3 text-contrast-icon-muted" />
                <span className="truncate">{s.name}</span>
              </ButtonBase>
            ))}
          </div>
        )}

        {/* Create New: never a name this meeting has, nor a placeholder. */}
        {canCreate && (
            <div className="px-2 py-1 border-t border-surface-divider mt-1 pt-2">
              <ButtonBase
                onClick={() => onSelect({ name: query, scope })}
                className="w-full text-left px-2 py-1.5 text-sm rounded-md hover:bg-action-tint text-action-text flex items-center gap-2"
              >
                <Plus className="w-3 h-3" />
                <span className="truncate">Create local &quot;{query}&quot;</span>
              </ButtonBase>
            </div>
          )}

        <PickerHint
          searching={!!query}
          isCurrentName={isCurrentName && !exactOption && !exactLive}
          isPlaceholder={isPlaceholder && !exactOption && !exactLive && !exactPerson}
          liveNeedsOneLine={liveNeedsOneLine}
          currentSpeakerName={currentName}
          hasOptions={filteredAvailable.length > 0 || filteredLive.length > 0}
          hasPeople={offerNames && peopleOutsideMeeting.length > 0}
          offerNames={offerNames}
        />
      </div>
    </SurfaceBase>,
    document.body,
  );
}

/** What the picker says when the list alone doesn't: how to reach People, or why nothing is offered. */
function PickerHint({
  searching, isCurrentName, isPlaceholder, liveNeedsOneLine, currentSpeakerName, hasOptions, hasPeople, offerNames,
}: {
  searching: boolean;
  isCurrentName: boolean;
  isPlaceholder: boolean;
  liveNeedsOneLine: boolean;
  currentSpeakerName?: string;
  hasOptions: boolean;
  hasPeople: boolean;
  offerNames: boolean;
}) {
  const text = !searching
    ? hasPeople ? "Type to search People" : hasOptions ? null
      : offerNames ? "Type to search or add" : "No other speaker yet."
    : isCurrentName ? `This speaker is already ${currentSpeakerName}.`
    : liveNeedsOneLine ? "Choose This utterance to move this line to that live speaker."
    : isPlaceholder && offerNames ? "Type a real name, not a placeholder."
    : null;
  // Mounted throughout, so a screen reader hears the text change.
  return (
    <div role="status" className={text ? "px-4 py-2 text-xs text-contrast-icon-muted text-center" : undefined}>
      {text}
    </div>
  );
}
