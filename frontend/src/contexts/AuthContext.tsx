import { createContext, useContext, useState, useEffect, type ReactNode } from "react";
import { api } from "@/lib/api";

interface AuthState {
  username: string | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [username, setUsername] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.get<{ username: string }>("/auth/me")
      .then((data) => setUsername(data.username))
      .catch(() => setUsername(null))
      .finally(() => setLoading(false));
  }, []);

  const login = async (user: string, password: string) => {
    await api.post("/auth/login", { username: user, password });
    setUsername(user);
  };

  const logout = () => {
    setUsername(null);
    window.location.href = "/login";
  };

  return (
    <AuthContext.Provider value={{ username, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
