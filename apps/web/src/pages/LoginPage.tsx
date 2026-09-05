import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/hooks/useAuth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Loader2, Diamond } from "lucide-react";

export default function LoginPage() {
  const { signInWithPassword, signUp, verifyOtp } = useAuth();
  const navigate = useNavigate();
  const [isRegister, setIsRegister] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [pendingVerify, setPendingVerify] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [success, setSuccess] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);

    if (!email || !password) {
      setError("请填写邮箱和密码");
      return;
    }
    if (isRegister && password !== confirmPassword) {
      setError("两次密码不一致");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }

    setLoading(true);
    try {
      if (isRegister) {
        console.log("[login] signUp start");
        const { error: err } = await signUp(email, password);
        console.log("[login] signUp done, error:", err);
        if (err) {
          setError(err);
        } else {
          setSuccess("验证码已发送到你的邮箱，请查收并输入。");
          setPendingVerify(true);
        }
      } else {
        console.log("[login] signIn start");
        const { error: err } = await signInWithPassword(email, password);
        console.log("[login] signIn done, error:", err);
        if (err) {
          setError(err);
        } else {
          console.log("[login] navigating to /");
          navigate("/");
        }
      }
    } catch (ex: any) {
      console.error("[login] exception:", ex);
      setError(ex?.message || "登录失败，请重试");
    } finally {
      setLoading(false);
      console.log("[login] loading set to false");
    }
  };

  const handleVerify = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!otpCode.trim()) {
      setError("请输入验证码");
      return;
    }
    setLoading(true);
    const { error: err } = await verifyOtp(email, otpCode.trim());
    if (err) {
      setError(err);
    } else {
      // After OTP verification, sign in with password
      const { error: signInErr } = await signInWithPassword(email, password);
      if (signInErr) {
        setSuccess("邮箱验证成功！请登录。");
        setPendingVerify(false);
        setIsRegister(false);
      } else {
        navigate("/");
      }
    }
    setLoading(false);
  };

  const handleGuest = () => {
    navigate("/");
  };

  return (
    <div className="flex-1 flex items-center justify-center p-4 relative overflow-hidden">
      {/* Warm gradient background */}
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-0 left-0 w-full h-full bg-gradient-to-br from-orange-50 via-amber-50/60 to-rose-50/40" />
        <div className="absolute top-1/4 -left-20 w-[500px] h-[500px] rounded-full bg-gradient-to-br from-orange-200/30 via-amber-100/25 to-yellow-200/15 blur-3xl" />
        <div className="absolute bottom-1/4 -right-20 w-[450px] h-[450px] rounded-full bg-gradient-to-tl from-rose-200/25 via-orange-100/20 to-amber-200/20 blur-3xl" />
        <div className="absolute top-10 right-1/4 w-[300px] h-[300px] rounded-full bg-gradient-to-b from-yellow-100/25 to-orange-100/10 blur-3xl" />
      </div>

      <div className="w-full max-w-sm space-y-8 relative z-10">
        {/* Brand */}
        <div className="text-center space-y-3">
          <div className="inline-flex items-center justify-center w-14 h-14 rounded-2xl bg-gradient-to-br from-orange-400 to-amber-500 shadow-lg shadow-orange-300/30 mb-2">
            <Diamond className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight text-foreground">SenseClip</h1>
          <p className="text-sm text-muted-foreground">基于视频资料的 AI 问答引擎</p>
        </div>

        {/* Card */}
        <div className="space-y-4 bg-card/90 backdrop-blur-sm border border-border/50 rounded-2xl p-6 shadow-lg shadow-orange-100/20">
          {pendingVerify ? (
            /* OTP verification form */
            <form onSubmit={handleVerify} className="space-y-3">
              <p className="text-sm text-muted-foreground text-center">
                验证码已发送到 <span className="font-medium text-foreground">{email}</span>
              </p>
              <Input
                type="text"
                placeholder="输入 6 位验证码"
                value={otpCode}
                onChange={(e) => setOtpCode(e.target.value)}
                className="h-12 text-center text-lg tracking-[0.5em] rounded-xl font-mono"
                maxLength={6}
                autoFocus
              />

              {error && <p className="text-xs text-destructive">{error}</p>}
              {success && <p className="text-xs text-emerald-600">{success}</p>}

              <Button type="submit" className="w-full h-10 text-sm rounded-xl" disabled={loading}>
                {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                验证并登录
              </Button>
              <div className="flex justify-between">
                <button
                  type="button"
                  className="text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => { setPendingVerify(false); setError(null); setSuccess(null); }}
                >
                  返回
                </button>
                <button
                  type="button"
                  className="text-xs text-primary hover:underline"
                  onClick={async () => {
                    setError(null);
                    const { error: err } = await signUp(email, password);
                    if (err) setError(err);
                    else setSuccess("验证码已重新发送");
                  }}
                >
                  重新发送
                </button>
              </div>
            </form>
          ) : (
            /* Login / Register form */
            <>
              <form onSubmit={handleSubmit} className="space-y-3">
                <Input
                  type="email"
                  placeholder="邮箱"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-10 text-sm rounded-xl"
                />
                <Input
                  type="password"
                  placeholder="密码"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="h-10 text-sm rounded-xl"
                />
                {isRegister && (
                  <Input
                    type="password"
                    placeholder="确认密码"
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    className="h-10 text-sm rounded-xl"
                  />
                )}

                {error && <p className="text-xs text-destructive">{error}</p>}
                {success && <p className="text-xs text-emerald-600">{success}</p>}

                <Button type="submit" className="w-full h-10 text-sm rounded-xl" disabled={loading}>
                  {loading && <Loader2 className="h-4 w-4 mr-2 animate-spin" />}
                  {isRegister ? "注册" : "登录"}
                </Button>
              </form>

              <p className="text-center text-xs text-muted-foreground">
                {isRegister ? "已有账号？" : "没有账号？"}
                <button
                  className="text-primary hover:underline ml-1"
                  onClick={() => {
                    setIsRegister(!isRegister);
                    setError(null);
                    setSuccess(null);
                  }}
                >
                  {isRegister ? "登录" : "注册"}
                </button>
              </p>
            </>
          )}
        </div>

        {/* Guest */}
        <p className="text-center text-xs text-muted-foreground">
          <button
            className="text-muted-foreground hover:text-foreground transition-colors"
            onClick={handleGuest}
          >
            不想注册？以访客身份体验 →
          </button>
        </p>
      </div>
    </div>
  );
}
