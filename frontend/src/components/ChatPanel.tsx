"use client";

import { ButtonBase, TextareaBase } from '@/components/ui/nativeBases';
import { SurfaceBase } from '@/components/ui/SurfaceBase';
import { useState, useEffect, useRef, useMemo } from "react";
import {
  MessageSquare,
  Send,
  Trash2,
  StopCircle,
  Info,
  Loader2,
  Tag as TagIcon,
  ChevronDown,
  ChevronsRight,
  ChevronUp,
} from "lucide-react";
import { useParams } from "next/navigation";
import {
  getSettings,
  getChatHistory,
  clearChatHistory,
  streamChatMessage,
  getUserMe,
  getTags,
} from "@/lib/api";
import { getCliOAuthStatus } from "@/lib/api/cliOauth";
import { AiAvailability, resolveAiAvailability } from "@/lib/aiAvailability";
import { isAdministrator } from "@/lib/roles";
import { ChatContextMetadata, ChatMessage, CliOAuthStatus, RecordingId, Tag } from "@/types";
import Link from "next/link";
import MarkdownBubble from "./MarkdownBubble";
import { useNotificationStore } from "@/lib/notificationStore";
import { useStickToBottom } from "@/lib/useStickToBottom";
import ConfirmationModal from "./ConfirmationModal";
import MultiSelect, { Option } from "@/components/ui/MultiSelect";
import IconButton from "@/components/ui/IconButton";

type DisplayChatMessage = ChatMessage & { context?: ChatContextMetadata; contextFooterComplete?: boolean };

export default function ChatPanel({
  onNotesUpdate,
  onCollapse,
  inFlight = false,
}: {
  onNotesUpdate?: () => void;
  /** Desktop side-column collapse; the mobile full-screen chat omits it. */
  onCollapse?: () => void;
  inFlight?: boolean;
}) {
  const params = useParams();
  const recordingId: RecordingId | null =
    typeof params?.id === "string"
      ? params.id
      : Array.isArray(params?.id)
        ? params.id[0] ?? null
        : null;
  const [messages, setMessages] = useState<DisplayChatMessage[]>([]);
  const [loadedHistoryRecordingId, setLoadedHistoryRecordingId] = useState<RecordingId | null>(null);
  const historyReady = loadedHistoryRecordingId === recordingId;
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const abortController = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const optimisticId = useRef(-1);
  const [isClearModalOpen, setIsClearModalOpen] = useState(false);
  const messagesRef = useRef<HTMLDivElement>(null);
  const [messagesContent, setMessagesContent] = useState<HTMLDivElement | null>(null);
  const followNewMessages = useStickToBottom(messagesRef, messagesContent);
  const { addNotification } = useNotificationStore();

  const [isAdmin, setIsAdmin] = useState(false);
  // Optimistic until the settings load: a brief flash of a working composer
  // beats blocking a chat that is in fact configured.
  const [availability, setAvailability] = useState<AiAvailability>({
    available: true,
  });

  // Cross-Meeting Context State
  const [availableTags, setAvailableTags] = useState<Tag[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<(number | string)[]>([]);
  const [showTagFilter, setShowTagFilter] = useState(false);

  // Resolve whether AI can answer at all, for either routing mode.
  useEffect(() => {
    let cancelled = false;

    const load = async () => {
      const [settings, user] = await Promise.all([getSettings(), getUserMe()]);
      if (cancelled) return;
      // Any administrator can choose the model in Settings > AI providers.
      setIsAdmin(isAdministrator(user));

      // Only users routed through their own subscription need the connection
      // status, so everyone else is spared the extra request.
      let cliStatus: CliOAuthStatus | null = null;
      if (settings.usage_model === "cli_oauth") {
        try {
          cliStatus = await getCliOAuthStatus();
        } catch (error) {
          // Fail open: a status endpoint blip must not block a chat that would
          // have worked.
          console.error(error);
          return;
        }
        if (cancelled) return;
      }

      setAvailability(resolveAiAvailability(settings, cliStatus));
    };

    load().catch(console.error);

    getTags().then(setAvailableTags).catch(console.error);

    return () => {
      cancelled = true;
    };
  }, []);

  const tagOptions: Option[] = useMemo(() => {
    return availableTags.map((tag) => ({
      value: tag.id,
      label: tag.name,
      color: tag.color,
    }));
  }, [availableTags]);

  // Load chat history
  useEffect(() => {
    let current = true;
    if (recordingId) {
      setMessages([]);
      getChatHistory(recordingId).then((history) => {
        if (current) setMessages(history);
      }).catch(console.error).finally(() => {
        if (current) setLoadedHistoryRecordingId(recordingId);
      });
    } else {
      setMessages([]);
    }
    return () => {
      current = false;
      generation.current += 1;
      abortController.current?.abort();
      abortController.current = null;
    };
  }, [recordingId]);

  const handleSend = async () => {
    if (!inputValue.trim() || !recordingId || isStreaming || !historyReady) return;
    const requestGeneration = ++generation.current;
    const isCurrent = () => generation.current === requestGeneration;

    const userMsg: ChatMessage = {
      // Local-only id for optimistic rendering; the server assigns the real
      // one when the message is persisted.
      id: optimisticId.current--,
      recording_id: recordingId,
      user_id: 0,
      role: "user",
      content: inputValue,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    // Sending brings a reader who had scrolled up back to the conversation.
    followNewMessages();
    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsStreaming(true);

    // Create placeholder for assistant message
    const assistantMsgId = optimisticId.current--;
    const assistantMsg: ChatMessage = {
      id: assistantMsgId,
      recording_id: recordingId,
      user_id: 0,
      role: "assistant",
      content: "",
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, assistantMsg]);

    const controller = streamChatMessage(
      recordingId,
      userMsg.content,
      (token) => {
        if (!isCurrent()) return;
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id === assistantMsgId) {
              return { ...msg, content: msg.content + token };
            }
            return msg;
          }),
        );
      },
      () => {
        if (!isCurrent()) return;
        setMessages((previous) => previous.map((message) => message.id === assistantMsgId
          ? { ...message, contextFooterComplete: true } : message));
        setIsStreaming(false);
        abortController.current = null;
      },
      (error) => {
        if (!isCurrent()) return;
        generation.current += 1;
        console.error("Chat error:", error);
        addNotification({ type: "error", message: error });
        setIsStreaming(false);
        abortController.current = null;
        setMessages((prev) =>
          prev.map((msg) => {
            if (msg.id === assistantMsgId) {
              return {
                ...msg,
                content: msg.content + `\n\n*[Error: ${error}]*`,
              };
            }
            return msg;
          }),
        );
      },
      selectedTagIds.length > 0 ? (selectedTagIds as number[]) : undefined,
      () => { if (isCurrent()) onNotesUpdate?.(); },
      (context) => {
        if (!isCurrent()) return;
        setMessages((previous) => previous.map((message) =>
          message.id === assistantMsgId ? { ...message, context } : message));
      },
    );
    abortController.current = controller;
  };

  const handleStop = () => {
    if (abortController.current) {
      generation.current += 1;
      abortController.current.abort();
      setIsStreaming(false);
      abortController.current = null;
      addNotification({ type: "info", message: "Generation stopped" });
    }
  };

  const handleClear = () => {
    if (!recordingId) return;
    setIsClearModalOpen(true);
  };

  const confirmClear = async () => {
    if (!recordingId) return;

    try {
      await clearChatHistory(recordingId);
      setMessages([]);
      addNotification({ type: "success", message: "Chat history cleared" });

        } catch (e: unknown) {
      console.error(e);
      addNotification({ type: "error", message: "Failed to clear chat" });
    } finally {
      setIsClearModalOpen(false);
    }
  };

  return (
    <aside
      data-ui-fill="inset"
      id="meeting-chat"
      data-ui="surface"
      data-ui-surface="pane"
      className="@container flex-1 min-w-0 bg-surface-inset h-full flex flex-col"
    >
      <SurfaceBase data-ui-fill="card" kind="pane" className="py-1 pl-3 pr-2 @min-[20rem]:pl-4 border-b border-surface-border flex justify-between items-center bg-surface-card">
        <h2 className="text-sm font-semibold text-foreground flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-action-text" />
          Chat
        </h2>
        <div className="flex items-center gap-1">
          {recordingId && (
            <ButtonBase
              id="chat-context-toggle"
              onClick={() => setShowTagFilter(!showTagFilter)}
              className={`flex items-center gap-1 text-xs px-2 py-1.5 rounded-lg border transition-colors ${
                showTagFilter || selectedTagIds.length > 0
                  ? "bg-action-tint text-action-text border-action-border"
                  : "text-contrast-helper border-transparent hover:bg-surface-inset"
              }`}
            >
              <TagIcon className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Context</span>
              {selectedTagIds.length > 0 && (
                <span className="ml-0.5 bg-action-tint text-action-text text-[10px] px-1.5 rounded-pill">
                  {selectedTagIds.length}
                </span>
              )}
              {showTagFilter ? (
                <ChevronUp className="w-3 h-3" />
              ) : (
                <ChevronDown className="w-3 h-3" />
              )}
            </ButtonBase>
          )}
          {recordingId && messages.length > 0 && (
            <ButtonBase
              onClick={handleClear}
              className="text-contrast-icon-muted hover:text-status-danger-fg transition-colors p-1"
              title="Clear Chat"
            >
              <Trash2 className="w-4 h-4" />
            </ButtonBase>
          )}
          {onCollapse && (
            <IconButton
              size="sm"
              icon={<ChevronsRight />}
              aria-label="Collapse chat panel"
              title="Collapse chat panel"
              onClick={onCollapse}
            />
          )}
        </div>
      </SurfaceBase>

      {showTagFilter && recordingId && (
        <SurfaceBase data-ui-fill="card" kind="pane" className="px-4 py-3 bg-surface-card border-b border-surface-border animate-in slide-in-from-top-2">
          <label className="text-xs text-contrast-helper mb-1.5 block">
            Include context from related meetings with these tags:
          </label>
          <MultiSelect
            options={tagOptions}
            selected={selectedTagIds}
            onChange={setSelectedTagIds}
            placeholder="Select tags..."
          />
        </SurfaceBase>
      )}

      {inFlight && <p className="border-b border-surface-border px-4 py-2 text-xs text-contrast-helper">
        Answers use the transcript so far; wording and speakers may still change.
      </p>}

      <div
        ref={messagesRef}
        data-testid="chat-messages"
        className="flex-1 overflow-y-auto p-3 @min-[20rem]:p-4"
      >
        {!recordingId ? (
          // No opacity on these empty states: the helper token is pitched to
          // clear 4.5:1 on the card, and dimming the wrapper took the text
          // to 2.79:1. The icon carries the quieter look instead.
          <div className="h-full flex flex-col items-center justify-center text-center text-contrast-helper">
            <MessageSquare className="w-12 h-12 mb-4 text-contrast-icon-muted" aria-hidden="true" />
            <p className="text-sm">Select a meeting to start chatting.</p>
          </div>
        ) : !historyReady ? (
          <p className="text-sm text-contrast-helper" role="status">Loading chat history...</p>
        ) : messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-contrast-helper">
            <Info className="w-12 h-12 mb-4 text-contrast-icon-muted" aria-hidden="true" />
            <p className="text-sm">
              Ask questions about the transcript, generate summaries, or draft
              emails. To regenerate the notes, make a request via the meeting chat below with the requested changes.
            </p>
          </div>
        ) : (
          <div
            ref={setMessagesContent}
            className="space-y-3 @min-[20rem]:space-y-4"
          >
            {messages.map((msg, index) => (
              <div
                key={index}
                className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
              >
                <SurfaceBase data-ui-fill={(msg.role === "user") ? "action" : "card"} data-ui-border={(msg.role === "user") ? undefined : "full"} kind="inset"
                  className={`max-w-[85%] rounded-surface-panel px-3 py-2 @min-[20rem]:px-4 @min-[20rem]:py-3 text-sm shadow-card ${
                    msg.role === "user"
                      ? "bg-action text-action-on rounded-tr-none"
                      : "bg-surface-card border border-surface-border text-foreground rounded-tl-none"
                  }`}
                >
                  {msg.role === "user" ? (
                    <p className="whitespace-pre-wrap">{msg.content}</p>
                  ) : (
                    <div className="w-full">
                      {msg.context?.transcript_trimmed && !msg.contextFooterComplete && <p className="mb-2 text-xs text-contrast-helper">
                        Only the most recent part of the transcript was included for this answer.
                      </p>}
                      {msg.content ? (
                        <>
                          <MarkdownBubble content={msg.content} />
                          {isStreaming && index === messages.length - 1 && (
                            <span className="inline-block w-2 h-4 ml-1 bg-surface-card animate-pulse align-middle"></span>
                          )}
                        </>
                      ) : (
                        <div className="flex items-center gap-2 text-contrast-helper py-1">
                          <Loader2 className="w-4 h-4 animate-spin" />
                          <span className="text-xs">Thinking...</span>
                        </div>
                      )}
                    </div>
                  )}
                </SurfaceBase>
              </div>
            ))}
          </div>
        )}
      </div>

      <SurfaceBase data-ui-fill="card" kind="pane" className="p-3 @min-[20rem]:p-4 border-t border-surface-border bg-surface-card relative">
        {!availability.available && (
          <SurfaceBase data-ui-fill="card" kind="pane" className="absolute inset-0 bg-surface-card z-20 flex items-center justify-center p-4 text-center">
            <div className="text-sm text-contrast-helper font-medium">
              {availability.reason === "subscription_disconnected" ? (
                // AI routing is per-user, so this link is right for admins and
                // non-admins alike.
                <p>
                  Chat is disabled. Your AI subscription is not connected.{" "}
                  <Link
                    href="/settings"
                    className="text-action-text hover:underline"
                  >
                    Reconnect it in Settings
                  </Link>
                  .
                </p>
              ) : isAdmin ? (
                <p>
                  Chat is disabled. Please{" "}
                  <Link
                    href="/settings"
                    className="text-action-text hover:underline"
                  >
                    configure an API key and select a model
                  </Link>
                  .
                </p>
              ) : (
                <p>Chat is disabled. Please contact your administrator.</p>
              )}
            </div>
          </SurfaceBase>
        )}
        <div className="relative">
          <TextareaBase
            data-ui-fill="inset"
            data-ui-border="full"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleSend();
              }
            }}
            placeholder={
              recordingId ? "Ask a question..." : "Select a meeting first..."
            }
            className="w-full bg-surface-inset border border-control-border rounded-xl pl-4 pr-12 py-3 h-12 @min-[20rem]:py-4 @min-[20rem]:h-14 text-sm focus:outline-none focus:ring-2 focus:ring-action focus:border-transparent resize-none max-h-32 flex items-center"
            disabled={!recordingId}
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            {isStreaming ? (
              <ButtonBase
                data-ui-fill="status-danger"
                data-ui-hover="status-danger" chrome="action"
                onClick={handleStop}
                className="p-2 bg-status-danger-bg text-foreground rounded-lg hover:bg-status-danger-bg transition-colors shadow-card"
                title="Stop Generation"
              >
                <StopCircle className="w-4 h-4" />
              </ButtonBase>
            ) : (
              <ButtonBase
                data-ui-fill="action"
                data-ui-hover="action-hover" chrome="action"
                onClick={handleSend}
                disabled={!recordingId || !inputValue.trim() || !historyReady}
                className="p-2 bg-action text-action-on rounded-lg hover:bg-action-hover disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-card"
              >
                <Send className="w-4 h-4" />
              </ButtonBase>
            )}
          </div>
        </div>
        {recordingId && (
          <div className="text-[10px] text-contrast-icon-muted mt-2 text-center">
            AI can make mistakes. Check important info.
          </div>
        )}
      </SurfaceBase>

      <ConfirmationModal
        isOpen={isClearModalOpen}
        onClose={() => setIsClearModalOpen(false)}
        onConfirm={confirmClear}
        title="Clear Chat History"
        message="Are you sure you want to clear the chat history? This action cannot be undone."
        confirmText="Clear History"
        isDangerous={true}
      />
    </aside>
  );
}
