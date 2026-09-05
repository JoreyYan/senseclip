import { useState, useCallback, useRef, useEffect } from "react";
import { Link } from "react-router-dom";
import { ChatSidebar } from "@/components/chat/ChatSidebar";
import { ChatInput } from "@/components/chat/ChatInput";
import { MessageBubble } from "@/components/chat/MessageBubble";
import { WelcomeScreen } from "@/components/chat/WelcomeScreen";
import { sendChatMessage, fetchPersonas, type ChatMode, type PersonaInfo } from "@/services/chatApi";
import { useAuth } from "@/hooks/useAuth";
import { supabase } from "@/lib/supabase";
import type { ChatMessage, Conversation, Citation } from "@/types/chat";
import { ScrollArea } from "@/components/ui/scroll-area";
import { X } from "lucide-react";

export default function ChatPage() {
  const { user, canChat, credits, refreshCredits, guestChatsUsed, guestChatLimit, incrementGuestChat } = useAuth();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvId, setActiveConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [loadingStatus, setLoadingStatus] = useState("");
  const [personaList, setPersonaList] = useState<PersonaInfo[]>([]);
  useEffect(() => { fetchPersonas().then(setPersonaList).catch(() => {}); }, []);
  const [panelCitations, setPanelCitations] = useState<Citation[] | null>(null);
  const [highlightedCitationIdx, setHighlightedCitationIdx] = useState<number | null>(null);
  const [chatMode, setChatMode] = useState<ChatMode>("standard");
  const abortRef = useRef<AbortController | null>(null);

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
  }, []);
  const bottomRef = useRef<HTMLDivElement>(null);

  const isGuest = !user;

  // Load conversations from Supabase on mount (logged-in users only)
  useEffect(() => {
    if (!user) return;
    supabase
      .from("conversations")
      .select("id, title, updated_at, created_at")
      .order("updated_at", { ascending: false })
      .then(({ data }) => {
        if (data) setConversations(data);
      });
  }, [user]);

  // Load messages when switching conversations
  useEffect(() => {
    if (!activeConvId || !user) return;
    supabase
      .from("chat_messages")
      .select("id, role, content, citations, created_at")
      .eq("conversation_id", activeConvId)
      .order("created_at", { ascending: true })
      .then(({ data }) => {
        if (data) setMessages(data as ChatMessage[]);
      });
  }, [activeConvId, user]);

  // Auto-scroll on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleSend = useCallback(
    async (content: string, attachments?: import("@/services/chatApi").ChatAttachment[]) => {
      if (!canChat) return;

      const attachNote = attachments && attachments.length > 0
        ? "\n\n📎 " + attachments.map((a) => a.name).join("、")
        : "";
      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content: content + attachNote,
        created_at: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, userMsg]);
      setIsLoading(true);

      let convId = activeConvId;

      // Create conversation if first message (DB errors don't block chat)
      if (!convId) {
        const title = content.slice(0, 20);
        if (user) {
          try {
            const { data } = await supabase
              .from("conversations")
              .insert({ user_id: user.id, title })
              .select("id, title, updated_at, created_at")
              .single();
            if (data) {
              convId = data.id;
              setConversations((prev) => [data, ...prev]);
              setActiveConvId(data.id);
            }
          } catch {
            // DB save failed (e.g. unverified user) — continue without persistence
          }
        }
        if (!convId) {
          // Fallback: in-memory conversation (guest or DB failure)
          const newConv: Conversation = {
            id: crypto.randomUUID(),
            title,
            updated_at: new Date().toISOString(),
            created_at: new Date().toISOString(),
          };
          convId = newConv.id;
          setConversations((prev) => [newConv, ...prev]);
          setActiveConvId(newConv.id);
        }
      }

      // Save user message to DB (non-blocking)
      if (user && convId) {
        supabase.from("chat_messages").insert({
          conversation_id: convId,
          role: "user",
          content,
        }).then(() => {});
      }

      // Guest counter
      if (isGuest) incrementGuestChat();

      try {
        const apiMessages = [...messages, userMsg].map((m) => ({
          role: m.role,
          content: m.content,
        }));
        abortRef.current = new AbortController();
        const response = await sendChatMessage(
          apiMessages, null, chatMode, attachments, abortRef.current.signal,
          (s) => setLoadingStatus(s));

        const assistantMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: response.reply,
          citations: response.sources,
          webCitations: response.webSources,
          created_at: new Date().toISOString(),
          logId: response.logId,
          avatar: chatMode !== "standard" ? personaList.find((p) => p.key === chatMode)?.avatar : undefined,
        };
        setMessages((prev) => [...prev, assistantMsg]);

        // Refresh credits after successful chat
        if (user) refreshCredits();

        // Save assistant message to DB (non-blocking)
        if (user && convId) {
          supabase.from("chat_messages").insert({
            conversation_id: convId,
            role: "assistant",
            content: response.reply,
            citations: response.sources || null,
          }).then(() => {
            supabase
              .from("conversations")
              .update({ updated_at: new Date().toISOString() })
              .eq("id", convId)
              .then(() => {});
          });
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          // 用户主动中止:静默收尾,不加错误消息
          setIsLoading(false);
          return;
        }
        const isGuestLimit = err instanceof Error && err.message === "GUEST_LIMIT";
        const isNoCredits = err instanceof Error && err.message === "INSUFFICIENT_CREDITS";
        const isSystemError = !isGuestLimit && !isNoCredits;
        const errorMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          content: isGuestLimit
            ? "您今日的免费对话次数已用完。登录后可无限对话，支持邮箱验证码登录，无需密码。"
            : isNoCredits
            ? "您的提问积分已用完。请前往 [积分中心](/billing) 购买更多积分。"
            : "抱歉，服务暂时不可用，请稍后重试。",
          created_at: new Date().toISOString(),
          errorReport: isSystemError
            ? {
                question: content,
                error: err instanceof Error ? err.message : String(err),
                mode: chatMode,
              }
            : undefined,
        };
        setMessages((prev) => [...prev, errorMsg]);
      } finally {
        setIsLoading(false);
        setLoadingStatus("");
      }
    },
    [messages, activeConvId, user, canChat, isGuest, incrementGuestChat, refreshCredits, chatMode]
  );

  const handleNewConversation = useCallback(() => {
    setActiveConvId(null);
    setMessages([]);
  }, []);

  const handleSelectConversation = useCallback((id: string) => {
    setActiveConvId(id);
    // Messages will be loaded by the useEffect above
    if (!user) setMessages([]);
  }, [user]);

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (activeConvId === id) {
        setActiveConvId(null);
        setMessages([]);
      }
      if (user) {
        await supabase.from("conversations").delete().eq("id", id);
      }
    },
    [activeConvId, user]
  );

  const handleCitationClick = useCallback((citations: Citation[], index: number) => {
    setPanelCitations(citations);
    setHighlightedCitationIdx(index);
  }, []);

  const isEmpty = messages.length === 0;
  const chatDisabled = !canChat;
  const guestLimitMessage = isGuest && !canChat
    ? `已达免费体验上限（${guestChatsUsed}/${guestChatLimit}），注册以继续对话`
    : undefined;

  return (
    <div className="flex flex-1 min-h-0">
      <ChatSidebar
        conversations={conversations}
        activeId={activeConvId}
        onSelect={handleSelectConversation}
        onNew={handleNewConversation}
        onDelete={handleDeleteConversation}
        isCollapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
        isGuest={isGuest}
      />

      <div className="flex-1 flex flex-col min-w-0">
        {isEmpty ? (
          <WelcomeScreen onSelectQuestion={handleSend} />
        ) : (
          <ScrollArea className="flex-1">
            <div className="max-w-5xl mx-auto py-6 px-4 space-y-6">
              {messages.map((msg) => (
                <MessageBubble key={msg.id} message={msg} onCitationClick={handleCitationClick} />
              ))}
              {isLoading && (
                <div className="flex gap-3 items-center">
                  <div className="w-7 h-7 rounded-lg bg-accent flex items-center justify-center shrink-0">
                    <div className="flex gap-1">
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:0ms]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:150ms]" />
                      <span className="w-1.5 h-1.5 rounded-full bg-muted-foreground animate-bounce [animation-delay:300ms]" />
                    </div>
                  </div>
                  {loadingStatus && (
                    <span className="text-xs text-muted-foreground animate-pulse">
                      {loadingStatus}
                    </span>
                  )}
                </div>
              )}
              <div ref={bottomRef} />
            </div>
          </ScrollArea>
        )}

        <ChatInput
          onSend={handleSend}
          onStop={handleStop}
          isLoading={isLoading}
          disabled={chatDisabled}
          disabledMessage={guestLimitMessage}
          mode={chatMode}
          onModeChange={setChatMode}
        />
      </div>

      {/* Right panel: citations */}
      {panelCitations && panelCitations.length > 0 && (
        <div className="w-[380px] shrink-0 border-l border-border bg-card flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-border">
            <span className="text-sm font-semibold">参考原子</span>
            <button onClick={() => setPanelCitations(null)} className="text-muted-foreground hover:text-foreground">
              <X className="h-4 w-4" />
            </button>
          </div>
          <ScrollArea className="flex-1">
            <div className="p-3 space-y-3">
              {panelCitations.map((c) => (
                <div
                  key={c.index}
                  className={`p-3 rounded-lg border text-xs transition-colors ${
                    highlightedCitationIdx === c.index
                      ? "border-primary/50 bg-primary/5"
                      : "border-border/30 bg-secondary/30"
                  }`}
                  onClick={() => setHighlightedCitationIdx(c.index)}
                >
                  <div className="flex items-center justify-between text-muted-foreground mb-1.5">
                    <span className="font-medium text-primary">[{c.index}]</span>
                    <div className="flex items-center gap-2">
                      {c.similarity && <span>相关度: {Math.round(c.similarity * 100)}%</span>}
                      {!c.url && <span>{Math.round((c.start_ms || 0) / 1000)}s</span>}
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground mb-1.5 flex items-center justify-between">
                    <span className="truncate">来源: {c.url ? "X 推文" : c.video_id}</span>
                    {c.url ? (
                      <a
                        href={c.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-primary hover:text-primary/80 underline shrink-0 ml-2"
                      >
                        查看原推
                      </a>
                    ) : (
                      <Link
                        to={`/analysis?projectId=${c.video_id}&atomId=${c.atom_id}`}
                        className="text-primary hover:text-primary/80 underline shrink-0 ml-2"
                      >
                        查看原子
                      </Link>
                    )}
                  </div>
                  <p className="text-foreground/80 leading-relaxed text-sm">{c.text}</p>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      )}
    </div>
  );
}
