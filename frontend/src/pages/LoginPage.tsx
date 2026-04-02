import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export default function LoginPage() {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const { login } = useAuth();
  const navigate = useNavigate();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    try {
      await login(username, password);
      navigate("/");
    } catch {
      setError("Invalid credentials");
    }
  };

  return (
    <div className="flex min-h-screen bg-background">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 cta-gradient flex-col items-center justify-center p-12 relative overflow-hidden">
        <div className="relative z-10 text-white text-center">
          <div className="w-16 h-16 bg-card/10 rounded-2xl flex items-center justify-center mb-6 mx-auto shadow-lg">
            <span className="material-symbols-outlined text-white text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>receipt_long</span>
          </div>
          <h1 className="text-4xl font-headline font-extrabold mb-3 tracking-tight">Receiptory</h1>
          <p className="text-white/70 text-lg font-medium max-w-xs mx-auto leading-relaxed">
            Precision document management for financial clarity.
          </p>
          <div className="mt-10 grid grid-cols-3 gap-4 text-center">
            {[
              { icon: "psychology", label: "AI Extraction" },
              { icon: "folder_zip",  label: "Smart Export" },
              { icon: "cloud_sync",  label: "Auto Backup" },
            ].map((f) => (
              <div key={f.label} className="bg-card/10 rounded-xl p-4">
                <span className="material-symbols-outlined text-white/80 block mb-1">{f.icon}</span>
                <span className="text-xs font-semibold text-white/80">{f.label}</span>
              </div>
            ))}
          </div>
        </div>
        {/* Background decoration */}
        <div className="absolute -bottom-16 -right-16 w-64 h-64 rounded-full bg-card/5" />
        <div className="absolute -top-8 -left-8 w-48 h-48 rounded-full bg-card/5" />
      </div>

      {/* Right panel — login form */}
      <div className="flex-1 flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="flex items-center gap-3 mb-8 lg:hidden">
            <div className="w-10 h-10 bg-primary flex items-center justify-center rounded-xl">
              <span className="material-symbols-outlined text-white" style={{ fontVariationSettings: "'FILL' 1" }}>receipt_long</span>
            </div>
            <div>
              <h1 className="text-xl font-headline font-bold text-primary">Receiptory</h1>
              <p className="text-[10px] uppercase tracking-widest text-muted-foreground font-bold">Precision Management</p>
            </div>
          </div>

          <div className="bg-card rounded-2xl shadow-[0_8px_32px_rgba(25,28,30,0.06)] p-8">
            <h2 className="text-2xl font-headline font-extrabold text-primary mb-1 tracking-tight">Sign in</h2>
            <p className="text-sm text-muted-foreground mb-6">Enter your credentials to access Receiptory</p>

            <form onSubmit={handleSubmit} className="space-y-5">
              <div className="space-y-1.5">
                <Label htmlFor="username" className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                  Username
                </Label>
                <Input
                  id="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  className="bg-muted border-none rounded-lg h-11 focus-visible:ring-2 focus-visible:ring-primary/30"
                  placeholder="admin"
                  autoComplete="username"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password" className="text-xs font-bold text-muted-foreground uppercase tracking-wider">
                  Password
                </Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="bg-muted border-none rounded-lg h-11 focus-visible:ring-2 focus-visible:ring-primary/30"
                  placeholder="••••••••"
                  autoComplete="current-password"
                />
              </div>

              {error && (
                <div className="flex items-center gap-2 bg-[#ffdad6] text-[#93000a] rounded-lg px-3 py-2 text-sm font-medium">
                  <span className="material-symbols-outlined text-sm">error</span>
                  {error}
                </div>
              )}

              <Button
                type="submit"
                className="w-full h-11 cta-gradient text-white font-bold rounded-xl shadow-lg hover:opacity-90 active:scale-[0.98] transition-all border-0"
              >
                Sign In
              </Button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}
