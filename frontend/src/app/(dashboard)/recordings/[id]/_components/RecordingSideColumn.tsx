"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";
import {
  ChevronDown,
  ChevronsRight,
  ChevronUp,
  MessageSquare,
  StickyNote,
  Users,
} from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import IconButton from "@/components/ui/IconButton";
import { useNavigationStore, type SideTab } from "@/lib/store";

const SIDE_TABS: { tab: SideTab; label: string }[] = [
  { tab: "speakers", label: "Speakers" },
  { tab: "my-notes", label: "My notes" },
];

interface RecordingSideColumnProps {
  /** The speaker panel, as the page builds it. */
  speakers: ReactNode;
  /** The My notes list, as the page builds it. */
  myNotes: ReactNode;
  /** Chat's percentage of the column's height. */
  chatPanelHeight: number;
  onChatPanelHeightChange: (height: number) => void;
  onNotesUpdate: () => void;
  onResizeDragging: (dragging: boolean) => void;
}

/**
 * The top of the side column: Speakers and My notes as tabs (the WAI-ARIA
 * tabs pattern), and the button that collapses it. Both panels stay mounted,
 * so switching tabs keeps a rename or an edit where it was.
 */
function SideTabs({ speakers, myNotes }: { speakers: ReactNode; myNotes: ReactNode }) {
  const sideTab = useNavigationStore((state) => state.sideTab);
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const tablistRef = useRef<HTMLDivElement>(null);
  const panels: Record<SideTab, ReactNode> = { speakers, "my-notes": myNotes };

  // Arrow keys move between the tabs and open them, wrapping; Home and End
  // jump to the first and last.
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = SIDE_TABS.findIndex(({ tab }) => tab === sideTab);
    const targets: Record<string, number> = {
      ArrowLeft: index - 1,
      ArrowRight: index + 1,
      Home: 0,
      End: SIDE_TABS.length - 1,
    };
    if (!(event.key in targets)) return;
    event.preventDefault();
    const { tab } = SIDE_TABS[(targets[event.key] + SIDE_TABS.length) % SIDE_TABS.length];
    setSideTab(tab);
    tablistRef.current?.querySelector<HTMLElement>(`#side-tab-${tab}`)?.focus();
  };

  return (
    <div id="side-tabs" className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-between border-b border-surface-border bg-surface-card pr-1">
        <div ref={tablistRef} role="tablist" aria-label="Side panels" className="flex min-w-0 overflow-hidden">
          {SIDE_TABS.map(({ tab, label }) => (
            <button
              key={tab}
              type="button"
              role="tab"
              id={`side-tab-${tab}`}
              aria-selected={sideTab === tab}
              aria-controls={`side-panel-${tab}`}
              tabIndex={sideTab === tab ? 0 : -1}
              onClick={() => setSideTab(tab)}
              onKeyDown={onKeyDown}
              className={`min-w-0 whitespace-nowrap border-b-2 px-2.5 py-2.5 text-sm font-semibold transition-colors outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-action ${
                sideTab === tab
                  ? "border-action text-action-text"
                  : "border-transparent text-contrast-helper hover:text-foreground"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <IconButton
          size="sm"
          icon={<ChevronsRight />}
          aria-label="Collapse speakers and notes panel"
          title="Collapse speakers and notes panel"
          onClick={() => setSpeakerPanelCollapsed(true)}
        />
      </div>
      {SIDE_TABS.map(({ tab }) => (
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
 * The desktop side column beside a processed meeting: Speakers and My notes
 * as tabs on top, and Chat below, each part collapsible, with a handle
 * between them while both are open.
 */
export default function RecordingSideColumn({
  speakers,
  myNotes,
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
        <button
          onClick={() => setSpeakerPanelCollapsed(false)}
          className="flex h-9 w-full shrink-0 items-center gap-2 border-b border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
          aria-expanded={false}
          aria-controls="side-tabs"
        >
          <Users className="h-3.5 w-3.5" />
          Speakers and My notes
          <ChevronDown className="ml-auto h-3.5 w-3.5" />
        </button>
        <div className="min-h-0 flex-1">
          <ChatPanel
            onNotesUpdate={onNotesUpdate}
            onCollapse={() => setChatPanelCollapsed(true)}
          />
        </div>
      </div>
    );
  }

  if (isChatPanelCollapsed) {
    return (
      <div className="flex h-full flex-col">
        <div className="min-h-0 flex-1">
          <SideTabs speakers={speakers} myNotes={myNotes} />
        </div>
        <button
          onClick={() => setChatPanelCollapsed(false)}
          className="flex h-9 w-full shrink-0 items-center gap-2 border-t border-surface-border bg-surface-card px-3 text-xs font-medium text-contrast-helper transition-colors hover:bg-surface-inset hover:text-foreground"
          aria-expanded={false}
          aria-controls="meeting-chat"
        >
          <MessageSquare className="h-3.5 w-3.5" />
          Chat
          <ChevronUp className="ml-auto h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  return (
    <PanelGroup
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
        <SideTabs speakers={speakers} myNotes={myNotes} />
      </Panel>

      <PanelResizeHandle
        className="bg-surface-inset border-t border-control-border h-2 hover:bg-action transition-colors flex items-center justify-center group"
        onDragging={onResizeDragging}
      >
        <div className="w-8 h-1 bg-surface-card rounded-full group-hover:bg-surface-card transition-colors" />
      </PanelResizeHandle>

      <Panel
        id="side-chat"
        order={2}
        defaultSize={chatPanelHeight}
        minSize={18}
      >
        <ChatPanel
          onNotesUpdate={onNotesUpdate}
          onCollapse={() => setChatPanelCollapsed(true)}
        />
      </Panel>
    </PanelGroup>
  );
}

/** The rail left when both parts of the side column are collapsed. */
export function RecordingSideRail() {
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const setChatPanelCollapsed = useNavigationStore(
    (state) => state.setChatPanelCollapsed,
  );
  const setSideTab = useNavigationStore((state) => state.setSideTab);
  const show = (tab: SideTab) => {
    setSideTab(tab);
    setSpeakerPanelCollapsed(false);
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
        icon={<MessageSquare />}
        aria-label="Show meeting chat panel"
        title="Show meeting chat panel"
        onClick={() => setChatPanelCollapsed(false)}
      />
    </div>
  );
}
