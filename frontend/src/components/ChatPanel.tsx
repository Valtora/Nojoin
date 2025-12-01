'use client';

import { useState, useEffect, useRef } from 'react';
import { MessageSquare, Send, Trash2, StopCircle, Info } from 'lucide-react';
import { useParams } from 'next/navigation';
import { getSettings, getChatHistory, clearChatHistory, streamChatMessage, ChatMessage } from '@/lib/api';
import MarkdownBubble from './MarkdownBubble';
import { useNotificationStore } from '@/lib/notificationStore';

export default function ChatPanel() {
  const params = useParams();
  const recordingId = params?.id ? parseInt(params.id as string) : null;
  const [provider, setProvider] = useState<string>('AI');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [abortController, setAbortController] = useState<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { addNotification } = useNotificationStore();

  // Load provider name
  useEffect(() => {
    getSettings().then(settings => {
      if (settings.llm_provider) {
        const p = settings.llm_provider;
        setProvider(p.charAt(0).toUpperCase() + p.slice(1));
      }
    }).catch(console.error);
  }, []);

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
        role: 'user',
        content: inputValue,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
    };

    setMessages(prev => [...prev, userMsg]);
    setInputValue("");
    setIsStreaming(true);

    // Create placeholder for assistant message
    const assistantMsgId = Date.now() + 1;
    const assistantMsg: ChatMessage = {
        id: assistantMsgId, // Temp ID
        recording_id: recordingId,
        user_id: 0,
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString()
    };
    
    setMessages(prev => [...prev, assistantMsg]);

    const controller = streamChatMessage(
        recordingId,
        userMsg.content,
        (token) => {
            setMessages(prev => prev.map(msg => {
                if (msg.id === assistantMsgId) {
                     return { ...msg, content: msg.content + token };
                }
                return msg;
            }));
        },
        () => {
            setIsStreaming(false);
            setAbortController(null);
        },
        (error) => {
            console.error("Chat error:", error);
            addNotification({ type: 'error', message: error });
            setIsStreaming(false);
            setAbortController(null);
            setMessages(prev => prev.map(msg => {
                if (msg.id === assistantMsgId) {
                    return { ...msg, content: msg.content + `\n\n*[Error: ${error}]*` };
                }
                return msg;
            }));
        }
    );
    setAbortController(controller);
  };

  const handleStop = () => {
      if (abortController) {
          abortController.abort();
          setIsStreaming(false);
          setAbortController(null);
          addNotification({ type: 'info', message: 'Generation stopped' });
      }
  };

  const handleClear = async () => {
      if (!recordingId) return;
      if (!confirm("Are you sure you want to clear the chat history?")) return;
      
      try {
          await clearChatHistory(recordingId);
          setMessages([]);
          addNotification({ type: 'success', message: 'Chat history cleared' });
      } catch (e) {
          console.error(e);
          addNotification({ type: 'error', message: 'Failed to clear chat' });
      }
  };

  return (
    <aside className="w-96 flex-shrink-0 border-l border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 h-screen sticky top-0 flex flex-col shadow-xl z-10">
      <div className="p-4 border-b border-gray-200 dark:border-gray-800 flex justify-between items-center bg-white dark:bg-gray-900">
        <h2 className="text-sm font-semibold text-gray-900 dark:text-gray-100 flex items-center gap-2">
          <MessageSquare className="w-4 h-4 text-orange-500" />
          Chat ({provider})
        </h2>
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
      
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {!recordingId ? (
            <div className="h-full flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400 opacity-60">
                <MessageSquare className="w-12 h-12 mb-4" />
                <p className="text-sm">Select a meeting to start chatting.</p>
            </div>
        ) : messages.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center text-gray-500 dark:text-gray-400 opacity-60">
                <Info className="w-12 h-12 mb-4" />
                <p className="text-sm">Ask questions about the transcript, generate summaries, or draft emails.</p>
            </div>
        ) : (
            messages.map((msg, index) => (
                <div 
                    key={index} 
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                    <div 
                        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm shadow-sm ${
                            msg.role === 'user' 
                                ? 'bg-orange-500 text-white rounded-tr-none' 
                                : 'bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 text-gray-800 dark:text-gray-200 rounded-tl-none'
                        }`}
                    >
                        {msg.role === 'user' ? (
                            <p className="whitespace-pre-wrap">{msg.content}</p>
                        ) : (
                            <div className="w-full">
                                <MarkdownBubble content={msg.content} />
                                {isStreaming && index === messages.length - 1 && (
                                    <span className="inline-block w-2 h-4 ml-1 bg-gray-400 animate-pulse align-middle"></span>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            ))
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="p-4 border-t border-gray-200 dark:border-gray-800 bg-white dark:bg-gray-900">
        <div className="relative">
            <textarea 
                value={inputValue}
                onChange={(e) => setInputValue(e.target.value)}
                onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                        e.preventDefault();
                        handleSend();
                    }
                }}
                placeholder={recordingId ? "Ask a question..." : "Select a meeting first..."}
                className="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-xl pl-4 pr-12 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-orange-500 focus:border-transparent resize-none h-14 max-h-32"
                disabled={!recordingId}
            />
            <div className="absolute right-2 top-2">
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
                        className="p-2 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
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
    </aside>
  );
}
