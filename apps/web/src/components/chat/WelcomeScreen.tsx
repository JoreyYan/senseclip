import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Sun, Vote, ArrowRight, Newspaper, Sparkles, Check } from "lucide-react";
import { fetchNominations, type NominationBoard } from "@/services/nominationApi";
import { fetchPersonas, type PersonaInfo } from "@/services/chatApi";
import { API_BASE } from "@/constants";

interface WelcomeScreenProps {
  onSelectQuestion: (question: string) => void;
  onPickPersona?: (key: string) => void;
}

interface ReportBrief {
  id: string;
  label: string;
  avatar: string;
  title: string;
  week_start: string;
}

const NEW_DAYS = 14;

function isNew(p: PersonaInfo) {
  if (!p.published_at) return false;
  return Date.now() - new Date(p.published_at).getTime() < NEW_DAYS * 864e5;
}

export function WelcomeScreen({ onPickPersona }: WelcomeScreenProps) {
  const [board, setBoard] = useState<NominationBoard | null>(null);
  const [personas, setPersonas] = useState<PersonaInfo[]>([]);
  const [report, setReport] = useState<ReportBrief | null>(null);
  const [picked, setPicked] = useState<string | null>(null);

  useEffect(() => {
    fetchNominations().then(setBoard).catch(() => {});
    fetchPersonas().then(setPersonas).catch(() => {});
    fetch(`${API_BASE}/api/reports?limit=1`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => setReport(d?.reports?.[0] || null))
      .catch(() => {});
  }, []);

  const coming = board?.coming_soon || [];
  const fresh = personas.filter(isNew);
  const others = personas.filter((p) => !isNew(p));

  const pick = (key: string) => {
    onPickPersona?.(key);
    setPicked(key);
  };

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="min-h-full flex flex-col items-center justify-center px-4 py-8 max-w-2xl mx-auto w-full relative">
        {/* Warm gradient background orbs */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          <div className="absolute top-1/4 left-1/4 w-[400px] h-[400px] rounded-full bg-gradient-to-br from-orange-200/40 via-amber-100/30 to-yellow-200/20 blur-3xl" />
          <div className="absolute bottom-1/4 right-1/4 w-[350px] h-[350px] rounded-full bg-gradient-to-tl from-rose-200/30 via-orange-100/20 to-amber-200/25 blur-3xl" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] rounded-full bg-gradient-to-r from-yellow-100/20 to-orange-100/15 blur-3xl" />
        </div>

        <div className="relative z-10 text-center">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-3xl bg-gradient-to-br from-orange-400 to-amber-500 shadow-lg shadow-orange-300/30 mb-4">
            <Sun className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-3xl font-bold text-foreground mb-2 tracking-tight">SenseClip</h1>
          <p className="text-sm text-muted-foreground leading-relaxed max-w-md mx-auto">
            把博主的公开内容变成可对话的 AI 人格,句句可溯源
          </p>
        </div>

        {/* 新上线的博主 */}
        {fresh.length > 0 && (
          <section className="relative z-10 mt-6 w-full max-w-md">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-orange-600 mb-2">
              <Sparkles className="h-3.5 w-3.5" /> 新上线
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {fresh.map((p) => (
                <button
                  key={p.key}
                  onClick={() => pick(p.key)}
                  className={`flex items-start gap-2.5 rounded-2xl border bg-white/70 backdrop-blur px-3 py-2.5 text-left shadow-sm transition-all hover:shadow-md ${
                    picked === p.key ? "border-orange-400 ring-1 ring-orange-200" : "border-orange-200/70 hover:border-orange-300"
                  }`}
                >
                  <img src={p.avatar} alt="" className="h-9 w-9 rounded-full object-cover shrink-0" />
                  <span className="min-w-0">
                    <span className="flex items-center gap-1.5">
                      <span className="text-sm font-medium truncate">{p.label}</span>
                      {picked === p.key
                        ? <Check className="h-3.5 w-3.5 text-orange-500 shrink-0" />
                        : <span className="text-[10px] rounded-full bg-orange-100 text-orange-700 px-1.5">新</span>}
                    </span>
                    <span className="block text-xs text-muted-foreground line-clamp-2 leading-snug">{p.desc}</span>
                  </span>
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {picked
                ? `已切到「${personas.find((p) => p.key === picked)?.label}」,直接在下面提问`
                : "点一下就切到他本人回答"}
            </p>
          </section>
        )}

        {/* 最新一期每周市场解读 */}
        {report && (
          <Link
            to={`/reports?id=${report.id}`}
            className="relative z-10 mt-4 w-full max-w-md group rounded-2xl border border-border/60 bg-white/70 backdrop-blur px-4 py-3 text-left shadow-sm hover:shadow-md hover:border-sky-300 transition-all"
          >
            <div className="flex items-center gap-2 text-xs font-semibold text-sky-700">
              <Newspaper className="h-3.5 w-3.5" /> {report.label} 每周市场解读
              <span className="ml-auto inline-flex items-center gap-0.5 text-muted-foreground group-hover:text-sky-700">
                去看 <ArrowRight className="h-3 w-3" />
              </span>
            </div>
            <p className="mt-1 text-sm text-foreground">
              最新一期:{Number(report.week_start.slice(5, 7))}月{Number(report.week_start.slice(8, 10))}日当周
            </p>
            <p className="text-xs text-muted-foreground">
              每周一更新 · 美债、通胀、就业、仓位与信用四层数据 + 他的判断
            </p>
          </Link>
        )}

        {/* 其他人格 */}
        {others.length > 0 && (
          <div className="relative z-10 mt-4 w-full max-w-md flex items-center gap-1.5 flex-wrap">
            <span className="text-xs text-muted-foreground">也可以问:</span>
            {others.map((p) => (
              <button
                key={p.key}
                onClick={() => pick(p.key)}
                className={`inline-flex items-center gap-1.5 rounded-full border pl-0.5 pr-2.5 py-0.5 text-xs transition-colors ${
                  picked === p.key
                    ? "border-orange-300 bg-orange-50 text-orange-700"
                    : "border-border/60 bg-white/60 hover:border-orange-300"
                }`}
              >
                <img src={p.avatar} alt="" className="h-5 w-5 rounded-full object-cover" />
                {p.label}
              </button>
            ))}
          </div>
        )}

        {/* 提名活动 */}
        <Link
          to="/nominate"
          className="relative z-10 mt-4 w-full max-w-md group rounded-2xl border border-orange-200/70 bg-white/70 backdrop-blur px-4 py-3 text-left shadow-sm hover:shadow-md hover:border-orange-300 transition-all"
        >
          <div className="flex items-center gap-2 text-xs font-semibold text-orange-600">
            <Vote className="h-3.5 w-3.5" /> 提名活动
            <span className="ml-auto inline-flex items-center gap-0.5 text-muted-foreground group-hover:text-orange-600">
              去提名 <ArrowRight className="h-3 w-3" />
            </span>
          </div>
          <p className="mt-1 text-sm text-foreground">
            还想和谁对话?贴上 YouTube 或 X 链接,票高的先上线
          </p>
          {coming.length > 0 && (
            <div className="mt-2 flex items-center gap-1.5 flex-wrap text-xs">
              {coming.map((p) => (
                <span key={p.key} className="inline-flex items-center gap-1 rounded-full bg-amber-50 border border-amber-200/70 pl-0.5 pr-2 py-0.5 text-amber-800">
                  <img src={p.avatar} alt="" className="h-4 w-4 rounded-full object-cover" />
                  {p.label} · 即将上线
                </span>
              ))}
            </div>
          )}
        </Link>
      </div>
    </div>
  );
}
