import { lazy, Suspense } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AuthProvider } from "@/hooks/useAuth";
import { TopNav } from "@/components/layout/TopNav";

const ChatPage = lazy(() => import("./pages/ChatPage"));
const Index = lazy(() => import("./pages/Index"));
const LoginPage = lazy(() => import("./pages/LoginPage"));
const NotFound = lazy(() => import("./pages/NotFound"));
const AnalysisPage = lazy(() => import("./pages/AnalysisPage"));
const IngestPage = lazy(() => import("./pages/IngestPage"));
const LibraryPage = lazy(() => import("./pages/LibraryPage"));
const SettingsPage = lazy(() => import("./pages/SettingsPage"));
const BillingPage = lazy(() => import("./pages/BillingPage"));
const SquarePage = lazy(() => import("./pages/SquarePage"));

const queryClient = new QueryClient();

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <AuthProvider>
          <div className="flex flex-col h-screen w-screen overflow-hidden">
            <TopNav />
            <Suspense fallback={<div className="flex-1 flex items-center justify-center"><div className="h-6 w-6 border-2 border-primary border-t-transparent rounded-full animate-spin" /></div>}>
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/person-network" element={<Index />} />
              <Route path="/square" element={<SquarePage />} />
              <Route path="/analysis" element={<AnalysisPage />} />
              <Route path="/ingest" element={<IngestPage />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/billing" element={<BillingPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
            </Suspense>
          </div>
        </AuthProvider>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export default App;
