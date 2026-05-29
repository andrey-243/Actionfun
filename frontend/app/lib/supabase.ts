import { createClient as _createClient } from "@supabase/supabase-js";

export type { User, Session } from "@supabase/supabase-js";

export interface PinnedStock {
  id: string;
  user_id: string;
  ticker: string;
  company_name: string;
  pinned_at: string;
}

export interface RecentSearch {
  ticker: string;
  company_name: string;
  searched_at: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyClient = ReturnType<typeof _createClient<any>>;
let _instance: AnyClient | null = null;

export function createClient(): AnyClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL ?? "";
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY ?? "";
  if (!url || !key || url === "your_supabase_url") return null;
  if (!_instance) {
    _instance = _createClient<any>(url, key);
  }
  return _instance;
}
