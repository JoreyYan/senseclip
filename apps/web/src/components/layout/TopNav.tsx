import { Link, useLocation, useNavigate } from "react-router-dom";
import { MessageSquare, GitGraph, Users, User, LogOut, LogIn, Video, Upload, Settings, ChevronDown, CreditCard } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/hooks/useAuth";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const ROLE_ZH: Record<string, string> = {
  owner: "管理员",
  editor: "编辑",
  viewer: "用户",
  guest: "访客",
};

function canEdit(role: string) {
  return role === "owner" || role === "editor";
}

export function TopNav() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, role, credits, signOut } = useAuth();

  const navItems = [
    { to: "/", label: "对话", icon: MessageSquare },
    { to: "/square", label: "广场", icon: Users },
    { to: "/person-network", label: "群英图", icon: GitGraph },
  ];

  return (
    <header className="h-11 shrink-0 flex items-center justify-between px-4 border-b border-border/30 bg-background">
      <div className="flex items-center gap-6">
        <Link to="/" className="text-sm font-bold tracking-tight text-foreground">
          SenseClip
        </Link>
        <nav className="flex items-center gap-1">
          {navItems.map(({ to, label, icon: Icon }) => {
            const isActive = location.pathname === to;
            return (
              <Link
                key={to}
                to={to}
                className={cn(
                  "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                  isActive
                    ? "bg-accent text-foreground"
                    : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {label}
              </Link>
            );
          })}

          {/* Admin menu for editor/owner */}
          {canEdit(role) && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button
                  className={cn(
                    "flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors",
                    ["/library", "/ingest", "/settings"].includes(location.pathname)
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:text-foreground hover:bg-accent/50"
                  )}
                >
                  管理
                  <ChevronDown className="h-3 w-3" />
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="w-36">
                <DropdownMenuItem onClick={() => navigate("/library")}>
                  <Video className="h-3.5 w-3.5 mr-2" />
                  <span className="text-xs">视频库</span>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => navigate("/ingest")}>
                  <Upload className="h-3.5 w-3.5 mr-2" />
                  <span className="text-xs">提交视频</span>
                </DropdownMenuItem>
                {role === "owner" && (
                  <DropdownMenuItem onClick={() => navigate("/settings")}>
                    <Settings className="h-3.5 w-3.5 mr-2" />
                    <span className="text-xs">设置</span>
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </nav>
      </div>

      {/* Right: user menu */}
      {user ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full">
              <div className="h-6 w-6 rounded-full bg-primary/20 flex items-center justify-center text-[10px] font-bold text-primary">
                {user.email?.charAt(0).toUpperCase() || "U"}
              </div>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-48">
            <div className="px-2 py-1.5">
              <p className="text-xs font-medium truncate">{user.email}</p>
              <p className="text-[10px] text-muted-foreground">{ROLE_ZH[role] || role}</p>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={() => navigate("/billing")}>
              <CreditCard className="h-3.5 w-3.5 mr-2" />
              <span className="text-xs">积分: {credits ?? "..."}</span>
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={async () => {
                await signOut();
                navigate("/login");
              }}
            >
              <LogOut className="h-3.5 w-3.5 mr-2" />
              <span className="text-xs">退出登录</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <Button
          variant="ghost"
          size="sm"
          className="h-7 text-xs gap-1.5"
          onClick={() => navigate("/login")}
        >
          <LogIn className="h-3.5 w-3.5" />
          登录
        </Button>
      )}
    </header>
  );
}
