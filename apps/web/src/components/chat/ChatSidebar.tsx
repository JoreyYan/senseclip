import { Plus, MessageSquare, X, PanelLeftClose, PanelLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { Conversation } from "@/types/chat";
import { cn } from "@/lib/utils";

interface ChatSidebarProps {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  isCollapsed: boolean;
  onToggle: () => void;
  isGuest?: boolean;
}

function groupByDate(conversations: Conversation[]) {
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  const weekAgo = new Date(today.getTime() - 7 * 86400000);

  const groups: { label: string; items: Conversation[] }[] = [
    { label: "今天", items: [] },
    { label: "昨天", items: [] },
    { label: "过去 7 天", items: [] },
    { label: "更早", items: [] },
  ];

  conversations.forEach((c) => {
    const d = new Date(c.updated_at);
    if (d >= today) groups[0].items.push(c);
    else if (d >= yesterday) groups[1].items.push(c);
    else if (d >= weekAgo) groups[2].items.push(c);
    else groups[3].items.push(c);
  });

  return groups.filter((g) => g.items.length > 0);
}

export function ChatSidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  isCollapsed,
  onToggle,
  isGuest,
}: ChatSidebarProps) {
  if (isCollapsed) {
    return (
      <div className="flex flex-col items-center py-3 px-1 border-r border-border/30 bg-background gap-2">
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onToggle}>
          <PanelLeft className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onNew}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  const groups = groupByDate(conversations);

  return (
    <div className="w-[260px] shrink-0 flex flex-col border-r border-border/30 bg-background">
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border/20">
        <Button
          variant="ghost"
          size="sm"
          className="h-8 gap-1.5 text-xs text-foreground/80"
          onClick={onNew}
        >
          <Plus className="h-3.5 w-3.5" />
          新对话
        </Button>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onToggle}>
          <PanelLeftClose className="h-4 w-4" />
        </Button>
      </div>

      {/* Conversation list */}
      <ScrollArea className="flex-1">
        <div className="p-2 space-y-4">
          {groups.length === 0 && (
            <p className="text-xs text-muted-foreground text-center py-8">暂无历史对话</p>
          )}
          {groups.map((group) => (
            <div key={group.label}>
              <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wider px-2 mb-1">
                {group.label}
              </p>
              <div className="space-y-0.5">
                {group.items.map((conv) => (
                  <div
                    key={conv.id}
                    className={cn(
                      "group flex items-center gap-2 px-2 py-1.5 rounded-lg cursor-pointer text-sm transition-colors",
                      activeId === conv.id
                        ? "bg-accent text-foreground"
                        : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
                    )}
                    onClick={() => onSelect(conv.id)}
                  >
                    <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate flex-1">{conv.title}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(conv.id);
                      }}
                      className="opacity-0 group-hover:opacity-100 transition-opacity"
                    >
                      <X className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </ScrollArea>

      {/* Footer */}
      {isGuest && (
        <div className="p-3 border-t border-border/20">
          <p className="text-[11px] text-muted-foreground text-center">
            <button className="text-primary hover:underline">登录</button> 以保存对话历史
          </p>
        </div>
      )}
    </div>
  );
}
