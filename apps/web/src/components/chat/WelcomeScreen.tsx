import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sun, Vote, ArrowRight } from "lucide-react";
import { fetchNominations, type NominationBoard } from "@/services/nominationApi";

interface WelcomeScreenProps {
  onSelectQuestion: (question: string) => void;
}

export function WelcomeScreen(_props: WelcomeScreenProps) {
  const [board, setBoard] = useState<NominationBoard | null>(null);

  useEffect(() => {
    fetchNominations().then(setBoard).catch(() => {});
  }, []);

  const coming = board?.coming_soon || [];
  const top = (board?.nominations || []).slice(0, 3);

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4 max-w-2xl mx-auto w-full relative overflow-hidden">
      {/* Warm gradient background orbs */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        <div className="absolute top-1/4 left-1/4 w-[400px] h-[400px] rounded-full bg-gradient-to-br from-orange-200/40 via-amber-100/30 to-yellow-200/20 blur-3xl" />
        <div className="absolute bottom-1/4 right-1/4 w-[350px] h-[350px] rounded-full bg-gradient-to-tl from-rose-200/30 via-orange-100/20 to-amber-200/25 blur-3xl" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] rounded-full bg-gradient-to-r from-yellow-100/20 to-orange-100/15 blur-3xl" />
      </div>

      {/* Content */}
      <div className="relative z-10 text-center">
        <div className="inline-flex items-center justify-center w-16 h-16 rounded-3xl bg-gradient-to-br from-orange-400 to-amber-500 shadow-lg shadow-orange-300/30 mb-5">
          <Sun className="h-8 w-8 text-white" />
        </div>
        <h1 className="text-3xl font-bold text-foreground mb-3 tracking-tight">
          SenseClip
        </h1>
        <p className="text-base text-muted-foreground leading-relaxed max-w-md mx-auto">
          基于视频资料的 AI 问答引擎
        </p>
      </div>

      {/* 提名活动卡片 */}
      <Link
        to="/nominate"
        className="relative z-10 mt-8 w-full max-w-md group rounded-2xl border border-orange-200/70 bg-white/70 backdrop-blur px-4 py-3.5 text-left shadow-sm hover:shadow-md hover:border-orange-300 transition-all"
      >
        <div className="flex items-center gap-2 text-xs font-semibold text-orange-600">
          <Vote className="h-3.5 w-3.5" /> 提名活动
          <span className="ml-auto inline-flex items-center gap-0.5 text-muted-foreground group-hover:text-orange-600">
            去提名 <ArrowRight className="h-3 w-3" />
          </span>
        </div>
        <p className="mt-1 text-sm font-medium text-foreground">
          你最想和哪位博主对话?贴上 YouTube 或 X 链接,票高先上线
        </p>
        {(coming.length > 0 || top.length > 0) && (
          <div className="mt-2.5 flex items-center gap-1.5 flex-wrap text-xs">
            {coming.map((p) => (
              <span key={p.key} className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200/70 pl-0.5 pr-2 py-0.5 text-amber-800">
                <img src={p.avatar} alt="" className="h-4 w-4 rounded-full object-cover" />
                {p.label} · 即将上线
              </span>
            ))}
            {top.map((n) => (
              <span key={n.id} className="inline-flex items-center rounded-full bg-zinc-50 border border-border/60 px-2 py-0.5 text-muted-foreground">
                {n.name} · {n.votes} 票
              </span>
            ))}
          </div>
        )}
      </Link>
    </div>
  );
}
