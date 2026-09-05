import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { uploadCookies, getCookiesStatus, adjustCredits } from "@/services/api";
import { useAuth } from "@/hooks/useAuth";
import { supabase } from "@/lib/supabase";
import { API_BASE } from "@/constants";

interface UserInfo {
  user_id: string;
  email: string;
  role: string;
  credits: number;
  created_at: string;
}

function UserRoleManager() {
  const [users, setUsers] = useState<UserInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState<string | null>(null);

  const getToken = async () => {
    const { data: { session } } = await supabase.auth.getSession();
    return session?.access_token || "";
  };

  const loadUsers = async () => {
    setLoading(true);
    setError(null);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/admin/users`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || "加载失败");
      }
      const d = await res.json();
      setUsers(d.users || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "加载失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadUsers(); }, []);

  const changeRole = async (email: string, newRole: string) => {
    setSaving(email);
    try {
      const token = await getToken();
      const res = await fetch(`${API_BASE}/api/admin/set-role?email=${encodeURIComponent(email)}&role=${newRole}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail || "设置失败");
      }
      setUsers(prev => prev.map(u => u.email === email ? { ...u, role: newRole } : u));
    } catch (e) {
      setError(e instanceof Error ? e.message : "设置失败");
    } finally {
      setSaving(null);
    }
  };

  const handleAdjustCredits = async (email: string, amount: number) => {
    setSaving(email);
    try {
      const res = await adjustCredits(email, amount, amount > 0 ? "管理员手动增加" : "管理员手动扣减");
      setUsers(prev => prev.map(u => u.email === email ? { ...u, credits: res.new_balance } : u));
    } catch (e) {
      setError(e instanceof Error ? e.message : "调整失败");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
      <h2 className="text-base font-semibold mb-4">用户权限管理</h2>
      {error && <p className="text-xs text-red-400 mb-3">{error}</p>}
      {loading ? (
        <p className="text-xs text-gray-400">加载中...</p>
      ) : (
        <div className="space-y-2">
          {users.map((u) => (
            <div key={u.user_id} className="flex items-center justify-between py-2 px-3 bg-gray-800/50 rounded-lg">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-sm text-gray-200 truncate">{u.email}</span>
                <span className={"text-xs px-1.5 py-0.5 rounded shrink-0 " + (
                  u.role === "owner" ? "bg-amber-900/40 text-amber-300" :
                  u.role === "editor" ? "bg-blue-900/40 text-blue-300" :
                  "bg-gray-700/40 text-gray-400"
                )}>{u.role}</span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <div className="flex items-center gap-1">
                  <button
                    onClick={() => handleAdjustCredits(u.email, -1)}
                    disabled={saving === u.email || u.credits <= 0}
                    className="w-5 h-5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-30 text-xs text-gray-300 flex items-center justify-center"
                  >−</button>
                  <span className="text-xs text-gray-300 w-6 text-center font-medium">{u.credits}</span>
                  <button
                    onClick={() => handleAdjustCredits(u.email, 5)}
                    disabled={saving === u.email}
                    className="w-5 h-5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-30 text-xs text-gray-300 flex items-center justify-center"
                  >+</button>
                </div>
                {u.role !== "owner" && (
                  <select
                    value={u.role}
                    onChange={(e) => changeRole(u.email, e.target.value)}
                    disabled={saving === u.email}
                    className="text-xs bg-gray-700 border border-gray-600 rounded px-2 py-1 text-gray-200 focus:outline-none"
                  >
                    <option value="viewer">viewer</option>
                    <option value="editor">editor</option>
                  </select>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function SettingsPage() {
  const [cookiesStatus, setCookiesStatus] = useState<{ has_cookies: boolean; updated_at: string | null } | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getCookiesStatus().then(setCookiesStatus).catch(() => {});
  }, []);

  async function handleFile(file: File) {
    if (!file.name.endsWith(".txt")) {
      setMessage({ type: "error", text: "请上传 .txt 格式的 cookies 文件" });
      return;
    }
    setUploading(true);
    setMessage(null);
    try {
      const res = await uploadCookies(file);
      setMessage({ type: "success", text: res.message });
      const status = await getCookiesStatus();
      setCookiesStatus(status);
    } catch (err: unknown) {
      setMessage({ type: "error", text: err instanceof Error ? err.message : "上传失败" });
    } finally {
      setUploading(false);
    }
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  }

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
    e.target.value = "";
  }

  const { role } = useAuth();
  const isOwner = role === "owner";

  return (
    <div className="flex-1 overflow-auto bg-gray-950 text-white p-8">
      <div className="max-w-md mx-auto">
        <div className="mb-6">
          <Link to="/ingest" className="text-xs text-gray-500 hover:text-gray-400">← 返回提交视频</Link>
        </div>

        <h1 className="text-2xl font-bold mb-2">设置</h1>
        <p className="text-gray-400 text-sm mb-8">管理 YouTube Cookies，用于下载需要登录的视频。</p>

        <div className="bg-gray-900 border border-gray-800 rounded-xl p-6">
          <h2 className="text-base font-semibold mb-1">YouTube Cookies</h2>

          {cookiesStatus && (
            <p className={"text-xs mb-4 " + (cookiesStatus.has_cookies ? "text-green-400" : "text-gray-500")}>
              {cookiesStatus.has_cookies
                ? "已配置 · 更新于 " + new Date(cookiesStatus.updated_at!).toLocaleString("zh-CN")
                : "未配置"}
            </p>
          )}

          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => fileInputRef.current?.click()}
            className={"border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors " +
              (dragging ? "border-blue-500 bg-blue-900/10" : "border-gray-700 hover:border-gray-600")}
          >
            <input ref={fileInputRef} type="file" accept=".txt" className="hidden" onChange={handleChange} />
            <div className="text-3xl mb-2">📄</div>
            {uploading ? (
              <p className="text-sm text-blue-400">上传中...</p>
            ) : (
              <>
                <p className="text-sm text-gray-300 font-medium">拖拽或点击上传 cookies.txt</p>
                <p className="text-xs text-gray-500 mt-1">从浏览器导出的 Netscape 格式 cookies 文件</p>
              </>
            )}
          </div>

          {message && (
            <div className={"mt-4 p-3 rounded-lg text-sm " +
              (message.type === "success" ? "bg-green-900/30 border border-green-800 text-green-400" : "bg-red-900/30 border border-red-800 text-red-400")}>
              {message.text}
            </div>
          )}

          <div className="mt-4 p-3 bg-gray-800/50 rounded-lg">
            <p className="text-xs text-gray-400 font-medium mb-1">如何获取 cookies.txt</p>
            <ol className="text-xs text-gray-500 space-y-1 list-decimal list-inside">
              <li>浏览器安装 "Get cookies.txt LOCALLY" 扩展</li>
              <li>在 YouTube 登录状态下访问任意视频页面</li>
              <li>点击扩展图标，导出 cookies.txt</li>
            </ol>
          </div>
        </div>
        {isOwner && (
          <div className="mt-8">
            <UserRoleManager />
          </div>
        )}
      </div>
    </div>
  );
}
