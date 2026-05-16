import { createClient } from "@supabase/supabase-js";

// Vite exposes env vars via import.meta.env — must be prefixed with VITE_
// Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to frontend/.env
const supabaseUrl = import.meta.env.VITE_SUPABASE_URL as string;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY as string;

if (!supabaseUrl || !supabaseAnonKey) {
  throw new Error(
    "Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY in environment",
  );
}

// We use this client for storage helpers and future real-time features.
// Auth itself is handled by our FastAPI backend (which calls Supabase Admin API).
export const supabase = createClient(supabaseUrl, supabaseAnonKey, {
  auth: {
    // Disable auto-refresh since we manage the token ourselves via FastAPI.
    autoRefreshToken: false,
    persistSession: false,
  },
});
