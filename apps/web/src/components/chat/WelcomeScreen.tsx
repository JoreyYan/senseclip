import { Sun } from "lucide-react";

interface WelcomeScreenProps {
  onSelectQuestion: (question: string) => void;
}

export function WelcomeScreen(_props: WelcomeScreenProps) {
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
        <p className="text-base text-muted-foreground leading-relaxed max-w-md">
          基于视频资料的 AI 问答引擎
        </p>
      </div>
    </div>
  );
}
