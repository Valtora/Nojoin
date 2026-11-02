"use client";

import { ButtonBase, InputBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Recording, RecordingId, RecordingStatus, Tag } from "@/types";

import {
  Loader2,
  AlertCircle,
  HelpCircle,
  Pause,
  UploadCloud,
  Search,
  Filter,
  X,
  Archive,
  PanelLeftClose,
  PanelLeftOpen,
  Trash2,
  NotebookPen,
  MoreHorizontal,
} from "lucide-react";
import { cn } from "@/lib/cn";
import IconButton from "./ui/IconButton";
import { TOUCH_TARGET_SQUARE } from "./ui/touchTarget";
import ImportAudioButton from "./importAudio/ImportAudioButton";
import RecordingInfoModal from "./RecordingInfoModal";
import MeetingControls from "./MeetingControls";
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useHydrated } from "@/lib/useHydrated";
import {
  getGlobalSpeakers,
  RecordingFilters,
  getTags,
  getRecordingsCalendar,
  type BatchRecordingResult,
} from "@/lib/api";
import { useRecordingActions } from "./recordings/_hooks/useRecordingActions";
import {
  useRecordingSelection,
  useTouchLongPress,
} from "./recordings/_hooks/useRecordingSelection";
import {
  LIST_ACTIONS,
  permanentDeleteConfirmation,
  type ListActionId,
} from "./recordings/recordingListActions";
import {
  RECORDING_UPDATED_EVENT,
  dispatchRecordingRenamed,
  recordingRenameOf,
  subscribeRecordingRemoved,
  withRecordingName,
} from "@/lib/recordingEvents";
import MonthCalendar from "./MonthCalendar";
import { formatTimeZoneDay, formatTimeZoneLocale, localDayRangeToUtc } from "@/lib/timezone";
import { useUserTimeZone } from "@/lib/useUserTimeZone";
import { parseServerTimestamp } from "@/lib/serverTimestamp";
import { format, startOfMonth } from "date-fns";
import ContextMenu from "./ContextMenu";
import ConfirmationModal from "./ConfirmationModal";
import { DISCARD_RECORDING_MESSAGE, DISCARD_RECORDING_TITLE } from "./recordings/DiscardRecordingDialog";
import BatchActionBar from "./BatchActionBar";
import ReprocessDialog from "./ReprocessDialog";
import { GlobalSpeaker } from "@/types";
import { useNavigationStore } from "@/lib/store";
import { getColorByKey } from "@/lib/constants";
import { useViewportDensity } from "./ViewportDensityProvider";
import { Badge } from "./ui/Badge";
import { useForgetNotesOfDeletedRecordings } from "@/lib/notes/deletedRecordingNotes";
import { useUnsentRecordings } from "@/lib/notes/useNoteOutbox";
import { useRecordingStatusNotifications } from "./recordings/_hooks/useRecordingStatusNotifications";
import { useRecordingListPages } from "./recordings/_hooks/useRecordingListPages";
import { useSidebarResize } from "./recordings/_hooks/useSidebarResize";
import RecordingAttentionBadge from "./RecordingAttentionBadge";
import RecordingTagDots from "./recordings/RecordingTagDots";
import RecordingListFooter from "./recordings/RecordingListFooter";
import { RECORDINGS_PAGE_SIZE } from "./recordings/recordingListPages";
import { useCapture } from "@/lib/capture/CaptureProvider";

interface RowMenuItem {
  label: string;
  onClick: () => void;
  className?: string;
}

const formatDurationString = (seconds?: number) => {
  if (!seconds) return "0s";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);

  if (hours > 0) {
    return `${hours}hr ${minutes}${minutes === 1 ? "min" : "mins"}`;
  }
  return `${minutes}${minutes === 1 ? "min" : "mins"}`;
};

const formatDate = (dateString: string, timeZone: string) => {
  const date = parseServerTimestamp(dateString);
  if (!date) return "Unknown date";
  return formatTimeZoneDay(date, timeZone);
};

const formatTime = (date: Date, timeZone: string) => {
  return formatTimeZoneLocale(date, timeZone, "time", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
};

const StatusIcon = ({ recording }: { recording: Recording }) => {
  if (recording.import_stalled) {
    return (
      <span className="cursor-help" title="This import did not finish uploading.">
        <AlertCircle className="w-3 h-3 text-status-warning-fg" />
      </span>
    );
  }
  switch (recording.status) {
    case RecordingStatus.PROCESSED:
      return null;
    case RecordingStatus.QUEUED:
      return (
        <span
          className="cursor-help"
          title="Meeting is in queue to be processed..."
        >
          <Loader2 className="w-3 h-3 text-status-info-fg animate-spin" />
        </span>
      );
    case RecordingStatus.PROCESSING:
      return (
        <span
          className="cursor-help"
          title="Processing: transcription, diarization, voiceprints."
        >
          <Loader2 className="w-3 h-3 text-status-info-fg animate-spin" />
        </span>
      );
    case RecordingStatus.UPLOADING:
      return <UploadCloud className="w-3 h-3 text-action-text animate-pulse" />;
    case RecordingStatus.PAUSED:
      return <Pause className="w-3 h-3 text-status-warning-fg" />;
    case RecordingStatus.ERROR:
      return (
        <span
          role="img"
          aria-label="Processing failed"
          className="cursor-help"
          title="Processing failed. Open the recording to see why and retry."
        >
          <AlertCircle className="w-3 h-3 text-danger-text" />
        </span>
      );
    default:
      return <HelpCircle className="w-3 h-3 text-rail-fg-muted" />;
  }
};

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { refreshPausedRecording } = useCapture();
  const actions = useRecordingActions();
  const unsentRecordings = useUnsentRecordings();
  const { isCompact, isDesktop: isDesktopLayout } = useViewportDensity();
  const {
    currentView,
    selectedTagIds,
    toggleTagFilter,
    clearTagFilters,
    clearSelection,
    recordingsSidebarWidth,
    setRecordingsSidebarWidth,
    isRecordingsSidebarCollapsed,
    toggleRecordingsSidebarCollapse,
  } = useNavigationStore();

  const mounted = useHydrated();
  // The menu's items are built when it opens, as they act on the list. A
  // right-click opens it at the pointer; a row's actions button anchors it.
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    items: RowMenuItem[];
    label: string;
    returnFocusTo: HTMLElement;
    /** The row whose actions button holds the menu, when one does. */
    buttonFor?: RecordingId;
    anchor?: HTMLElement;
  } | null>(null);
  const closeContextMenu = useCallback(() => setContextMenu(null), []);
  const [renamingId, setRenamingId] = useState<RecordingId | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [showInfoModal, setShowInfoModal] = useState(false);
  const [infoModalRecording, setInfoModalRecording] =
    useState<Recording | null>(null);
  const [reprocessRecordingId, setReprocessRecordingId] = useState<RecordingId | null>(null);
  const MIN_SIDEBAR_WIDTH = isCompact ? 240 : 256;
  const MAX_SIDEBAR_WIDTH = isCompact ? 520 : 600;
  const COLLAPSED_SIDEBAR_WIDTH = isCompact ? 60 : 64;
  const resolvedSidebarWidth = isCompact
    ? Math.min(recordingsSidebarWidth, 304)
    : recordingsSidebarWidth;

  // The persisted collapse only applies on desktop, and we fall back to
  // expanded until mounted to avoid a hydration mismatch (mirrors MainNav).
  const isCollapsed = mounted && isDesktopLayout && isRecordingsSidebarCollapsed;

  const { isResizing, onResizePointerDown: handleResizePointerDown } = useSidebarResize({
    minWidth: MIN_SIDEBAR_WIDTH,
    maxWidth: MAX_SIDEBAR_WIDTH,
    setWidth: setRecordingsSidebarWidth,
  });

  // Search & Filter State
  const [searchQuery, setSearchQuery] = useState("");
  const [debouncedSearchQuery, setDebouncedSearchQuery] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const [globalSpeakers, setGlobalSpeakers] = useState<GlobalSpeaker[]>([]);
  const [selectedSpeakers, setSelectedSpeakers] = useState<number[]>([]);
  const [dateMode, setDateMode] = useState<"range" | "before" | "after">(
    "range",
  );
  const [dateRange, setDateRange] = useState<{ start: string; end: string }>({
    start: "",
    end: "",
  });
  const [tags, setTags] = useState<Tag[]>([]);
  const [calendarMonth, setCalendarMonth] = useState<Date>(() =>
    startOfMonth(new Date()),
  );
  const [calendarMarkedDays, setCalendarMarkedDays] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectedCalendarDay, setSelectedCalendarDay] = useState<string | null>(
    null,
  );
  // The day filter, the calendar's marked days and the times shown all use
  // the user's time-zone setting.
  const userTimeZone = useUserTimeZone();

  // Load per-day recording counts for the viewed calendar month
  useEffect(() => {
    let active = true;
    const monthKey = format(calendarMonth, "yyyy-MM");
    getRecordingsCalendar(monthKey, userTimeZone)
      .then((calendar) => {
        if (!active) {
          return;
        }
        setCalendarMarkedDays(
          new Set(
            calendar.day_counts
              .filter((entry) => entry.count > 0)
              .map((entry) => entry.date),
          ),
        );
      })
      .catch(() => {
        if (active) {
          setCalendarMarkedDays(new Set());
        }
      });
    return () => {
      active = false;
    };
  }, [calendarMonth, userTimeZone]);

  // Load tags for filter pills
  useEffect(() => {
    const loadTags = async () => {
      try {
        const data = await getTags();
        setTags(data);

            } catch (error: unknown) {
        console.error("Failed to load tags:", error);
      }
    };
    void loadTags();

    const handleTagsUpdated = () => {
      void loadTags();
    };
    window.addEventListener("tags-updated", handleTagsUpdated);
    return () => window.removeEventListener("tags-updated", handleTagsUpdated);
  }, []);

  // Search, the filters and the view all go to the server, which pages the
  // list newest first (issues #900 and #901).
  const listFilters = useMemo(() => {
    const filters: RecordingFilters = {};
    const query = debouncedSearchQuery.trim();
    if (query) filters.q = query;
    if ((dateMode === "range" || dateMode === "after") && dateRange.start) {
      filters.start_date = localDayRangeToUtc(dateRange.start, userTimeZone).startISO;
    }
    if ((dateMode === "range" || dateMode === "before") && dateRange.end) {
      filters.end_date = localDayRangeToUtc(dateRange.end, userTimeZone).endISO;
    }
    if (selectedSpeakers.length > 0) filters.speaker_ids = selectedSpeakers;
    if (selectedTagIds.length > 0) filters.tag_ids = selectedTagIds;
    if (currentView === "archived") filters.only_archived = true;
    if (currentView === "deleted") filters.only_deleted = true;
    return filters;
  }, [
    debouncedSearchQuery,
    dateMode,
    dateRange,
    userTimeZone,
    selectedSpeakers,
    selectedTagIds,
    currentView,
  ]);

  // A hidden tab's badges can wait for the first refresh after it shows.
  const refreshBadges = useCallback(() => {
    if (document.visibilityState !== "hidden") {
      void refreshPausedRecording().catch(() => {});
    }
  }, [refreshPausedRecording]);

  const list = useRecordingListPages(listFilters, refreshBadges);
  const {
    recordings,
    hasLoaded: hasLoadedRecordings,
    refresh: refreshList,
    update: updateRows,
  } = list;
  const fetchRecordings = useCallback(() => refreshList("all"), [refreshList]);

  useRecordingStatusNotifications(recordings);
  useForgetNotesOfDeletedRecordings(hasLoadedRecordings ? recordings : null);

  const shownIds = useMemo(
    () => recordings.map((recording) => recording.id),
    [recordings],
  );
  const selection = useRecordingSelection(shownIds);
  const longPress = useTouchLongPress(selection.toggle);

  // Debounce search query
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearchQuery(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Confirmation Modal State
  const [confirmModal, setConfirmModal] = useState<{
    isOpen: boolean;
    title: string;
    message: string;
    onConfirm: () => void;
    confirmText?: string;
    cancelText?: string;
    isDangerous?: boolean;
  }>({
    isOpen: false,
    title: "",
    message: "",
    onConfirm: () => {},
  });


  useEffect(() => {
    const interval = setInterval(() => refreshList("poll"), 15000);
    return () => clearInterval(interval);
  }, [refreshList]);

  useEffect(() => {
    getGlobalSpeakers().then(setGlobalSpeakers).catch(console.error);
  }, []);

  // A rename is applied in place. Anything else, or a tag change, re-reads the list.
  useEffect(() => {
    const handleUpdate = (event: Event) => {
      const rename = recordingRenameOf(event);
      if (rename) {
        updateRows((prev) => withRecordingName(prev, rename));
        return;
      }
      fetchRecordings();
    };
    window.addEventListener(RECORDING_UPDATED_EVENT, handleUpdate);
    window.addEventListener("tags-updated", handleUpdate);
    return () => {
      window.removeEventListener(RECORDING_UPDATED_EVENT, handleUpdate);
      window.removeEventListener("tags-updated", handleUpdate);
    };
  }, [fetchRecordings, updateRows]);

  // A discard from any surface drops the row here immediately rather than
  // leaving it on screen until the 15s poll comes round.
  useEffect(
    () =>
      subscribeRecordingRemoved((id) => {
        updateRows((prev) => prev.filter((r) => r.id !== id));
      }),
    [updateRows],
  );

  // Clear selection when view changes
  useEffect(() => {
    clearSelection();
  }, [currentView, clearSelection]);

  const rowMenuLabel = (recording: Recording) => `Actions for ${recording.name}`;

  const handleContextMenu = (e: React.MouseEvent<HTMLElement>, recording: Recording) => {
    e.preventDefault();
    // A touch held on a row selects it; Android raises contextmenu for it.
    if (longPress.claimContextMenu(recording.id)) return;
    setContextMenu({
      x: e.clientX,
      y: e.clientY,
      items: getContextMenuItems(recording),
      label: rowMenuLabel(recording),
      returnFocusTo: e.currentTarget,
    });
  };

  /**
   * The row's actions button: the way to the row menu on a touch screen,
   * where a long press selects. It opens the menu right-click opens, against
   * the button, and a second press closes it.
   */
  const toggleRowMenu = (button: HTMLButtonElement, recording: Recording) => {
    if (contextMenu?.buttonFor === recording.id) {
      setContextMenu(null);
      return;
    }
    const box = button.getBoundingClientRect();
    setContextMenu({
      x: box.right,
      y: box.bottom,
      items: getContextMenuItems(recording),
      label: rowMenuLabel(recording),
      returnFocusTo: button,
      buttonFor: recording.id,
      anchor: button,
    });
  };

  const listRef = useRef<HTMLUListElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
  // Where focus goes once the rows an action took away have gone, or the
  // batch bar holding focus has closed: a row link by id, or the search box.
  const pendingFocusRef = useRef<RecordingId | "search" | null>(null);
  // Where the confirmation dialog sends focus as it closes, in place of the
  // element that opened it, which a permanent delete takes away.
  const confirmFocusRef = useRef<HTMLElement | null>(null);

  const focusTargetElement = (target: RecordingId | "search") => {
    const link =
      target === "search"
        ? undefined
        : Array.from(
            listRef.current?.querySelectorAll<HTMLElement>("[data-recording-link]") ??
              [],
          ).find((element) => element.dataset.recordingLink === target);
    return link ?? searchInputRef.current;
  };

  useEffect(() => {
    const target = pendingFocusRef.current;
    if (target === null) return;
    pendingFocusRef.current = null;
    focusTargetElement(target)?.focus();
  });

  /** The row after the first one going, else the one before it, else the search box. */
  const focusTargetAfterRemoving = (ids: RecordingId[]): RecordingId | "search" => {
    const gone = new Set(ids);
    const first = shownIds.findIndex((id) => gone.has(id));
    const after = shownIds.slice(first + 1).find((id) => !gone.has(id));
    const before = shownIds
      .slice(0, Math.max(first, 0))
      .reverse()
      .find((id) => !gone.has(id));
    return after ?? before ?? "search";
  };

  const focusAfterRemoving = (ids: RecordingId[]) => {
    pendingFocusRef.current = focusTargetAfterRemoving(ids);
  };

  const removeRows = (ids: RecordingId[]) => {
    const gone = new Set(ids);
    updateRows((prev) => prev.filter((r) => !gone.has(r.id)));
  };

  const leaveIfOpen = (ids: RecordingId[]) => {
    if (ids.some((id) => pathname === `/recordings/${id}`)) {
      router.push("/recordings");
    }
  };

  // An undo brings back rows this list dropped, so every list reloads.
  const refreshAfterUndo = () => {
    window.dispatchEvent(new CustomEvent("recording-updated", { detail: {} }));
  };

  /**
   * Archive, unarchive, move to the bin or restore, for one row from its menu
   * or for the selection from the batch bar. The rows go at once, the
   * notification reports what the server changed, with Undo, and the list
   * reloads to match.
   */
  const moveRecordings = (
    action: "archive" | "unarchive" | "softDelete" | "restore",
    ids: RecordingId[],
  ) => {
    if (ids.length === 0) return;
    focusAfterRemoving(ids);
    removeRows(ids);
    const callbacks = {
      onSuccess: (result: BatchRecordingResult) => {
        if (action === "softDelete") leaveIfOpen(result.recording_ids);
        void fetchRecordings();
      },
      onError: () => void fetchRecordings(),
      onUndone: refreshAfterUndo,
    };
    switch (action) {
      case "archive":
        void actions.archive(ids, callbacks);
        break;
      case "unarchive":
        void actions.unarchive(ids, callbacks);
        break;
      case "restore":
        void actions.restore(ids, callbacks);
        break;
      case "softDelete":
        void actions.softDelete(ids, {
          ...callbacks,
          from: view === "archived" ? "archived" : "recordings",
        });
        break;
    }
  };

  /** The one list action that asks first, as it cannot be undone. */
  const confirmPermanentDelete = (ids: RecordingId[]) => {
    if (ids.length === 0) return;
    confirmFocusRef.current = null;
    setConfirmModal({
      isOpen: true,
      ...permanentDeleteConfirmation(ids.length),
      isDangerous: true,
      onConfirm: () => {
        const target = focusTargetAfterRemoving(ids);
        pendingFocusRef.current = target;
        confirmFocusRef.current = focusTargetElement(target);
        removeRows(ids);
        void actions.permanentDelete(ids, {
          onSuccess: (result) => {
            leaveIfOpen(result.recording_ids);
            void fetchRecordings();
          },
          onError: () => void fetchRecordings(),
        });
      },
    });
  };

  const runListAction = (
    action: Exclude<ListActionId, "tag">,
    ids: RecordingId[],
  ) => {
    if (action === "permanentDelete") {
      confirmPermanentDelete(ids);
    } else {
      moveRecordings(action, ids);
    }
  };

  const tagRecordings = (ids: RecordingId[], tagName: string) => {
    const announce = () => window.dispatchEvent(new CustomEvent("tags-updated"));
    void actions.tag(ids, tagName, { onSuccess: announce, onUndone: announce });
  };

  const clearSelectionFromBar = () => {
    // The bar goes with the selection, so focus returns to the list.
    pendingFocusRef.current =
      selection.anchorId ?? selection.selectedIds[0] ?? "search";
    selection.clear();
  };

  const handleRenameStart = (id: RecordingId, currentName: string) => {
    setRenamingId(id);
    setRenameValue(currentName);
    setContextMenu(null);
  };

  const handleRenameSubmit = async (id: RecordingId) => {
    if (!renameValue.trim()) return;

    const previousName = recordings.find((r) => r.id === id)?.name;
    setRenamingId(null);

    // This list, the open recording and the dashboard take the name now, and
    // a refusal puts the old one back in each.
    dispatchRecordingRenamed(id, renameValue);

    await actions.rename(id, renameValue, {
      onError: () => {
        if (previousName !== undefined) dispatchRecordingRenamed(id, previousName);
        void fetchRecordings();
      },
    });
  };

  const handleInferSpeakers = async (id: RecordingId) => {
    await actions.inferSpeakers(id, {
      onSuccess: () => {
        // Dispatch event to notify other components (like the detail page)
        window.dispatchEvent(
          new CustomEvent("recording-updated", { detail: { id } }),
        );
        fetchRecordings();
      },
    });
  };

  const handleDiscard = (id: RecordingId) => {
    confirmFocusRef.current = null;
    setConfirmModal({
      isOpen: true,
      title: DISCARD_RECORDING_TITLE,
      message: DISCARD_RECORDING_MESSAGE,
      isDangerous: true,
      confirmText: "Discard",
      onConfirm: async () => {
        updateRows((prev) => prev.filter((r) => r.id !== id));
        await actions.discard(id, {
          onSuccess: () => {
            if (pathname === `/recordings/${id}`) {
              router.push("/recordings");
            }
          },
          onError: fetchRecordings,
        });
      },
    });
  };

  /**
   * Ctrl or Cmd toggles a row and Shift extends from the anchor. While
   * anything is selected a plain click toggles too, rather than opening the
   * recording, so a click can never act on a selection by surprise.
   */
  const handleRecordingClick = (e: React.MouseEvent, id: RecordingId) => {
    if (longPress.consumeClick(id)) {
      e.preventDefault();
      return;
    }
    if (e.ctrlKey || e.metaKey) {
      e.preventDefault();
      selection.toggle(id);
    } else if (e.shiftKey) {
      e.preventDefault();
      selection.extendTo(id);
    } else if (selection.isSelecting) {
      e.preventDefault();
      selection.toggle(id);
    }
  };

  const handleRecordingKeyDown = (e: React.KeyboardEvent, id: RecordingId) => {
    if (e.key !== " ") return;
    e.preventDefault();
    if (e.shiftKey) {
      selection.extendTo(id);
    } else {
      selection.toggle(id);
    }
  };

  const handleListKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape" && selection.isSelecting) {
      e.preventDefault();
      e.stopPropagation();
      selection.clear();
    }
  };

  const getContextMenuItems = (recording: Recording) => {
    const items: RowMenuItem[] = [];

    if (view === "recordings") {
      items.push(
        {
          label: "Rename",
          onClick: () => handleRenameStart(recording.id, recording.name),
        },
        // Only a finished meeting: the run holds the recording in processing
        // and hands it back as processed, which would end a capture (#832).
        ...(recording.status === RecordingStatus.PROCESSED
          ? [
              {
                label: "Retry Speaker Inference",
                onClick: () => handleInferSpeakers(recording.id),
              },
            ]
          : []),
        // A finished meeting regenerating notes, inferring speakers or being
        // reprocessed shows as processing too, and goes to the bin (#833).
        ...(recording.discardable
          ? [
              {
                label: "Discard Recording",
                onClick: () => handleDiscard(recording.id),
                className: "text-danger-text",
              },
            ]
          : []),
        ...(recording.status !== RecordingStatus.PROCESSING &&
        recording.status !== RecordingStatus.QUEUED &&
        recording.status !== RecordingStatus.UPLOADING &&
        recording.status !== RecordingStatus.PAUSED
          ? [
              {
                label: "Retry Processing",
                onClick: () => setReprocessRecordingId(recording.id),
              },
            ]
          : []),
        {
          label: "Show Recording Info",
          onClick: () => {
            setInfoModalRecording(recording);
            setShowInfoModal(true);
          },
        },
        {
          label: LIST_ACTIONS.archive.label,
          onClick: () => moveRecordings("archive", [recording.id]),
        },
        {
          label: LIST_ACTIONS.softDelete.label,
          onClick: () => moveRecordings("softDelete", [recording.id]),
        },
      );
    } else if (view === "archived") {
      items.push(
        {
          label: LIST_ACTIONS.unarchive.label,
          onClick: () => moveRecordings("unarchive", [recording.id]),
        },
        {
          label: LIST_ACTIONS.softDelete.label,
          onClick: () => moveRecordings("softDelete", [recording.id]),
        },
      );
    } else if (view === "deleted") {
      items.push(
        {
          label: LIST_ACTIONS.restore.label,
          onClick: () => moveRecordings("restore", [recording.id]),
        },
        {
          label: LIST_ACTIONS.permanentDelete.label,
          className:
            "text-danger-text hover:bg-status-danger-bg",
          onClick: () => confirmPermanentDelete([recording.id]),
        },
      );
    }

    return items;
  };

  const getEmptyMessage = () => {
    switch (view) {
      case "archived":
        return {
          main: "No archived recordings.",
          sub: "Archived recordings will appear here.",
        };
      case "deleted":
        return {
          main: "The bin is empty.",
          sub: "Recordings you move to the bin appear here.",
        };
      default:
        return {
          main: "No recordings found.",
          sub: "Start a new meeting or import audio to get started.",
        };
    }
  };

  const hasActiveFilters =
    searchQuery ||
    dateRange.start ||
    dateRange.end ||
    selectedSpeakers.length > 0 ||
    selectedTagIds.length > 0;

  // A new search or filter is on its way while the last one's rows stay up.
  const isSearching = list.isLoadingQuery && hasLoadedRecordings;
  const appliedSearch = listFilters.q ?? "";
  const searchStatus = !appliedSearch
    ? ""
    : isSearching
      ? "Searching..."
      : recordings.length === 0
        ? "No recordings match."
        : list.hasMore
          ? `${recordings.length} matches shown, more load as you scroll.`
          : `${recordings.length} ${recordings.length === 1 ? "match" : "matches"}.`;

  // Prevent hydration mismatch
  const view = mounted ? currentView : "recordings";
  const currentViewLabel =
    view === "archived" ? "Archived" : view === "deleted" ? "Deleted" : "Recordings";

  // Determine if we should hide the sidebar on mobile
  const isRecordingView = pathname?.startsWith("/recordings/");

  // Tag dots sit in the gutter the hover checkbox keeps on a fine pointer. On
  // touch that gutter opens only while selecting, so a list holding any
  // tagged meeting keeps it open on every card and the titles stay aligned.
  const reserveTagGutter = recordings.some(
    (recording) => (recording.tags?.length ?? 0) > 0,
  );

  // Collapse and expand animate the width. A drag sets it on every pointer
  // move, so it goes without a transition and the edge stays on the pointer.
  const widthTransition = isResizing
    ? ""
    : "transition-[width] duration-300 motion-reduce:transition-none";

  return (
    <aside
      id="sidebar-recordings-list"
      className={`shrink-0 border-r border-rail-border bg-rail flex flex-col h-full relative ${widthTransition} ${
        isRecordingView ? "hidden lg:flex" : "w-full lg:flex"
      }`}
      style={
        mounted && isDesktopLayout
          ? {
              width: isCollapsed
                ? `${COLLAPSED_SIDEBAR_WIDTH}px`
                : `${resolvedSidebarWidth}px`,
            }
          : {}
      }
    >
      {isCollapsed && (
        <div className="flex flex-col items-center gap-1 p-2">
          <IconButton
            onClick={toggleRecordingsSidebarCollapse}
            size="sm"
            icon={<PanelLeftOpen aria-hidden="true" />}
            className="text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
            title="Expand recordings list"
            aria-label="Expand recordings list"
            aria-expanded={false}
          />
          {view === "recordings" ? (
            <MeetingControls variant="rail" onMeetingEnd={fetchRecordings} />
          ) : (
            <span
              className="flex h-10 w-10 items-center justify-center text-rail-fg-muted"
              title={currentViewLabel}
              role="img"
              aria-label={currentViewLabel}
            >
              {view === "archived" ? (
                <Archive aria-hidden="true" className="h-4 w-4" />
              ) : (
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              )}
            </span>
          )}
          {view === "recordings" && <ImportAudioButton appearance="icon" />}
        </div>
      )}
      {!isCollapsed && (
      // `relative` keeps absolutely positioned descendants, such as the batch
      // bar's sr-only status line, inside this scroller. Without it their
      // containing block is the aside, and the full list's height leaks out
      // as scrollable overflow on the app shell.
      <div ref={scrollRef} className="relative flex-1 overflow-y-auto">
      {view === "recordings" && (
        <div className="hidden lg:block">
          <MeetingControls
            onMeetingEnd={fetchRecordings}
            pairedAction={<ImportAudioButton className="w-full" />}
          />
        </div>
      )}

      {/* Header */}
      <div className="p-4 border-b border-rail-border">
        <div className="mb-3 flex items-center gap-3 lg:hidden">
          <div className="min-w-0">
            <div className="text-[11px] font-semibold uppercase tracking-[0.18em] text-rail-fg-muted">
              Library
            </div>
            <div className="text-sm font-semibold text-rail-fg">
              {currentViewLabel}
            </div>
          </div>
          {view === "recordings" && (
            <div className="ml-auto flex shrink-0 items-center gap-2">
              <MeetingControls variant="compact" onMeetingEnd={fetchRecordings} />
              <ImportAudioButton appearance="compact" />
            </div>
          )}
        </div>

        <div className="space-y-2">
          <p aria-live="polite" className="sr-only">
            {searchStatus}
          </p>
          <div className="flex items-center gap-1">
            <div className="relative min-w-0 flex-1">
              {isSearching ? (
                <Loader2
                  aria-hidden="true"
                  className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-contrast-icon-muted"
                />
              ) : (
                <Search
                  aria-hidden="true"
                  className="absolute left-3 top-1/2 transform -translate-y-1/2 w-4 h-4 text-contrast-icon-muted"
                />
              )}
              <InputBase
                data-ui-fill="control"
                data-ui-border="full"
                ref={searchInputRef}
                type="text"
                placeholder="Search..."
                aria-label="Search recordings"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-9 py-2 text-sm bg-control-bg border border-control-border rounded-lg text-foreground placeholder:text-control-placeholder focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-focus-ring"
              />
              <ButtonBase
                onClick={() => setShowFilters(!showFilters)}
                aria-label="Filter recordings"
                title="Filter recordings"
                aria-expanded={showFilters}
                className={`absolute right-2 top-1/2 transform -translate-y-1/2 p-1 rounded-md hover:bg-rail-item-hover ${showFilters ? "text-action-text" : "text-contrast-icon-muted"}`}
              >
                <Filter aria-hidden="true" className="w-4 h-4" />
              </ButtonBase>
            </div>
            <IconButton
              onClick={toggleRecordingsSidebarCollapse}
              size="sm"
              icon={<PanelLeftClose aria-hidden="true" />}
              className="hidden lg:inline-flex text-rail-fg-muted hover:bg-rail-item-hover hover:text-rail-fg"
              title="Collapse recordings list"
              aria-label="Collapse recordings list"
              aria-expanded={true}
            />
          </div>
        </div>

        {/* Active Tag Filter Pills */}
        {selectedTagIds.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 mt-2">
            {selectedTagIds.map((tagId) => {
              const tag = tags.find((t) => t.id === tagId);
              if (!tag) return null;
              const color = getColorByKey(tag.color);
              return (
                <div
                  key={tagId}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-pill bg-status-neutral-bg border border-status-neutral-border text-xs group hover:border-action-border transition-colors"
                >
                  <span
                    className={`w-2 h-2 rounded-full ${color.dot} shrink-0`}
                  />
                  <span className="text-status-neutral-fg font-medium">
                    {tag.name}
                  </span>
                  <ButtonBase
                    onClick={() => toggleTagFilter(tagId)}
                    className="ml-0.5 text-contrast-icon-muted hover:text-danger-text transition-colors"
                    title={`Remove ${tag.name} filter`}
                  >
                    <X className="w-3 h-3" />
                  </ButtonBase>
                </div>
              );
            })}
            <ButtonBase
              data-ui-hover="status-danger"
              data-ui-border="full" chrome="action"
              onClick={clearTagFilters}
              className="px-2.5 py-1 rounded-pill text-xs font-medium text-danger-text hover:bg-status-danger-bg border border-status-danger-border transition-colors"
              title="Clear all tag filters"
            >
              Clear All
            </ButtonBase>
          </div>
        )}

        {showFilters && (
          <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="p-3 bg-surface-card rounded-lg border border-surface-border space-y-3 text-sm mt-2">
            <div className="space-y-1">
              <label className="text-xs font-medium text-rail-fg-muted">
                Date Filter
              </label>
              <div className="flex gap-2 mb-2">
                <ButtonBase
                  onClick={() => setDateMode("range")}
                  className={`text-xs px-2 py-1 rounded ${dateMode === "range" ? "bg-action-tint text-action-tint-fg" : "bg-status-neutral-bg text-status-neutral-fg"}`}
                >
                  Range
                </ButtonBase>
                <ButtonBase
                  onClick={() => setDateMode("after")}
                  className={`text-xs px-2 py-1 rounded ${dateMode === "after" ? "bg-action-tint text-action-tint-fg" : "bg-status-neutral-bg text-status-neutral-fg"}`}
                >
                  After
                </ButtonBase>
                <ButtonBase
                  onClick={() => setDateMode("before")}
                  className={`text-xs px-2 py-1 rounded ${dateMode === "before" ? "bg-action-tint text-action-tint-fg" : "bg-status-neutral-bg text-status-neutral-fg"}`}
                >
                  Before
                </ButtonBase>
              </div>

              <div className="flex gap-2">
                {(dateMode === "range" || dateMode === "after") && (
                  <InputBase
                    data-ui-fill="control"
                    data-ui-border="full"
                    type="date"
                    value={dateRange.start}
                    onChange={(e) =>
                      setDateRange((prev) => ({
                        ...prev,
                        start: e.target.value,
                      }))
                    }
                    className="w-full px-2 py-1 bg-control-bg text-foreground border border-control-border rounded text-xs"
                    placeholder="Start Date"
                  />
                )}
                {(dateMode === "range" || dateMode === "before") && (
                  <InputBase
                    data-ui-fill="control"
                    data-ui-border="full"
                    type="date"
                    value={dateRange.end}
                    onChange={(e) =>
                      setDateRange((prev) => ({ ...prev, end: e.target.value }))
                    }
                    className="w-full px-2 py-1 bg-control-bg text-foreground border border-control-border rounded text-xs"
                    placeholder="End Date"
                  />
                )}
              </div>

              <MonthCalendar
                month={calendarMonth}
                markedDays={calendarMarkedDays}
                selectedDay={selectedCalendarDay}
                onSelectDay={(day) => {
                  setSelectedCalendarDay(day);
                  setDateMode("range");
                  setDateRange({ start: day, end: day });
                }}
                onMonthChange={setCalendarMonth}
              />
            </div>

            <div className="space-y-1">
              <label className="text-xs font-medium text-rail-fg-muted">
                Speakers
              </label>
              <div className="flex flex-wrap gap-1">
                {globalSpeakers.map((speaker) => (
                  <ButtonBase
                    key={speaker.id}
                    onClick={() => {
                      setSelectedSpeakers((prev) =>
                        prev.includes(speaker.id)
                          ? prev.filter((id) => id !== speaker.id)
                          : [...prev, speaker.id],
                      );
                    }}
                    className={`px-2 py-1 rounded-pill text-xs border ${
                      selectedSpeakers.includes(speaker.id)
                        ? "bg-action-tint border-action-border text-action-tint-fg"
                        : "bg-surface-card border-surface-border text-contrast-helper"
                    }`}
                  >
                    {speaker.name}
                  </ButtonBase>
                ))}
                {globalSpeakers.length === 0 && (
                  <span className="text-xs text-contrast-icon-muted">No speakers yet</span>
                )}
              </div>
            </div>

            {hasActiveFilters && (
              <ButtonBase
                onClick={() => {
                  setSearchQuery("");
                  setDateRange({ start: "", end: "" });
                  setSelectedCalendarDay(null);
                  setSelectedSpeakers([]);
                  clearTagFilters();
                }}
                className="text-xs text-danger-text hover:underline flex items-center gap-1"
              >
                <X className="w-3 h-3" /> Clear Filters
              </ButtonBase>
            )}
          </SurfaceBase>
        )}
      </div>

      {/* Recording List */}
      {recordings.length === 0 && (
        <div className="text-center p-4 m-2 text-rail-fg-muted text-sm">
          {list.loadFailed ? (
            <>
              <p role="alert">Could not load recordings.</p>
              <ButtonBase
                onClick={fetchRecordings}
                className="mt-2 rounded px-2 py-1 text-xs font-medium text-action-text hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
              >
                Try again
              </ButtonBase>
            </>
          ) : list.isLoadingQuery ? (
            <p className="flex items-center justify-center gap-2">
              <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
              Loading recordings...
            </p>
          ) : hasActiveFilters ? (
            <>
              <p>No recordings match.</p>
              <p className="mt-1 text-xs">Try other words, or clear the search and filters.</p>
            </>
          ) : (
            <>
              <p>{getEmptyMessage().main}</p>
              <p className="mt-1 text-xs">{getEmptyMessage().sub}</p>
              {/* Only for a library that is really empty, not when a search
                  or filter hides the rows. */}
              {view === "recordings" && (
                <div className="mt-3">
                  <ImportAudioButton />
                </div>
              )}
            </>
          )}
        </div>
      )}
      {recordings.length > 0 && (
        // Escape anywhere in the list, header checkbox included, clears the selection.
        <div onKeyDown={handleListKeyDown}>
          <label className="flex w-fit cursor-pointer items-center gap-2 px-5 pt-2 text-xs text-rail-fg-muted pointer-coarse:min-h-[44px] pointer-coarse:pt-0">
            <InputBase
              type="checkbox"
              aria-controls="recordings-list"
              checked={selection.headerState === "all"}
              ref={(input) => {
                if (input) input.indeterminate = selection.headerState === "some";
              }}
              onChange={selection.toggleAll}
              className="h-4 w-4 cursor-pointer rounded border-control-border text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring"
            />
            Select all
          </label>
          <ul
            id="recordings-list"
            ref={listRef}
            aria-label={`${currentViewLabel} list`}
            aria-busy={list.isLoadingQuery || undefined}
            className="@container p-2 space-y-2"
          >
            {recordings.map((recording) => {
              const isActive = pathname === `/recordings/${recording.id}`;
              const startDate = parseServerTimestamp(recording.created_at);
              // A capture's length is known only once Stop has joined its
              // audio, so until then the row shows when it started and no
              // length, rather than 0s and an end time equal to the start.
              const lengthPending = !recording.duration_seconds &&
                recording.status !== RecordingStatus.PROCESSED;
              const endDate = startDate && !lengthPending
                ? new Date(startDate.getTime() + (recording.duration_seconds || 0) * 1000)
                : null;
              const isRenaming = renamingId === recording.id;
              const isSelected = selection.isSelected(recording.id);
              const isHighlighted = isSelected || (isActive && !selection.isSelecting);
              const isMenuOpen = contextMenu?.buttonFor === recording.id;

              return (
                <li key={recording.id} className="relative group">
                  {isRenaming ? (
                    <SurfaceBase data-ui-fill="card" data-ui-border="full" kind="card" className="p-3 rounded-lg border bg-surface-card border-action">
                      <InputBase
                        autoFocus
                        type="text"
                        value={renameValue}
                        onChange={(e) => setRenameValue(e.target.value)}
                        onBlur={() => handleRenameSubmit(recording.id)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") handleRenameSubmit(recording.id);
                          if (e.key === "Escape") {
                            e.stopPropagation();
                            setRenamingId(null);
                          }
                        }}
                        className="w-full text-sm font-semibold bg-transparent focus:outline-none"
                      />
                    </SurfaceBase>
                  ) : (
                    <>
                      {/* Shown on hover or focus where there is a pointer that
                          hovers, and on every row once anything is selected.
                          On touch it stays hidden, but readable by a screen
                          reader, until a long press starts a selection. */}
                      <InputBase
                        type="checkbox"
                        aria-label={`Select ${recording.name}`}
                        checked={isSelected}
                        // Handled on click, which knows whether Shift is held.
                        onChange={() => undefined}
                        onClick={(e) => {
                          if (e.shiftKey) {
                            selection.extendTo(recording.id);
                          } else {
                            selection.toggle(recording.id);
                          }
                        }}
                        className={`absolute left-3 top-3.5 z-10 h-4 w-4 cursor-pointer rounded border-control-border text-action-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus-ring ${
                          selection.isSelecting
                            ? ""
                            : "pointer-coarse:sr-only pointer-fine:opacity-0 pointer-fine:group-hover:opacity-100 pointer-fine:group-focus-within:opacity-100"
                        }`}
                      />
                      <Link
                        data-ui-fill={isHighlighted ? "action-tint" : "card"}
                        data-ui-hover={isHighlighted ? undefined : "action-tint"}
                        data-ui-border="full"
                        data-ui="surface"
                        data-ui-surface="card"
                        data-recording-link={recording.id}
                        href={`/recordings/${recording.id}`}
                        id={
                          recording.name === "Welcome to Nojoin"
                            ? "demo-recording-card"
                            : undefined
                        }
                        aria-current={isActive ? "page" : undefined}
                        onClick={(e) => handleRecordingClick(e, recording.id)}
                        onKeyDown={(e) => handleRecordingKeyDown(e, recording.id)}
                        onMouseDown={(e) => {
                          // Shift-click extends the selection, not a text selection.
                          if (e.shiftKey) e.preventDefault();
                        }}
                        onContextMenu={(e) => handleContextMenu(e, recording)}
                        {...longPress.bind(recording.id)}
                        // The right padding keeps the text clear of the actions button.
                        className={`block py-3 pr-9 pointer-coarse:pr-[52px] rounded-lg border transition-all [-webkit-touch-callout:none] pointer-coarse:select-none ${
                          selection.isSelecting || reserveTagGutter
                            ? "pl-9"
                            : "pl-3 pointer-fine:pl-9"
                        } ${
                          isHighlighted
                            ? isSelected
                              ? "bg-action-tint border-action-border"
                              : "bg-action-tint border-action"
                            : "bg-surface-card border-surface-border hover:border-action-border hover:bg-action-tint"
                        }`}
                      >
                        <div className="flex justify-between items-start mb-1">
                          <h3
                            className={`min-w-0 text-sm font-semibold line-clamp-2 ${isActive && !selection.isSelecting ? "text-action-text" : "text-foreground"}`}
                            title="Double-click to rename"
                            onDoubleClick={(e) => {
                              if (selection.isSelecting) return;
                              e.preventDefault();
                              e.stopPropagation();
                              handleRenameStart(recording.id, recording.name);
                            }}
                          >
                            {recording.name}
                          </h3>
                          <StatusIcon recording={recording} />
                        </div>

                        <div className="flex items-center overflow-hidden text-xs text-contrast-helper gap-2">
                          <span className="whitespace-nowrap">
                            {formatDate(recording.created_at, userTimeZone)}
                          </span>
                          {/* The time range yields first when the rail narrows; date and duration stay. */}
                          <span className="hidden whitespace-nowrap text-contrast-icon-muted @min-[19rem]:inline">
                            |
                          </span>
                          <span className="hidden whitespace-nowrap @min-[19rem]:inline">
                            {!startDate
                              ? "Time unavailable"
                              : endDate
                                ? `${formatTime(startDate, userTimeZone)} - ${formatTime(endDate, userTimeZone)}`
                                : `Started ${formatTime(startDate, userTimeZone)}`}
                          </span>
                          {!lengthPending && (
                            <>
                              <span className="whitespace-nowrap text-contrast-icon-muted">
                                |
                              </span>
                              <span className="whitespace-nowrap">
                                {formatDurationString(recording.duration_seconds)}
                              </span>
                            </>
                          )}
                        </div>
                        {/* In the left gutter, below where the selection
                            checkbox appears, so the two never overlap. */}
                        <RecordingTagDots
                          tags={recording.tags}
                          className="absolute left-3 top-8.5 w-4"
                        />
                        <RecordingAttentionBadge recordingId={recording.id} className="mt-2" />
                        {/* The open recording lists its own unsent notes. */}
                        {!isActive && unsentRecordings.includes(recording.id) && (
                          <Badge
                            tone="warning"
                            size="sm"
                            icon={<NotebookPen aria-hidden="true" />}
                            className="mt-2"
                          >
                            Unsent notes
                          </Badge>
                        )}
                      </Link>
                      {/* Beside the link, not in it, so a press here never
                          opens, selects or long-presses the row. Shown on hover
                          or focus where the pointer hovers, and always on touch. */}
                      <IconButton
                        size="sm"
                        icon={<MoreHorizontal aria-hidden="true" />}
                        aria-label={rowMenuLabel(recording)}
                        title="Recording actions"
                        aria-haspopup="menu"
                        aria-expanded={isMenuOpen}
                        onClick={(e) => toggleRowMenu(e.currentTarget, recording)}
                        onKeyDown={(e) => {
                          if (e.key === "ArrowDown" && !isMenuOpen) {
                            e.preventDefault();
                            toggleRowMenu(e.currentTarget, recording);
                          }
                        }}
                        className={cn(
                          "absolute right-1 top-1/2 z-10 h-7 w-7 -translate-y-1/2",
                          TOUCH_TARGET_SQUARE,
                          isMenuOpen
                            ? "opacity-100"
                            : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 pointer-coarse:opacity-100",
                        )}
                      />
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          <RecordingListFooter
            rowCount={recordings.length}
            hasMore={list.hasMore}
            isLoadingMore={list.isLoadingMore}
            loadMoreFailed={list.loadMoreFailed}
            lastAdded={list.lastAdded}
            onLoadMore={list.loadMore}
            scrollRootRef={scrollRef}
            listRef={listRef}
            endMessage={
              recordings.length > RECORDINGS_PAGE_SIZE
                ? hasActiveFilters
                  ? "No more matches."
                  : "No older recordings."
                : null
            }
          />
        </div>
      )}

      <BatchActionBar
        view={view}
        count={selection.count}
        onClear={clearSelectionFromBar}
        onAction={(action) => runListAction(action, selection.selectedIds)}
        onTag={(tagName) => tagRecordings(selection.selectedIds, tagName)}
      />

      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          onClose={closeContextMenu}
          items={contextMenu.items}
          label={contextMenu.label}
          anchor={contextMenu.anchor}
          returnFocusTo={contextMenu.returnFocusTo}
        />
      )}

      {infoModalRecording && (
        <RecordingInfoModal
          isOpen={showInfoModal}
          onClose={() => {
            setShowInfoModal(false);
            setInfoModalRecording(null);
          }}
          recording={infoModalRecording}
        />
      )}

      <ConfirmationModal
        isOpen={confirmModal.isOpen}
        onClose={() => setConfirmModal({ ...confirmModal, isOpen: false })}
        onConfirm={confirmModal.onConfirm}
        title={confirmModal.title}
        message={confirmModal.message}
        confirmText={confirmModal.confirmText}
        cancelText={confirmModal.cancelText}
        isDangerous={confirmModal.isDangerous}
        returnFocus={confirmFocusRef}
      />

      {reprocessRecordingId && (
        <ReprocessDialog
          recordingId={reprocessRecordingId}
          currentMaxSpeakers={
            recordings.find((r) => r.id === reprocessRecordingId)?.max_speakers ??
            null
          }
          currentTranscriptionLanguage={
            recordings.find((r) => r.id === reprocessRecordingId)
              ?.transcription_language ?? null
          }
          // The list does not say whether a failed recording kept a
          // transcript, so the dialog reads the recording to find out.
          failed={
            recordings.find((r) => r.id === reprocessRecordingId)?.status ===
            RecordingStatus.ERROR
          }
          isOpen={!!reprocessRecordingId}
          onClose={() => setReprocessRecordingId(null)}
        />
      )}
      </div>
      )}

      {/* Resize Handle - Hidden on Mobile, inert while collapsed */}
      {!isCollapsed && (
        <div
          className="absolute right-0 top-0 bottom-0 z-[var(--z-sticky)] hidden w-1 cursor-col-resize touch-none hover:bg-action active:bg-action-hover lg:block"
          onPointerDown={handleResizePointerDown}
        >
          <div className="absolute top-1/2 right-0 -translate-y-1/2 w-1 h-12 bg-rail-border hover:bg-action transition-colors" />
        </div>
      )}
    </aside>
  );
}
