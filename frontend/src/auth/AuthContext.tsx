import { createContext, useContext, useState, useEffect, type ReactNode } from "react";
import { login as apiLogin, logout as apiLogout, register as apiRegister, refreshSession, getCurrentUser, setMemoryToken, getMemoryToken } from "../api/client";
import type { Role, Session } from "../types";

interface AuthValue { 
  session: Session | null; 
  isVerified: boolean;
  login: (email: string, password: string) => Promise<Session>; 
  register: (name: string, email: string, password: string, role: "advertiser" | "publisher") => Promise<Session>; 
  logout: () => void; 
  refreshUser: () => Promise<Session | null>;
}

export const AuthContext = createContext<AuthValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) { 
  const [session, setSession] = useState<Session | null>(null);
  const [isInitializing, setIsInitializing] = useState(true);

  useEffect(() => {
    refreshSession()
      .then(sess => {
        if (sess?.user.role === "advertiser" && !sess.user.advertiser_id) {
            setMemoryToken(null);
            setSession(null);
            return;
        }
        if (sess?.user.role === "publisher" && !sess.user.publisher_id) {
            setMemoryToken(null);
            setSession(null);
            return;
        }
        setMemoryToken(sess.token);
        setSession(sess);
      })
      .catch(() => {
        setMemoryToken(null);
        setSession(null);
      })
      .finally(() => setIsInitializing(false));
  }, []);

  const login = async (email: string, password: string) => {
    const sess = await apiLogin(email, password);
    setMemoryToken(sess.token);
    setSession(sess);
    return sess;
  };

  const register = async (name: string, email: string, password: string, role: "advertiser" | "publisher") => {
    const sess = await apiRegister(name, email, password, role);
    setMemoryToken(sess.token);
    try {
      await apiLogout();
    } finally {
      setMemoryToken(null);
      setSession(null);
    }
    return sess;
  };

  const logout = () => {
    apiLogout().catch(() => {});
    setMemoryToken(null);
    setSession(null);
  };

  const refreshUser = async (): Promise<Session | null> => {
    const currentToken = getMemoryToken();
    if (currentToken) {
      try {
        const user = await getCurrentUser();
        if (user.role === "advertiser" && !user.advertiser_id) {
          setMemoryToken(null);
          setSession(null);
          return null;
        }
        if (user.role === "publisher" && !user.publisher_id) {
          setMemoryToken(null);
          setSession(null);
          return null;
        }
        const updatedSession: Session = { user, token: currentToken };
        setSession(updatedSession);
        return updatedSession;
      } catch {
        // Fall back to refreshSession if bearer token has expired
      }
    }
    try {
      const sess = await refreshSession();
      if (sess?.user.role === "advertiser" && !sess.user.advertiser_id) {
        setMemoryToken(null);
        setSession(null);
        return null;
      }
      if (sess?.user.role === "publisher" && !sess.user.publisher_id) {
        setMemoryToken(null);
        setSession(null);
        return null;
      }
      setMemoryToken(sess.token);
      setSession(sess);
      return sess;
    } catch {
      setMemoryToken(null);
      setSession(null);
      return null;
    }
  };

  if (isInitializing) {
    return null;
  }

  const isVerified = Boolean(session?.user?.email_verified);

  return (
    <AuthContext.Provider value={{ session, isVerified, login, register, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  ); 
}

export function useAuth() { 
  const value = useContext(AuthContext); 
  if (!value) throw new Error("useAuth must be used inside AuthProvider"); 
  return value; 
}

export function roleHome(role: Role) { 
  return role === "admin" ? "/admin" : role === "advertiser" ? "/advertiser" : "/publisher"; 
}
