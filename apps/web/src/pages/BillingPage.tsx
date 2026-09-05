import { useState, useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { getCredits, createCheckout, type CreditTransaction } from "@/services/api";
import { Sparkles, Check } from "lucide-react";

const SUB_PLANS = [
  {
    id: "pro", name: "Pro", price: "$5.99", period: "/月", credits: 600, popular: true,
    features: ["每月 600 积分", "约 60 次博主深度分析", "或 600 次标准问答", "积分滚存不清零"],
  },
  {
    id: "max", name: "Max", price: "$14.99", period: "/月", credits: 2000, popular: false,
    features: ["每月 2000 积分", "约 200 次博主深度分析", "重度使用无忧", "积分滚存不清零"],
  },
];

const TYPE_LABELS: Record<string, { text: string; color: string }> = {
  signup_bonus: { text: "赠送", color: "bg-amber-900/40 text-amber-300" },
  purchase: { text: "加油包", color: "bg-green-900/40 text-green-300" },
  subscription: { text: "订阅发放", color: "bg-emerald-900/40 text-emerald-300" },
  ingest: { text: "视频处理", color: "bg-blue-900/40 text-blue-300" },
  chat: { text: "问答", color: "bg-blue-900/40 text-blue-300" },
  consult: { text: "鲁社长", color: "bg-orange-900/40 text-orange-300" },
  admin_adjustment: { text: "调整", color: "bg-purple-900/40 text-purple-300" },
};

export default function BillingPage() {
  const { user, credits, refreshCredits, billingEnabled } = useAuth();
  if (!billingEnabled) {
    return <div className="max-w-xl mx-auto p-10 text-center text-sm text-muted-foreground">此实例未启用计费(BILLING_ENABLED=false),所有功能免费使用。</div>;
  }
  const [searchParams] = useSearchParams();
  const [transactions, setTransactions] = useState<CreditTransaction[]>([]);
  const [plan, setPlan] = useState<string | null>(null);
  const [periodEnd, setPeriodEnd] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [buying, setBuying] = useState<string | null>(null);
  const [toast, setToast] = useState<{ type: "success" | "error"; text: string } | null>(null);

  useEffect(() => {
    const status = searchParams.get("status");
    if (status === "success") {
      setToast({ type: "success", text: "支付成功!积分将在数秒内到账(订阅每月自动续)。" });
      refreshCredits();
    } else if (status === "cancelled") {
      setToast({ type: "error", text: "支付已取消。" });
    }
  }, [searchParams, refreshCredits]);

  useEffect(() => {
    if (!user) { setLoading(false); return; }
    getCredits()
      .then((data) => {
        setTransactions(data.transactions);
        setPlan(data.plan ?? null);
        setPeriodEnd(data.sub_period_end ?? null);
        refreshCredits();
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [user, refreshCredits]);

  async function handleBuy(planId: string) {
    setBuying(planId);
    try {
      const { checkout_url } = await createCheckout(planId);
      window.location.href = checkout_url;
    } catch (err) {
      setToast({ type: "error", text: err instanceof Error ? err.message : "创建订单失败" });
      setBuying(null);
    }
  }

  if (!user) {
    return (
      <div className="flex-1 overflow-auto bg-gray-950 text-white p-8">
        <div className="max-w-md mx-auto text-center mt-20">
          <p className="text-gray-400 mb-4">请先登录以查看积分信息</p>
          <Link to="/login" className="text-blue-400 hover:text-blue-300 text-sm">去登录 →</Link>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto bg-gray-950 text-white p-8">
      <div className="max-w-3xl mx-auto">
        <div className="mb-6">
          <Link to="/" className="text-xs text-gray-500 hover:text-gray-400">← 返回</Link>
        </div>

        <h1 className="text-2xl font-bold mb-2">订阅与积分</h1>
        <p className="text-gray-400 text-sm mb-8">标准问答 1 积分/次 · 博主模式(深度检索) 10 积分/次 · 积分滚存不过期</p>

        {toast && (
          <div className={"mb-6 p-3 rounded-lg text-sm " +
            (toast.type === "success"
              ? "bg-green-900/30 border border-green-800 text-green-400"
              : "bg-red-900/30 border border-red-800 text-red-400")}>
            {toast.text}
          </div>
        )}

        {/* 余额与订阅状态 */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 mb-8 flex items-center justify-between">
          <div>
            <p className="text-gray-400 text-sm mb-1">当前余额</p>
            <p className="text-4xl font-bold">{credits ?? "..."} <span className="text-base font-normal text-gray-500">积分</span></p>
          </div>
          <div className="text-right">
            {plan ? (
              <>
                <span className="inline-flex items-center gap-1 text-sm px-3 py-1 rounded-full bg-emerald-900/40 text-emerald-300">
                  <Sparkles className="h-3.5 w-3.5" /> {plan === "pro" ? "Pro" : "Max"} 订阅中
                </span>
                {periodEnd && (
                  <p className="text-xs text-gray-500 mt-2">本期至 {new Date(periodEnd).toLocaleDateString("zh-CN")}</p>
                )}
              </>
            ) : (
              <span className="text-xs text-gray-500">未订阅</span>
            )}
          </div>
        </div>

        {/* 订阅计划 */}
        <h2 className="text-base font-semibold mb-4">订阅计划</h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
          {SUB_PLANS.map((p) => (
            <div key={p.id}
              className={"bg-gray-900 border rounded-xl p-6 relative " +
                (p.popular ? "border-amber-600" : "border-gray-800")}>
              {p.popular && (
                <span className="absolute -top-2.5 left-6 text-[10px] bg-amber-600 text-white px-2 py-0.5 rounded-full">
                  推荐
                </span>
              )}
              <div className="flex items-baseline justify-between mb-4">
                <span className="text-lg font-bold">{p.name}</span>
                <span><span className="text-2xl font-bold">{p.price}</span><span className="text-gray-500 text-sm">{p.period}</span></span>
              </div>
              <ul className="space-y-1.5 mb-5">
                {p.features.map((f) => (
                  <li key={f} className="flex items-center gap-2 text-xs text-gray-400">
                    <Check className="h-3 w-3 text-emerald-500 shrink-0" /> {f}
                  </li>
                ))}
              </ul>
              <button
                onClick={() => handleBuy(p.id)}
                disabled={buying !== null || plan === p.id}
                className={"w-full py-2 rounded-lg text-sm font-medium transition-colors " +
                  (plan === p.id
                    ? "bg-gray-800 text-gray-500 cursor-default"
                    : p.popular
                    ? "bg-amber-600 hover:bg-amber-700 text-white"
                    : "bg-gray-800 hover:bg-gray-700 text-gray-200") +
                  (buying === p.id ? " opacity-50 cursor-wait" : "")}
              >
                {plan === p.id ? "当前计划" : buying === p.id ? "跳转中..." : "订阅"}
              </button>
            </div>
          ))}
        </div>

        {/* 加油包 */}
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-4 mb-8 flex items-center justify-between">
          <div>
            <p className="text-sm font-medium">加油包</p>
            <p className="text-xs text-gray-500">一次性 200 积分,不订阅也可购买</p>
          </div>
          <button
            onClick={() => handleBuy("topup")}
            disabled={buying !== null}
            className={"px-4 py-2 rounded-lg text-sm bg-gray-800 hover:bg-gray-700 text-gray-200 transition-colors" +
              (buying === "topup" ? " opacity-50 cursor-wait" : "")}
          >
            {buying === "topup" ? "跳转中..." : "$2.99 购买"}
          </button>
        </div>

        {/* 使用记录 */}
        <h2 className="text-base font-semibold mb-4">使用记录</h2>
        <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
          {loading ? (
            <p className="text-xs text-gray-400 p-4">加载中...</p>
          ) : transactions.length === 0 ? (
            <p className="text-xs text-gray-500 p-4 text-center">暂无记录</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-gray-400 text-xs">
                  <th className="text-left px-4 py-2 font-normal">时间</th>
                  <th className="text-left px-4 py-2 font-normal">类型</th>
                  <th className="text-right px-4 py-2 font-normal">数量</th>
                  <th className="text-left px-4 py-2 font-normal">说明</th>
                </tr>
              </thead>
              <tbody>
                {transactions.map((tx) => {
                  const typeInfo = TYPE_LABELS[tx.type] || { text: tx.type, color: "bg-gray-700/40 text-gray-400" };
                  return (
                    <tr key={tx.id} className="border-b border-gray-800/50 last:border-0">
                      <td className="px-4 py-2.5 text-xs text-gray-400">
                        {new Date(tx.created_at).toLocaleString("zh-CN", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td className="px-4 py-2.5">
                        <span className={"text-xs px-1.5 py-0.5 rounded " + typeInfo.color}>
                          {typeInfo.text}
                        </span>
                      </td>
                      <td className={"px-4 py-2.5 text-right text-xs font-medium " +
                        (tx.amount > 0 ? "text-green-400" : "text-red-400")}>
                        {tx.amount > 0 ? `+${tx.amount}` : tx.amount}
                      </td>
                      <td className="px-4 py-2.5 text-xs text-gray-400 max-w-[220px] truncate">
                        {tx.description}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
