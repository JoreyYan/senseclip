import { Users, Video, GitBranch, Sparkles } from "lucide-react";
import type { NetworkStats } from "@/types";

interface StatsBarProps {
  stats: NetworkStats | null;
  directedEdgeCount: number;
}

export function StatsBar({ stats, directedEdgeCount }: StatsBarProps) {
  if (!stats) return null;

  const items = [
    { icon: Video, label: "视频", value: stats.video_count },
    { icon: Users, label: "人物", value: stats.person_count },
    { icon: GitBranch, label: "有向关系", value: directedEdgeCount },
    { icon: Sparkles, label: "AI增强", value: stats.ai_enriched ? "已启用" : "未启用" },
  ];

  return (
    <div className="flex gap-3">
      {items.map((item) => (
        <div
          key={item.label}
          className="flex items-center gap-2 rounded-lg border border-border/50 bg-card px-3 py-1.5 text-xs"
        >
          <item.icon className="h-3.5 w-3.5 text-muted-foreground" />
          <span className="text-muted-foreground">{item.label}</span>
          <span className="font-semibold text-foreground">{item.value}</span>
        </div>
      ))}
    </div>
  );
}
