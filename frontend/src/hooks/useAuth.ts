import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, ApiError } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AuthState {
  token: string | null;
  authorId: string | null;
  isLoading: boolean;
}

interface AuthContextValue extends AuthState {
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
}

// ── Storage keys ──────────────────────────────────────────────────────────────

const TOKEN_KEY = "rp_token";
const AUTHOR_ID_KEY = "rp_author_id";

// ── Context ───────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    token: null,
    authorId: null,
    isLoading: true, // true until we've checked localStorage
  });

  // Hydrate from localStorage on mount
  useEffect(() => {
    const token = localStorage.getItem(TOKEN_KEY);
    const authorId = localStorage.getItem(AUTHOR_ID_KEY);
    setState({ token, authorId, isLoading: false });
  }, []);

  const persist = useCallback((token: string, authorId: string) => {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(AUTHOR_ID_KEY, authorId);
    setState({ token, authorId, isLoading: false });
  }, []);

  const login = useCallback(
    async (email: string, password: string) => {
      const res = await api.auth.login(email, password);
      persist(res.access_token, res.author_id);
    },
    [persist],
  );

  const register = useCallback(
    async (email: string, password: string) => {
      await api.auth.register(email, password);
      // After registering, automatically log in so the user gets a token.
      const res = await api.auth.login(email, password);
      persist(res.access_token, res.author_id);
    },
    [persist],
  );

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(AUTHOR_ID_KEY);
    setState({ token: null, authorId: null, isLoading: false });
  }, []);

  const value = useMemo(
    () => ({ ...state, login, register, logout }),
    [state, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}

// ── Helper: throw on 401 and log out ─────────────────────────────────────────

export function isUnauthorized(err: unknown): err is ApiError {
  return err instanceof ApiError && err.status === 401;
}
