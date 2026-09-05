import { createContext, useContext, useEffect, useState, useCallback, type ReactNode } from "react";
import { supabase } from "@/lib/supabase";
import { API_BASE } from "@/constants";
import type { User, Session } from "@supabase/supabase-js";

export type UserRole = "owner" | "editor" | "viewer" | "guest";

interface AuthState {
  user: User | null;
  session: Session | null;
  role: UserRole;
  loading: boolean;
  credits: number | null;
  billingEnabled: boolean;
  refreshCredits: () => void;
  guestChatsUsed: number;
  guestChatLimit: number;
  canChat: boolean;
  signInWithPassword: (email: string, password: string) => Promise<{ error: string | null }>;
  signUp: (email: string, password: string) => Promise<{ error: string | null }>;
  verifyOtp: (email: string, token: string) => Promise<{ error: string | null }>;
  signOut: () => Promise<void>;
  incrementGuestChat: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

const GUEST_CHAT_LIMIT = 5;
const GUEST_CHAT_KEY = "senseclip_guest_chats";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [session, setSession] = useState<Session | null>(null);
  const [role, setRole] = useState<UserRole>("guest");
  const [loading, setLoading] = useState(true);
  const [credits, setCredits] = useState<number | null>(null);
  const [billingEnabled, setBillingEnabled] = useState(true);
  const [guestChatsUsed, setGuestChatsUsed] = useState(() => {
    return parseInt(localStorage.getItem(GUEST_CHAT_KEY) || "0", 10);
  });

  const refreshCredits = useCallback(async () => {
    try {
      const { data: { session: s } } = await supabase.auth.getSession();
      if (!s?.access_token) { setCredits(null); return; }
      const res = await fetch(`${API_BASE}/api/billing/credits`, {
        headers: { Authorization: `Bearer ${s.access_token}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.billing_enabled === false) { setBillingEnabled(false); setCredits(null); }
        else { setBillingEnabled(true); setCredits(data.balance); }
      }
    } catch { /* ignore */ }
  }, []);

  const fetchRole = useCallback(async (userId: string): Promise<UserRole> => {
    try {
      console.log("[auth] fetchRole for", userId);
      const res = await fetch(`${API_BASE}/api/me/role?user_id=${userId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      console.log("[auth] fetchRole result:", data.role);
      return (data.role as UserRole) || "viewer";
    } catch (ex) {
      console.error("[auth] fetchRole exception:", ex);
      return "viewer";
    }
  }, []);

  useEffect(() => {
    // Get initial session
    supabase.auth.getSession().then(async ({ data: { session: s } }) => {
      console.log("[auth] getSession:", s ? s.user?.email : "no session");
      setSession(s);
      setUser(s?.user ?? null);
      if (s?.user) {
        const r = await fetchRole(s.user.id);
        setRole(r);
        refreshCredits();
      } else {
        setRole("guest");
        setCredits(null);
      }
      setLoading(false);
    }).catch((err) => {
      console.error("[auth] getSession error:", err);
      setLoading(false);
    });

    // Listen for auth changes — DO NOT await inside this callback!
    // Supabase waits for onAuthStateChange to finish before resolving signInWithPassword.
    // If we await fetchRole here, it deadlocks.
    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, s) => {
      console.log("[auth] onAuthStateChange:", _event, s?.user?.email);
      setSession(s);
      setUser(s?.user ?? null);
      if (s?.user) {
        fetchRole(s.user.id).then((r) => setRole(r));
        refreshCredits();
      } else {
        setRole("guest");
        setCredits(null);
      }
    });

    return () => subscription.unsubscribe();
  }, [fetchRole]);

  const signInWithPassword = useCallback(async (email: string, password: string) => {
    console.log("[auth] signInWithPassword start");
    try {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      console.log("[auth] signInWithPassword result:", error ? error.message : "success");
      return { error: error?.message ?? null };
    } catch (ex: any) {
      console.error("[auth] signInWithPassword exception:", ex);
      return { error: ex?.message || "登录请求失败" };
    }
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const { error } = await supabase.auth.signUp({ email, password });
    return { error: error?.message ?? null };
  }, []);

  const verifyOtp = useCallback(async (email: string, token: string) => {
    const { error } = await supabase.auth.verifyOtp({ email, token, type: "signup" });
    return { error: error?.message ?? null };
  }, []);

  const signOut = useCallback(async () => {
    try {
      await supabase.auth.signOut();
    } catch {
      // force clear even if signOut API fails
    }
    setUser(null);
    setSession(null);
    setRole("guest");
  }, []);

  const incrementGuestChat = useCallback(() => {
    setGuestChatsUsed((prev) => {
      const next = prev + 1;
      localStorage.setItem(GUEST_CHAT_KEY, String(next));
      return next;
    });
  }, []);

  const isGuest = !user;
  const canChat = !isGuest || guestChatsUsed < GUEST_CHAT_LIMIT;

  return (
    <AuthContext.Provider
      value={{
        user,
        session,
        role,
        loading,
        credits,
        billingEnabled,
        refreshCredits,
        guestChatsUsed,
        guestChatLimit: GUEST_CHAT_LIMIT,
        canChat,
        signInWithPassword,
        signUp,
        verifyOtp,
        signOut,
        incrementGuestChat,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
