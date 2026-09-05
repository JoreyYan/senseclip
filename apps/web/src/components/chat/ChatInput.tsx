import { useState, useRef, useEffect, useCallback } from "react";
import { Send, Loader2, Sparkles, Paperclip, X, FileText, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { fetchPersonas, type PersonaInfo, type ChatMode, type ChatAttachment } from "@/services/chatApi";

// 人格按钮配色(按注册顺序轮换;tailwind 需完整字面量类名)
const PILL_STYLES = [
  { active: "bg-amber-600 text-white", border: "border-amber-600/50 focus-within:border-amber-500", btn: "bg-amber-600 hover:bg-amber-700" },
  { active: "bg-sky-600 text-white", border: "border-sky-600/50 focus-within:border-sky-500", btn: "bg-sky-600 hover:bg-sky-700" },
  { active: "bg-violet-600 text-white", border: "border-violet-600/50 focus-within:border-violet-500", btn: "bg-violet-600 hover:bg-violet-700" },
  { active: "bg-emerald-600 text-white", border: "border-emerald-600/50 focus-within:border-emerald-500", btn: "bg-emerald-600 hover:bg-emerald-700" },
  { active: "bg-rose-600 text-white", border: "border-rose-600/50 focus-within:border-rose-500", btn: "bg-rose-600 hover:bg-rose-700" },
];

interface ChatInputProps {
  onSend: (message: string, attachments?: ChatAttachment[]) => void;
  onStop?: () => void;
  isLoading: boolean;
  disabled?: boolean;
  disabledMessage?: string;
  placeholder?: string;
  mode?: ChatMode;
  onModeChange?: (mode: ChatMode) => void;
}

const MAX_ATTACHMENTS = 5;
const MAX_FILE_MB = 5;

/** 压缩图片:最长边 1600px, JPEG 0.82, 返回 base64(无前缀) */
async function compressImage(file: File): Promise<{ data: string; mediaType: string }> {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * scale);
  const h = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, w, h);
  const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
  return { data: dataUrl.split(",")[1], mediaType: "image/jpeg" };
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve((reader.result as string).split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

export function ChatInput({ onSend, onStop, isLoading, disabled, disabledMessage, placeholder, mode = "standard", onModeChange }: ChatInputProps) {
  const [input, setInput] = useState("");
  const [attachments, setAttachments] = useState<(ChatAttachment & { preview?: string })[]>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 160) + "px";
    }
  }, [input]);

  const addFiles = useCallback(async (files: File[]) => {
    setAttachError(null);
    for (const file of files) {
      if (attachments.length >= MAX_ATTACHMENTS) {
        setAttachError(`最多 ${MAX_ATTACHMENTS} 个附件`);
        break;
      }
      if (file.size > MAX_FILE_MB * 1024 * 1024) {
        setAttachError(`${file.name} 超过 ${MAX_FILE_MB}MB`);
        continue;
      }
      try {
        if (file.type.startsWith("image/")) {
          const { data, mediaType } = await compressImage(file);
          setAttachments((prev) => [...prev, {
            type: "image", name: file.name || "截图.jpg", media_type: mediaType, data,
            preview: `data:${mediaType};base64,${data}`,
          }]);
        } else {
          const ok = /\.(pdf|docx|txt|md)$/i.test(file.name);
          if (!ok) {
            setAttachError(`不支持的文件类型: ${file.name}(支持 pdf/docx/txt/md 和图片)`);
            continue;
          }
          const data = await fileToBase64(file);
          setAttachments((prev) => [...prev, {
            type: "file", name: file.name, media_type: file.type || "application/octet-stream", data,
          }]);
        }
      } catch {
        setAttachError(`读取 ${file.name} 失败`);
      }
    }
  }, [attachments.length]);

  // 粘贴截图支持
  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    const items = Array.from(e.clipboardData?.items || []);
    const imageFiles = items
      .filter((it) => it.type.startsWith("image/"))
      .map((it) => it.getAsFile())
      .filter((f): f is File => !!f);
    if (imageFiles.length > 0) {
      e.preventDefault();
      addFiles(imageFiles);
    }
  }, [addFiles]);

  const handleSend = () => {
    if ((!input.trim() && attachments.length === 0) || isLoading || disabled) return;
    onSend(input.trim() || "(请分析附件内容)", attachments.map(({ preview: _p, ...a }) => a));
    setInput("");
    setAttachments([]);
    setAttachError(null);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  useEffect(() => {
    fetchPersonas().then(setPersonas).catch(() => {});
  }, []);

  const isAgent = mode !== "standard";
  const activeIdx = personas.findIndex((p) => p.key === mode);
  const pillStyle = activeIdx >= 0 ? PILL_STYLES[activeIdx % PILL_STYLES.length] : null;
  const activeLabel = activeIdx >= 0 ? personas[activeIdx].label : "";
  const activeCost = activeIdx >= 0 ? personas[activeIdx].cost : 10;

  return (
    <div className="border-t border-border/30 bg-background p-4">
      <div className="max-w-5xl mx-auto relative">
        {/* 模式选择器 */}
        {onModeChange && (
          <div className="flex items-center gap-1.5 mb-2">
            <button
              onClick={() => onModeChange("standard")}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                !isAgent
                  ? "bg-primary text-primary-foreground"
                  : "bg-accent/50 text-muted-foreground hover:text-foreground"
              }`}
            >
              标准问答
            </button>
            {personas.map((p, i) => (
              <button
                key={p.key}
                onClick={() => onModeChange(p.key)}
                title={p.desc}
                className={`flex items-center gap-1 px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                  mode === p.key
                    ? PILL_STYLES[i % PILL_STYLES.length].active
                    : "bg-accent/50 text-muted-foreground hover:text-foreground"
                }`}
              >
                <Sparkles className="h-3 w-3" />
                {p.label}
              </button>
            ))}
            {isAgent && (
              <span className="text-[10px] text-muted-foreground ml-1">
                {activeLabel}本人回答 · 查库+联网{activeCost > 0 ? ` · ${activeCost}积分/次` : ""} · 响应较慢
              </span>
            )}
          </div>
        )}

        {/* 附件预览区 */}
        {attachments.length > 0 && (
          <div className="flex flex-wrap gap-2 mb-2">
            {attachments.map((a, i) => (
              <div key={i} className="relative group flex items-center gap-1.5 rounded-lg border border-border/50 bg-card px-2 py-1.5">
                {a.type === "image" && a.preview ? (
                  <img src={a.preview} alt={a.name} className="h-10 w-10 object-cover rounded" />
                ) : (
                  <FileText className="h-5 w-5 text-muted-foreground" />
                )}
                <span className="text-xs text-muted-foreground max-w-[120px] truncate">{a.name}</span>
                <button
                  onClick={() => setAttachments((prev) => prev.filter((_, j) => j !== i))}
                  className="absolute -top-1.5 -right-1.5 h-4 w-4 rounded-full bg-foreground/70 text-background flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                >
                  <X className="h-2.5 w-2.5" />
                </button>
              </div>
            ))}
          </div>
        )}
        {attachError && <p className="text-xs text-red-500 mb-1.5">{attachError}</p>}

        <div className={`flex items-end gap-2 rounded-2xl border bg-card px-3 py-2 transition-colors ${
          pillStyle ? pillStyle.border : "border-border/50 focus-within:border-primary/50"
        }`}>
          {/* 附件按钮 */}
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept="image/*,.pdf,.docx,.txt,.md"
            className="hidden"
            onChange={(e) => {
              const files = Array.from(e.target.files || []);
              if (files.length) addFiles(files);
              e.target.value = "";
            }}
          />
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={disabled || isLoading}
            title="添加图片或文件(也可直接粘贴截图)"
            className="shrink-0 h-8 w-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors disabled:opacity-40"
          >
            <Paperclip className="h-4 w-4" />
          </button>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            placeholder={placeholder || (isAgent
              ? `问一个新问题,${activeLabel}本人为你解答...(支持粘贴截图)`
              : "输入你的问题...(支持粘贴截图)")}
            disabled={disabled}
            rows={1}
            className="flex-1 resize-none bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none disabled:opacity-50 py-1.5 max-h-[160px]"
          />
          {isLoading && onStop ? (
            <Button
              size="icon"
              variant="outline"
              className="h-8 w-8 shrink-0 rounded-xl border-red-400/50 text-red-500 hover:bg-red-500/10"
              onClick={onStop}
              title="中止回答"
            >
              <Square className="h-3.5 w-3.5 fill-current" />
            </Button>
          ) : (
            <Button
              size="icon"
              className={`h-8 w-8 shrink-0 rounded-xl ${pillStyle ? pillStyle.btn : ""}`}
              onClick={handleSend}
              disabled={(!input.trim() && attachments.length === 0) || isLoading || disabled}
            >
              {isLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Send className="h-4 w-4" />
              )}
            </Button>
          )}
        </div>
        {disabled && disabledMessage && (
          <p className="text-xs text-center text-muted-foreground mt-2">
            {disabledMessage}
          </p>
        )}
      </div>
    </div>
  );
}
