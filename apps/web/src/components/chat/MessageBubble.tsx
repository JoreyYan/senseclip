import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { User, Bot, Copy, Check, ExternalLink, ThumbsUp, ThumbsDown } from "lucide-react";
import type { ChatMessage, Citation, WebCitation } from "@/types/chat";
import { useMemo, useState } from "react";
import { sendFeedback, reportError } from "@/services/chatApi";

interface MessageBubbleProps {
  message: ChatMessage;
  onCitationClick?: (citations: Citation[], index: number) => void;
}

export function MessageBubble({ message, onCitationClick }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const [copied, setCopied] = useState(false);
  const [rated, setRated] = useState<0 | 1 | -1>(0);
  const [reported, setReported] = useState(false);

  const handleRate = (rating: 1 | -1) => {
    if (rated !== 0 || !message.logId) return;
    setRated(rating);
    sendFeedback(message.logId, rating).catch(() => {});
  };

  const handleReport = () => {
    if (reported || !message.errorReport) return;
    setReported(true);
    reportError(message.errorReport).catch(() => {});
  };

  const handleCitationClick = (index: number) => {
    if (onCitationClick && message.citations) {
      onCitationClick(message.citations, index);
    }
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch { /* clipboard unavailable */ }
  };

  // Build citation lookup maps
  const citationMap = useMemo(() => {
    const map = new Map<number, Citation>();
    message.citations?.forEach((c) => map.set(c.index, c));
    return map;
  }, [message.citations]);

  const webMap = useMemo(() => {
    const map = new Map<number, WebCitation>();
    message.webCitations?.forEach((w) => map.set(w.index, w));
    return map;
  }, [message.webCitations]);

  return (
    <div className={`flex gap-3 ${isUser ? "flex-row-reverse" : ""}`}>
      {/* Avatar */}
      {isUser ? (
        <div className="shrink-0 w-7 h-7 rounded-lg flex items-center justify-center mt-1 bg-primary/20">
          <User className="h-4 w-4 text-primary" />
        </div>
      ) : (
        <img src={message.avatar || "/avatar.png"} alt="AI" className="shrink-0 w-7 h-7 rounded-full object-cover mt-1" />
      )}

      {/* Content */}
      <div className={`max-w-[90%] ${isUser ? "text-right" : ""}`}>
        <div
          className={`inline-block rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? "bg-primary text-primary-foreground rounded-tr-md"
              : "bg-card border border-border/30 text-foreground rounded-tl-md"
          }`}
        >
          {isUser ? (
            <p>{message.content}</p>
          ) : (
            <div className="markdown-body prose prose-sm max-w-none">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  // Render inline [N]/[WN] citation markers as clickable elements —
                  // cover every text container (Lu-mode answers use lists/bold/quotes heavily)
                  p: ({ children, ...props }) => (
                    <p {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</p>
                  ),
                  li: ({ children, ...props }) => (
                    <li {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</li>
                  ),
                  strong: ({ children, ...props }) => (
                    <strong {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</strong>
                  ),
                  em: ({ children, ...props }) => (
                    <em {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</em>
                  ),
                  blockquote: ({ children, ...props }) => (
                    <blockquote {...props}>{children}</blockquote>
                  ),
                  h2: ({ children, ...props }) => (
                    <h2 {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</h2>
                  ),
                  h3: ({ children, ...props }) => (
                    <h3 {...props}>{renderWithCitations(children, citationMap, webMap, handleCitationClick)}</h3>
                  ),
                }}
              >
                {message.content}
              </ReactMarkdown>
            </div>
          )}
        </div>

        {/* 回答异常:一键上报(自动附带诊断信息) */}
        {!isUser && message.errorReport && (
          <div className="mt-2">
            <button
              onClick={handleReport}
              disabled={reported}
              className={`text-xs px-3 py-1.5 rounded-lg border transition-colors ${
                reported
                  ? "border-green-500/40 bg-green-500/10 text-green-600 cursor-default"
                  : "border-amber-500/40 bg-amber-500/10 text-amber-600 hover:bg-amber-500/20"
              }`}
            >
              {reported ? "✓ 已通知开发人员，我们会尽快排查" : "⚠️ 回答异常？点此一键通知开发人员"}
            </button>
          </div>
        )}

        {/* 操作行:一键复制 + 引用总览 */}
        {!isUser && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <button
              onClick={handleCopy}
              title="复制回答"
              className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded transition-colors bg-accent/60 text-muted-foreground hover:text-foreground hover:bg-accent"
            >
              {copied ? <Check className="h-3 w-3 text-green-600" /> : <Copy className="h-3 w-3" />}
              {copied ? "已复制" : "复制"}
            </button>
            {message.logId && (
              <>
                <button
                  onClick={() => handleRate(1)}
                  title="回答有帮助"
                  disabled={rated !== 0}
                  className={`flex items-center text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                    rated === 1
                      ? "bg-green-500/20 text-green-600"
                      : "bg-accent/60 text-muted-foreground hover:text-foreground hover:bg-accent"
                  } ${rated === -1 ? "opacity-30" : ""}`}
                >
                  <ThumbsUp className="h-3 w-3" />
                </button>
                <button
                  onClick={() => handleRate(-1)}
                  title="回答不满意"
                  disabled={rated !== 0}
                  className={`flex items-center text-[10px] px-1.5 py-0.5 rounded transition-colors ${
                    rated === -1
                      ? "bg-red-500/20 text-red-600"
                      : "bg-accent/60 text-muted-foreground hover:text-foreground hover:bg-accent"
                  } ${rated === 1 ? "opacity-30" : ""}`}
                >
                  <ThumbsDown className="h-3 w-3" />
                </button>
              </>
            )}
            {message.citations && message.citations.map((c) => (
              <button
                key={c.index}
                onClick={() => handleCitationClick(c.index)}
                className="text-[10px] px-1.5 py-0.5 rounded transition-colors bg-primary/10 text-primary hover:bg-primary/20"
              >
                [{c.index}]
              </button>
            ))}
            {message.webCitations && message.webCitations.map((w) => (
              <a
                key={`w${w.index}`}
                href={w.url}
                target="_blank"
                rel="noopener noreferrer"
                title={w.title || w.url}
                className="inline-flex items-center gap-0.5 text-[10px] px-1.5 py-0.5 rounded transition-colors bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20 no-underline"
              >
                <ExternalLink className="h-2.5 w-2.5" />
                {w.title ? (w.title.length > 18 ? w.title.slice(0, 18) + "…" : w.title) : `W${w.index}`}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Process React children to replace [N] (知识库) and [WN] (网络来源) patterns
 * with clickable citation elements inline.
 */
function renderWithCitations(
  children: React.ReactNode,
  citationMap: Map<number, Citation>,
  webMap: Map<number, WebCitation>,
  onCitationClick: (index: number) => void
): React.ReactNode {
  if (!children) return children;

  if (typeof children === "string") {
    const parts = children.split(/(\[W?\d+\])/g);
    if (parts.length === 1) return children;

    return parts.map((part, i) => {
      const webMatch = part.match(/^\[W(\d+)\]$/);
      if (webMatch) {
        const num = parseInt(webMatch[1]);
        const w = webMap.get(num);
        if (w && w.url) {
          return (
            <a
              key={i}
              href={w.url}
              target="_blank"
              rel="noopener noreferrer"
              title={w.title || w.url}
              className="inline-flex items-center justify-center text-[10px] px-1 py-0 rounded mx-0.5 align-super transition-colors bg-emerald-500/10 text-emerald-600 hover:bg-emerald-500/20 no-underline"
            >
              [W{num}]
            </a>
          );
        }
      }
      const match = part.match(/^\[(\d+)\]$/);
      if (match) {
        const num = parseInt(match[1]);
        if (citationMap.has(num)) {
          return (
            <button
              key={i}
              onClick={() => onCitationClick(num)}
              className="inline-flex items-center justify-center text-[10px] px-1 py-0 rounded mx-0.5 align-super transition-colors bg-primary/10 text-primary hover:bg-primary/20"
            >
              [{num}]
            </button>
          );
        }
      }
      return part;
    });
  }

  if (Array.isArray(children)) {
    return children.map((child) =>
      typeof child === "string"
        ? renderWithCitations(child, citationMap, webMap, onCitationClick)
        : child
    );
  }

  return children;
}
