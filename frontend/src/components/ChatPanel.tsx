"use client";

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
import { ChatMessage, Tag } from "@/types";
import Link from "next/link";
import MarkdownBubble from "./MarkdownBubble";
import { useNotificationStore } from "@/lib/notificationStore";
import ConfirmationModal from "./ConfirmationModal";
import MultiSelect, { Option } from "@/components/ui/MultiSelect";

export default function ChatPanel() {
  const params = useParams();
  const recordingId = params?.id ? parseInt(params.id as string) : null;
  const [provider, setProvider] = useState<string>("AI");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [abortController, setAbortController] =
    useState<AbortController | null>(null);
  const [isClearModalOpen, setIsClearModalOpen] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { addNotification } = useNotificationStore();

  const [isAdmin, setIsAdmin] = useState(false);
  const [isLLMConfigured, setIsLLMConfigured] = useState(true);

  // Cross-Meeting Context State
  const [availableTags, setAvailableTags] = useState<Tag[]>([]);
  const [selectedTagIds, setSelectedTagIds] = useState<(number | string)[]>([]);
  const [showTagFilter, setShowTagFilter] = useState(false);

  // Load provider name and check config
  useEffect(() => {
    Promise.all([getSettings(), getUserMe()])
      .then(([settings, user]) => {
        setIsAdmin(user.is_superuser);

        const provider = settings.llm_provider;
        let key = "";
        let model = "";
        if (provider === "gemini") {
          key = settings.gemini_api_key || "";
          model = settings.gemini_model || "";
        } else if (provider === "openai") {
          key = settings.openai_api_key || "";
          model = settings.openai_model || "";
        } else if (provider === "anthropic") {
          key = settings.anthropic_api_key || "";
          model = settings.anthropic_model || "";
        } else if (provider === "ollama") {
          // Ollama doesn't need a key, just a model and URL (which has a default)
          model = settings.ollama_model || "";
        }

        const configured = !!(
          provider &&
          (key || provider === "ollama") &&
          model
        );
        setIsLLMConfigured(configured);

        if (provider) {
          setProvider(provider.charAt(0).toUpperCase() + provider.slice(1));
        }
      })
      .catch(console.error);

    // Fetch tags
    getTags().then(setAvailableTags).catch(console.error);
  }, []);

  // Tag Options
  const tagOptions: Option[] = useMemo(() => {
    return availableTags.map((tag) => ({
      value: tag.id,
      label: tag.name,
      color: tag.color,
    }));
  }, [availableTags]);

  // Load chat history
  useEffect(() => {
    if (recordingId) {
      setMessages([]); // Clear previous
      getChatHistory(recordingId).then(setMessages).catch(console.error);
    } else {
      setMessages([]);
    }
  }, [recordingId]);

  // Auto-scroll
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isStreaming]);

  const handleSend = async () => {
    if (!inputValue.trim() || !recordingId || isStreaming) return;

    const userMsg: ChatMessage = {
      id: Date.now(), // Temp ID
      recording_id: recordingId,
      user_id: 0, // Placeholder
      role: "user",
      content: inputValue,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMsg]);
    setInputValue("");
    setIsStreaming(true);

    // Create placeholder for assistant message
    const assistantMsgId = Date.now() + 1;
    const assistantMsg: ChatMessage = {
      id: assistantMsgId, // Temp ID
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
        setIsStreaming(false);
        setAbortController(null);
      },
      (error) => {
        console.error("Chat error:", error);
        addNotification({ type: "error", message: error });
        setIsStreaming(false);
        setAbortController(null);
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
    );
    setAbortController(controller);
  };

  const handleStop = () => {
    if (abortController) {
      abortController.abort();
      setIsStreaming(false);
      setAbortController(null);
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
    } catch (e) {
      console.error(e);
      addNotification({ type: "error", message: "Failed to clear chat" });
    } finally {
      setIsClearModalOpen(false);
    }
  };

  return (
    <aside
      id="meeting-chat"
      className="flex-1 min-w-0 border-l border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 h-full flex flex-col shadow-xl z-10"
    >
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center bg-white dark:bg-gray-900">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-orange-500" />
          Chat ({provider})
        </h2>
        <div className="flex items-center gap-1">
          {recordingId && (
            <button
              id="chat-context-toggle"
              onClick={() => setShowTagFilter(!showTagFilter)}
              className={`flex items-center gap-1 text-xs px-2 py-1.5 rounded-lg border transition-colors ${
                showTagFilter || selectedTagIds.length > 0
                  ? "bg-orange-50 dark:bg-orange-900/20 text-orange-600 dark:text-orange-400 border-orange-200 dark:border-orange-800"
                  : "text-gray-600 dark:text-gray-400 border-transparent hover:bg-gray-100 dark:hover:bg-gray-800"
              }`}
            >
              <TagIcon className="w-3.5 h-3.5" />
              <span className="hidden sm:inline">Context</span>
              {selectedTagIds.length > 0 && (
                <span className="ml-0.5 bg-orange-200 dark:bg-orange-800 text-orange-800 dark:text-orange-100 text-[10px] px-1.5 rounded-full">
                  {selectedTagIds.length}
                </span>
              )}
              {showTagFilter ? (
                <ChevronUp className="w-3 h-3" />
              ) : (
                <ChevronDown className="w-3 h-3" />
              )}
            </button>
          )}
          {recordingId && messages.length > 0 && (
            <button
              onClick={handleClear}
              className="text-gray-400 hover:text-red-500 transition-colors p-1"
              title="Clear Chat"
            >
              <Trash2 className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>

      {showTagFilter && recordingId && (
        <div className="px-4 py-3 bg-white dark:bg-gray-900 border-b border-gray-200 dark:border-gray-800 animate-in slide-in-from-top-2">
          <label className="text-xs text-gray-500 dark:text-gray-400 mb-1.5 block">
            Include context from related meetings with these tags:
          </label>
          <MultiSelect
            options={tagOptions}
            selected={selectedTagIds}
            onChange={setSelectedTagIds}
            placeholder="Select tags..."
          />
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {!recordingId ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400 opacity-60">
            <MessageSquare className="w-12 h-12 mb-4" />
            <p className="text-sm">Select a meeting to start chatting.</p>
          </div>
        ) : messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400 opacity-60">
            <Info className="w-12 h-12 mb-4" />
            <p className="text-sm">
              Ask questions about the transcript, generate summaries, or draft
              emails.
            </p>
          </div>
        ) : (
          messages.map((msg, index) => (
            <div
              key={index}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm ${
                  msg.role === "user"
                    ? "bg-orange-600 text-white rounded-tr-none"
                    : "bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 rounded-tl-none"
                }`}
              >
                {msg.role === "user" ? (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                ) : (
                  <div className="w-full">
                    {msg.content ? (
                      <>
                        <MarkdownBubble content={msg.content} />
                        {isStreaming && index === messages.length - 1 && (
                          <span className="inline-block w-2 h-4 ml-1 bg-gray-400 animate-pulse align-middle"></span>
                        )}
                      </>
                    ) : (
                      <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 py-1">
                        <Loader2 className="w-4 h-4 animate-spin" />
                        <span className="text-xs">Thinking...</span>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900 relative">
        {!isLLMConfigured && (
          <div className="absolute inset-0 bg-white/90 dark:bg-gray-900/90 backdrop-blur-[1px] z-20 flex items-center justify-center p-4 text-center">
            <div className="text-sm text-gray-600 dark:text-gray-300 font-medium">
              {isAdmin ? (
                <p>
                  Chat is disabled. Please{" "}
                  <Link
                    href="/settings"
                    className="text-orange-500 hover:underline"
                  >
                    configure an API key and select a model
                  </Link>
                  .
                </p>
              ) : (
                <p>Chat is disabled. Please contact your administrator.</p>
              )}
            </div>
          </div>
        )}
        <div className="relative">
          <textarea
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
            className="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-xl pl-4 pr-12 py-4 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent resize-none h-14 max-h-32 flex items-center"
            disabled={!recordingId}
          />
          <div className="absolute right-2 top-1/2 -translate-y-1/2">
            {isStreaming ? (
              <button
                onClick={handleStop}
                className="p-2 bg-red-500 text-white rounded-lg hover:bg-red-600 transition-colors shadow-sm"
                title="Stop Generation"
              >
                <StopCircle className="w-4 h-4" />
              </button>
            ) : (
              <button
                onClick={handleSend}
                disabled={!recordingId || !inputValue.trim()}
                className="p-2 bg-orange-600 text-white rounded-lg hover:bg-orange-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
              >
                <Send className="w-4 h-4" />
              </button>
            )}
          </div>
        </div>
        {recordingId && (
          <div className="text-[10px] text-gray-400 mt-2 text-center">
            AI can make mistakes. Check important info.
          </div>
        )}
      </div>

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
