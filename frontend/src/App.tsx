import { useState, useCallback } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/pages/DashboardPage";
import DocumentsPage from "@/pages/DocumentsPage";
import DocumentDetailPage from "@/pages/DocumentDetailPage";
import ExportPage from "@/pages/ExportPage";
import SettingsPage from "@/pages/SettingsPage";
import Sidebar from "@/components/Sidebar";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { username, loading } = useAuth();
  if (loading) return (
    <div className="flex items-center justify-center h-screen bg-[#f2f4f6]">
      <div className="flex flex-col items-center gap-3">
        <div className="w-10 h-10 bg-primary flex items-center justify-center rounded-xl">
          <span className="material-symbols-outlined text-white" style={{ fontVariationSettings: "'FILL' 1" }}>receipt_long</span>
        </div>
        <p className="text-sm font-medium text-[#43474c]">Loading...</p>
      </div>
    </div>
  );
  if (!username) return <Navigate to="/login" />;
  return <>{children}</>;
}

function AppLayout({ children }: { children: React.ReactNode }) {
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [headerSearch, setHeaderSearch] = useState(searchParams.get("search") || "");

  const handleHeaderSearch = useCallback((e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" && headerSearch.trim()) {
      navigate(`/documents?search=${encodeURIComponent(headerSearch.trim())}`);
    }
  }, [headerSearch, navigate]);

  return (
    <div className="min-h-screen bg-[#f2f4f6]">
      <Sidebar mobileOpen={mobileMenuOpen} onMobileClose={() => setMobileMenuOpen(false)} />

      {/* Top header — only visible on mobile for hamburger */}
      <header className="fixed top-0 right-0 left-0 md:left-64 z-40 flex justify-between items-center px-4 md:px-6 h-16 glass-header shadow-[0_2px_8px_rgba(25,28,30,0.04)] font-body text-sm">
        {/* Mobile: hamburger */}
        <button
          className="md:hidden p-2 text-[#43474c] hover:text-primary transition-colors"
          onClick={() => setMobileMenuOpen(true)}
        >
          <span className="material-symbols-outlined">menu</span>
        </button>

        {/* Search bar */}
        <div className="flex items-center gap-4 flex-1 md:flex-initial md:w-auto">
          <div className="relative w-full max-w-md hidden md:block">
            <span className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-[#74777d] text-lg">search</span>
            <input
              className="w-full pl-10 pr-4 py-2 bg-[#eceef0] border-none rounded-lg text-sm focus:ring-2 focus:ring-primary/20 placeholder:text-[#74777d] outline-none transition-all"
              placeholder="Search documents, vendors..."
              value={headerSearch}
              onChange={(e) => setHeaderSearch(e.target.value)}
              onKeyDown={handleHeaderSearch}
            />
          </div>
        </div>

        {/* Right side */}
        <div className="flex items-center gap-3">
          <button className="p-2 text-[#43474c] hover:text-primary transition-colors opacity-80 hover:opacity-100">
            <span className="material-symbols-outlined">notifications</span>
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="md:ml-64 pt-16 pb-20 md:pb-8 min-h-screen">
        <div className="px-4 md:px-8 py-6 max-w-7xl mx-auto">
          {children}
        </div>
      </main>
    </div>
  );
}


export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute><AppLayout><DashboardPage /></AppLayout></ProtectedRoute>} />
          <Route path="/documents" element={<ProtectedRoute><AppLayout><DocumentsPage /></AppLayout></ProtectedRoute>} />
          <Route path="/documents/:id" element={<ProtectedRoute><AppLayout><DocumentDetailPage /></AppLayout></ProtectedRoute>} />
          <Route path="/export" element={<ProtectedRoute><AppLayout><ExportPage /></AppLayout></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute><AppLayout><SettingsPage /></AppLayout></ProtectedRoute>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
