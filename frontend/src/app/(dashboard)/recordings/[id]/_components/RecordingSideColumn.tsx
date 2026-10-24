"use client";

import type { ReactNode } from "react";
import { ChevronDown, ChevronUp, MessageSquare, Users } from "lucide-react";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";

import ChatPanel from "@/components/ChatPanel";
import IconButton from "@/components/ui/IconButton";
import { useNavigationStore } from "@/lib/store";

interface RecordingSideColumnProps {
  /** The speaker panel, as the page builds it. */
  speakers: ReactNode;
  /** Chat's percentage of the column's height. */
  chatPanelHeight: number;
  onChatPanelHeightChange: (height: number) => void;
  onNotesUpdate: () => void;
  onResizeDragging: (dragging: boolean) => void;
}

/**
 * The desktop side column beside a processed meeting: the speaker panel on
 * top and Chat below, each collapsible, with a handle between them while both
 * are open.
 */
export default function RecordingSideColumn({
  speakers,
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
          aria-controls="speaker-panel"
        >
          <Users className="h-3.5 w-3.5" />
          Speakers
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
        <div className="min-h-0 flex-1">{speakers}</div>
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
        {speakers}
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

/** The rail left when both panels of the side column are collapsed. */
export function RecordingSideRail() {
  const setSpeakerPanelCollapsed = useNavigationStore(
    (state) => state.setSpeakerPanelCollapsed,
  );
  const setChatPanelCollapsed = useNavigationStore(
    (state) => state.setChatPanelCollapsed,
  );
  return (
    <div className="flex h-full w-12 shrink-0 flex-col items-center gap-1 border-l border-surface-border bg-surface-inset py-2">
      <IconButton
        size="sm"
        icon={<Users />}
        aria-label="Show speakers panel"
        title="Show speakers panel"
        onClick={() => setSpeakerPanelCollapsed(false)}
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
