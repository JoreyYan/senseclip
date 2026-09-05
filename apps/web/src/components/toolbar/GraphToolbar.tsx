import { Search, RefreshCw, ArrowLeft, Zap, Sparkles } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { CATEGORY_ZH, RELATION_TYPE_ZH, CATEGORY_COLORS, RELATION_EDGE_STYLES } from "@/constants";

interface GraphToolbarProps {
  searchQuery: string;
  onSearchChange: (query: string) => void;
  onSearch: () => void;
  categoryFilter: string;
  onCategoryFilterChange: (cat: string) => void;
  relationFilter: string;
  onRelationFilterChange: (rel: string) => void;
  onRefresh: () => void;
  onQuickRebuild?: () => void;
  onAiRebuild?: () => void;
  isLoading: boolean;
  isRebuilding?: boolean;
  isEgoMode: boolean;
  egoPersonName?: string;
  onBackToGlobal: () => void;
}

function RebuildButtons({
  onQuickRebuild,
  onAiRebuild,
  isRebuilding,
}: {
  onQuickRebuild?: () => void;
  onAiRebuild?: () => void;
  isRebuilding?: boolean;
}) {
  const { role } = useAuth();
  if (role !== "owner") return null;

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={onQuickRebuild}
        disabled={isRebuilding}
      >
        <Zap className="h-3.5 w-3.5" />
        快速重建
      </Button>
      <Button
        variant="outline"
        size="sm"
        className="h-8 text-xs gap-1.5"
        onClick={onAiRebuild}
        disabled={isRebuilding}
      >
        <Sparkles className="h-3.5 w-3.5" />
        AI 重建
      </Button>
    </>
  );
}

export function GraphToolbar({
  searchQuery,
  onSearchChange,
  onSearch,
  categoryFilter,
  onCategoryFilterChange,
  relationFilter,
  onRelationFilterChange,
  onRefresh,
  onQuickRebuild,
  onAiRebuild,
  isLoading,
  isRebuilding,
  isEgoMode,
  egoPersonName,
  onBackToGlobal,
}: GraphToolbarProps) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      {isEgoMode && (
        <Button variant="ghost" size="sm" onClick={onBackToGlobal} className="gap-1.5 text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
          返回全局图
        </Button>
      )}

      {isEgoMode && egoPersonName && (
        <Badge variant="secondary" className="text-xs">
          {egoPersonName} 的关系网络
        </Badge>
      )}

      <div className="relative flex-1 max-w-xs">
        <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
        <Input
          placeholder="搜索人物..."
          value={searchQuery}
          onChange={(e) => onSearchChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSearch()}
          className="pl-8 h-8 text-xs bg-card border-border/50"
        />
      </div>

      <Select value={categoryFilter} onValueChange={onCategoryFilterChange}>
        <SelectTrigger className="w-[120px] h-8 text-xs bg-card border-border/50">
          <SelectValue placeholder="人物类别" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部类别</SelectItem>
          {Object.entries(CATEGORY_ZH).map(([key, label]) => (
            <SelectItem key={key} value={key}>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: CATEGORY_COLORS[key] }} />
                {label}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Select value={relationFilter} onValueChange={onRelationFilterChange}>
        <SelectTrigger className="w-[120px] h-8 text-xs bg-card border-border/50">
          <SelectValue placeholder="关系类型" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">全部关系</SelectItem>
          {Object.entries(RELATION_TYPE_ZH).map(([key, label]) => (
            <SelectItem key={key} value={key}>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ backgroundColor: RELATION_EDGE_STYLES[key]?.color || "#6b7280" }} />
                {label}
              </span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Button variant="ghost" size="icon" className="h-8 w-8" onClick={onRefresh} disabled={isLoading}>
        <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
      </Button>

      {/* Owner-only rebuild buttons */}
      <RebuildButtons
        onQuickRebuild={onQuickRebuild}
        onAiRebuild={onAiRebuild}
        isRebuilding={isRebuilding}
      />

      {/* Legend */}
      <div className="hidden lg:flex items-center gap-2 ml-auto text-[10px] text-muted-foreground">
        {Object.entries(CATEGORY_ZH).slice(0, 4).map(([key, label]) => (
          <span key={key} className="flex items-center gap-1">
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: CATEGORY_COLORS[key] }} />
            {label}
          </span>
        ))}
      </div>
    </div>
  );
}
