import { createClient } from "@supabase/supabase-js";

// 大陆网络经常无法直连 supabase.co —— 生产环境走 Vercel 同源代理
// (vercel.json 把 /sb-proxy/* 反代到 Supabase),本地开发走直连。
const isBrowser = typeof window !== "undefined";
const isLocalDev = isBrowser && (window.location.hostname === "localhost" || window.location.hostname === "127.0.0.1");

const SUPABASE_URL =
  import.meta.env.VITE_SUPABASE_URL ||
  (isBrowser && !isLocalDev
    ? `${window.location.origin}/sb-proxy`
    : (import.meta.env.VITE_SUPABASE_DIRECT_URL as string));

const SUPABASE_ANON_KEY = import.meta.env.VITE_SUPABASE_ANON_KEY as string;
if (!SUPABASE_ANON_KEY) console.warn("VITE_SUPABASE_ANON_KEY 未配置");

export const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
