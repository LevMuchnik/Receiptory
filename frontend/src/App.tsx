import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import LoginPage from "@/pages/LoginPage";
import DashboardPage from "@/pages/DashboardPage";
import DocumentsPage from "@/pages/DocumentsPage";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { username, loading } = useAuth();
  if (loading) return <div className="flex items-center justify-center h-screen">Loading...</div>;
  if (!username) return <Navigate to="/login" />;
  return <>{children}</>;
}

function AppLayout({ children }: { children: React.ReactNode }) {
  const { username, logout } = useAuth();
  return (
    <div className="min-h-screen bg-background">
      <header className="border-b px-6 py-3 flex items-center justify-between">
        <nav className="flex gap-4">
          <a href="/" className="font-semibold">Receiptory</a>
          <a href="/documents" className="text-muted-foreground hover:text-foreground">Documents</a>
          <a href="/export" className="text-muted-foreground hover:text-foreground">Export</a>
          <a href="/settings" className="text-muted-foreground hover:text-foreground">Settings</a>
        </nav>
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground">{username}</span>
          <button onClick={logout} className="text-sm underline">Logout</button>
        </div>
      </header>
      <main className="p-6">{children}</main>
    </div>
  );
}

// Placeholder pages — implemented in subsequent tasks
function Placeholder({ name }: { name: string }) {
  return <div className="text-xl">{name} — coming soon</div>;
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<ProtectedRoute><AppLayout><DashboardPage /></AppLayout></ProtectedRoute>} />
          <Route path="/documents" element={<ProtectedRoute><AppLayout><DocumentsPage /></AppLayout></ProtectedRoute>} />
          <Route path="/documents/:id" element={<ProtectedRoute><AppLayout><Placeholder name="Document Detail" /></AppLayout></ProtectedRoute>} />
          <Route path="/export" element={<ProtectedRoute><AppLayout><Placeholder name="Export" /></AppLayout></ProtectedRoute>} />
          <Route path="/settings" element={<ProtectedRoute><AppLayout><Placeholder name="Settings" /></AppLayout></ProtectedRoute>} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
