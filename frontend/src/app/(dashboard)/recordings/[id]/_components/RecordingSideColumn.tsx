"use client";

import { ButtonBase } from "@/components/ui/nativeBases";
import { SurfaceBase } from "@/components/ui/SurfaceBase";
import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { flushSync } from "react-dom";
import {
  ChevronDown,
  ChevronsRight,
  ChevronUp,
  ListTodo,
  Images,
  MessageSquare,
  StickyNote,
  Users,
  Sparkles,
  type LucideIcon,
} from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import IconButton from "@/components/ui/IconButton";
import { cn } from "@/lib/cn";
import { useNavigationStore, type SideTab } from "@/lib/store";

const SIDE_TABS: { tab: SideTab; label: string; icon: LucideIcon }[] = [
  { tab: "speakers", label: "Speakers", icon: Users },
  { tab: "my-notes", label: "My notes", icon: StickyNote },
  { tab: "captures", label: "Captures", icon: Images },
  { tab: "meeting-edge", label: "Meeting Edge", icon: Sparkles },
  { tab: "tasks", label: "Tasks", icon: ListTodo },
];

interface RecordingSideColumnProps {
  allowChat?: boolean;
  inFlight?: boolean;
  /** The speaker panel, as the page builds it. */
  speakers: ReactNode;
  /** The My notes list, as the page builds it. */
  myNotes: ReactNode;
  /** The Captures list, as the page builds it. */
  captures: ReactNode;
  /** The Meeting Edge panel, left out where Meeting Edge is not shown. */
  meetingEdge?: ReactNode;
  /** Linked tasks, left out when the meeting has none. */
  tasks?: ReactNode;
  /** Chat's percentage of the column's height. */
  chatPanelHeight: number;
  onChatPanelHeightChange: (height: number) => void;
  onNotesUpdate: () => void;
  onResizeDragging: (dragging: boolean) => void;
}

/**
 * The top of the side column: Speakers, My notes, Captures, Meeting Edge and Tasks as tabs (the
 * WAI-ARIA tabs pattern), and the button that collapses it. Every panel stays
 * mounted, so switching tabs keeps a rename or an edit where it was.
 *
 * A narrow column keeps what fits. Under 30rem, where the labels and
 * the button no longer fit, the open tab shows its icon and label and the
 * others only their icons; under 12rem all are icons. A hidden label stays
 * for screen readers, and every tab has its label as a tooltip.
 */
function SideTabs({
  speakers,
  myNotes,
  captures,
  meetingEdge,
  tasks,
}: {
  speakers: ReactNode;
  myNotes: ReactNode;
  captures: ReactNode;
  meetingEdge?: ReactNode;
  tasks?: ReactNode;
}) {
  const remembered = useNavigationStore((state) => state.sideTab);
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const tablistRef = useRef<HTMLDivElement>(null);
  const panels: Record<SideTab, ReactNode> = { speakers, "my-notes": myNotes, captures, "meeting-edge": meetingEdge, tasks };
  const tabs = SIDE_TABS.filter(({ tab }) => panels[tab] !== undefined);
  const sideTab = tabs.some(({ tab }) => tab === remembered) ? remembered : "speakers";

  useEffect(() => {
    tablistRef.current?.querySelector<HTMLElement>(`#side-tab-${sideTab}`)
      ?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
  }, [sideTab]);

  // Arrow keys move between the tabs and open them, wrapping; Home and End
  // jump to the first and last.
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.findIndex(({ tab }) => tab === sideTab);
    const targets: Record<string, number> = {
      ArrowLeft: index - 1,
      ArrowRight: index + 1,
      Home: 0,
      End: tabs.length - 1,
    };
    if (!(event.key in targets)) return;
    event.preventDefault();
    const { tab } = tabs[(targets[event.key] + tabs.length) % tabs.length];
    setSideTab(tab);
    tablistRef.current?.querySelector<HTMLElement>(`#side-tab-${tab}`)?.focus();
  };

  return (
    <div id="side-tabs" className="@container flex h-full flex-col">
      <SurfaceBase data-ui-fill="card" kind="pane" className="flex shrink-0 items-center justify-between border-b border-surface-border bg-surface-card pr-1">
        <div
          ref={tablistRef}
          role="tablist"
          aria-label="Side panels"
          className="flex min-w-0 overflow-x-auto"
        >
          {tabs.map(({ tab, label, icon: Icon }) => {
            const open = sideTab === tab;
            return (
              <ButtonBase
                key={tab}
                type="button"
                role="tab"
                id={`side-tab-${tab}`}
                data-ui-side-tab={tab}
                aria-selected={open}
                aria-controls={`side-panel-${tab}`}
                tabIndex={open ? 0 : -1}
                title={label}
                onClick={() => setSideTab(tab)}
                onKeyDown={onKeyDown}
                className={cn(
                  "flex min-w-0 items-center gap-1 border-b-2 px-2.5 py-2.5 text-sm font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-action @max-[12rem]:px-1",
                  open
                    ? "border-action text-action-text @max-[30rem]:px-2"
                    : "shrink-0 border-transparent text-contrast-helper hover:text-foreground @max-[30rem]:px-1.5",
                )}
              >
                <Icon aria-hidden="true" className="hidden h-4 w-4 shrink-0 @max-[30rem]:block" />
                <span
                  className={cn(
                    "min-w-0 truncate",
                    open ? "@max-[12rem]:sr-only" : "@max-[30rem]:sr-only",
                  )}
                >
                  {label}
                </span>
              </ButtonBase>
            );
          })}
        </div>
        <IconButton
          size="sm"
          icon={<ChevronsRight />}
          aria-label="Collapse side panels"
          title="Collapse side panels"
          onClick={() => setSpeakerPanelCollapsed(true)}
          className="@max-[30rem]:w-8"
        />
      </SurfaceBase>
      {tabs.map(({ tab }) => (
        <div
          key={tab}
          role="tabpanel"
          id={`side-panel-${tab}`}
          aria-labelledby={`side-tab-${tab}`}
          hidden={sideTab !== tab}
          className="min-h-0 flex-1"
        >
          {panels[tab]}
        </div>
      ))}
    </div>
  );
}

/**
 * The recording's desktop side column: Speakers, My notes, Captures and
 * Meeting Edge and linked Tasks as tabs on top, and Chat below, each part collapsible, with a handle
 * between them while both are open.
 */
export default function RecordingSideColumn({
  allowChat = true,
  inFlight = false,
  speakers,
  myNotes,
  captures,
  meetingEdge,
  tasks,
  chatPanelHeight,
  onChatPanelHeightChange,
  onNotesUpdate,
  onResizeDragging,
}: RecordingSideColumnProps) {
  const isSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.isSpeakerPanelCollapsed,
  );
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const isChatPanelCollapsed = useNavigationStore(
    (state) => state.isChatPanelCollapsed,
  );
  const setChatPanelCollapsed = useNavigationStore(
    (state) => state.setChatPanelCollapsed,
  );

  if (isSpeakerPanelCollapsed) {
    return (
      <div className="flex h-full flex-col">
        <ButtonBase
          data-ui-fill="card"
          onClick={() => setSpeakerPanelCollapsed(false)}
          className="flex h-9 w-full shrink-0 items-center gap-2 border-b border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
          aria-expanded={false}
          aria-controls="side-tabs"
        >
          <Users className="h-3.5 w-3.5" />
          <span className="truncate">
            {tasks !== undefined
              ? `Speakers, My notes, Captures${meetingEdge !== undefined ? ", Meeting Edge" : ""} and Tasks`
              : meetingEdge === undefined ? "Speakers, My notes and Captures" : "Speakers, My notes, Captures and Meeting Edge"}
          </span>
          <ChevronDown className="ml-auto h-3.5 w-3.5" />
        </ButtonBase>
        {allowChat && <div className="min-h-0 flex-1">
          <ChatPanel
            inFlight={inFlight}
            onNotesUpdate={onNotesUpdate}
            onCollapse={() => setChatPanelCollapsed(true)}
          />
        </div>}
      </div>
    );
  }

  if (!allowChat) {
    return <SideTabs speakers={speakers} myNotes={myNotes} captures={captures} meetingEdge={meetingEdge} tasks={tasks} />;
  }

  if (isChatPanelCollapsed) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1">
          <SideTabs speakers={speakers} myNotes={myNotes} captures={captures} meetingEdge={meetingEdge} tasks={tasks} />
        </div>
        <ButtonBase
          data-ui-fill="card"
          onClick={() => setChatPanelCollapsed(false)}
          className="flex h-9 w-full shrink-0 items-center gap-2 border-t border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
          aria-expanded={false}
          aria-controls="meeting-chat"
        >
          <MessageSquare className="h-3.5 w-3.5" />
          Chat
          <ChevronUp className="ml-auto h-3.5 w-3.5" />
        </ButtonBase>
      </div>
    );
  }

  return (
    <PanelGroup
      data-ui-resize-group="vertical"
      direction="vertical"
      onLayout={(sizes) => {
        if (sizes.length === 2) {
          onChatPanelHeightChange(sizes[1]);
        }
      }}
    >
      <Panel
        id="side-speakers"
        order={1}
        defaultSize={100 - chatPanelHeight}
        minSize={20}
      >
        <SideTabs speakers={speakers} myNotes={myNotes} captures={captures} meetingEdge={meetingEdge} tasks={tasks} />
      </Panel>

      <PanelResizeHandle
        data-ui-resize-handle="vertical"
        className="bg-surface-inset border-t border-control-border h-2 hover:bg-action transition-colors flex items-center justify-center group"
        onDragging={onResizeDragging}
      >
        <div data-ui-resize-grip="" className="w-8 h-1 bg-surface-card rounded-full group-hover:bg-surface-card transition-colors" />
      </PanelResizeHandle>

      <Panel
        id="side-chat"
        order={2}
        defaultSize={chatPanelHeight}
        minSize={18}
      >
        <ChatPanel
          inFlight={inFlight}
          onNotesUpdate={onNotesUpdate}
          onCollapse={() => setChatPanelCollapsed(true)}
        />
      </Panel>
    </PanelGroup>
  );
}

/** The rail left when both parts of the side column are collapsed. */
export function RecordingSideRail({
  allowChat = true,
  showMeetingEdge,
  showTasks = false,
}: {
  allowChat?: boolean;
  showMeetingEdge: boolean;
  showTasks?: boolean;
}) {
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const setChatPanelCollapsed = useNavigationStore(
    (state) => state.setChatPanelCollapsed,
  );
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const show = (tab: SideTab) => {
    // Expansion removes this focused opener. Commit the tabs before handing
    // focus to the selected one, including when the page replaces the rail.
    flushSync(() => {
      setSideTab(tab);
      setSpeakerPanelCollapsed(false);
    });
    document.getElementById(`side-tab-${tab}`)?.focus();
  };
  return (
    <div className="flex h-full w-12 shrink-0 flex-col items-center gap-1 border-l border-surface-border bg-surface-inset py-2">
      <IconButton
        size="sm"
        icon={<Users />}
        aria-label="Show speakers panel"
        title="Show speakers panel"
        onClick={() => show("speakers")}
      />
      <IconButton
        size="sm"
        icon={<StickyNote />}
        aria-label="Show my notes"
        title="Show my notes"
        onClick={() => show("my-notes")}
      />
      <IconButton
        size="sm"
        icon={<Images />}
        aria-label="Show captures"
        title="Show captures"
        onClick={() => show("captures")}
      />
      {showMeetingEdge && <IconButton
        size="sm"
        icon={<Sparkles />}
        aria-label="Show Meeting Edge"
        title="Show Meeting Edge"
        onClick={() => show("meeting-edge")}
      />}
      {showTasks && <IconButton
        size="sm"
        icon={<ListTodo />}
        aria-label="Show tasks"
        title="Show tasks"
        onClick={() => show("tasks")}
      />}
      {allowChat && <IconButton
        size="sm"
        icon={<MessageSquare />}
        aria-label="Show meeting chat panel"
        title="Show meeting chat panel"
        onClick={() => setChatPanelCollapsed(false)}
      />}
    </div>
  );
}
