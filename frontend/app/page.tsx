"use client";

export const dynamic = "force-dynamic";

import { useState, useEffect, useRef, useCallback } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "./lib/supabase";
import type { User, RecentSearch, PinnedStock } from "./lib/supabase";
import AuthModal from "./components/AuthModal";
import Navbar from "./components/Navbar";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

const LS_KEY = "sv_recent";

function getLocalRecent(): RecentSearch[] {
  if (typeof window === "undefined") return [];
  try { return JSON.parse(localStorage.getItem(LS_KEY) ?? "[]"); }
  catch { return []; }
}

function saveLocalRecent(item: RecentSearch) {
  const prev = getLocalRecent().filter((r) => r.ticker !== item.ticker);
  localStorage.setItem(LS_KEY, JSON.stringify([item, ...prev].slice(0, 8)));
}

function ChangeBadge({ value }: { value: number | null }) {
  if (value === null) return <span style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)", fontSize: "11px" }}>—</span>;
  const pos = value >= 0;
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "2px",
        padding: "2px 6px",
        borderRadius: "3px",
        fontSize: "11px",
        fontWeight: 600,
        fontFamily: "var(--font-mono)",
        letterSpacing: "0.02em",
        background: pos ? "var(--color-positive-bg)" : "var(--color-negative-bg)",
        color: pos ? "var(--color-positive)" : "var(--color-negative)",
      }}
    >
      {pos ? "▲" : "▼"} {Math.abs(value).toFixed(2)}%
    </span>
  );
}

function StockRow({
  ticker, name, change, onPin, onUnpin, isPinned, onClick,
}: {
  ticker: string; name: string; change?: number | null;
  onPin?: () => void; onUnpin?: () => void; isPinned?: boolean; onClick: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "10px 16px",
        cursor: "pointer",
        borderBottom: "1px solid var(--color-border)",
        background: hovered ? "var(--color-surface-2)" : "transparent",
        transition: "background 0.1s",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "12px", minWidth: 0 }}>
        <div
          style={{
            width: "32px",
            height: "32px",
            borderRadius: "6px",
            background: "var(--color-accent-light)",
            color: "var(--color-accent)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: "10px",
            fontWeight: 700,
            fontFamily: "var(--font-mono)",
            flexShrink: 0,
          }}
        >
          {ticker.slice(0, 3)}
        </div>
        <div style={{ minWidth: 0 }}>
          <span
            style={{
              fontWeight: 700,
              fontSize: "13px",
              fontFamily: "var(--font-mono)",
              color: "var(--color-text-primary)",
              letterSpacing: "0.04em",
              marginRight: "8px",
            }}
          >
            {ticker}
          </span>
          <span style={{ fontSize: "12px", color: "var(--color-text-muted)" }}>{name}</span>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: 0 }}>
        <ChangeBadge value={change ?? null} />
        {(onPin || onUnpin) && (
          <button
            onClick={(e) => { e.stopPropagation(); isPinned ? onUnpin?.() : onPin?.(); }}
            title={isPinned ? "Unpin" : "Pin"}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "2px",
              opacity: hovered ? 1 : 0,
              transition: "opacity 0.15s",
              color: isPinned ? "var(--color-accent)" : "var(--color-text-muted)",
            }}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill={isPinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2">
              <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}

function SectionTable({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        borderRadius: "8px",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div
      style={{
        padding: "24px 16px",
        textAlign: "center",
        fontSize: "13px",
        color: "var(--color-text-muted)",
      }}
    >
      {text}
    </div>
  );
}

export default function HomePage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [authReady, setAuthReady] = useState(false);
  const [showAuth, setShowAuth] = useState(false);
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<RecentSearch[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  const [pins, setPins] = useState<PinnedStock[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const searchAbortRef = useRef<AbortController | null>(null);
  const supabase = createClient();

  useEffect(() => {
    if (!supabase) { setAuthReady(true); return; }
    supabase.auth.getUser().then(({ data }) => {
      setUser(data.user);
      setAuthReady(true);
    });
    const { data: listener } = supabase.auth.onAuthStateChange((_, session) => {
      setUser(session?.user ?? null);
    });
    return () => listener.subscription.unsubscribe();
  }, []);

  useEffect(() => { setRecent(getLocalRecent()); }, [user]);
  useEffect(() => {
    if (!authReady) return;
    if (!user || !supabase) { setPins([]); return; }
    supabase.from("pinned_stocks").select("*").eq("user_id", user.id).order("pinned_at", { ascending: false })
      .then(({ data }) => { if (data) setPins(data); });
  }, [user, authReady]);

  useEffect(() => {
    const q = query.trim();
    if (!q) { setSuggestions([]); setSearchLoading(false); return; }

    // Debounce 200ms
    const timer = setTimeout(() => {
      if (searchAbortRef.current) searchAbortRef.current.abort();
      const ctrl = new AbortController();
      searchAbortRef.current = ctrl;
      setSearchLoading(true);

      fetch(`${API}/api/search?q=${encodeURIComponent(q)}`, { signal: ctrl.signal })
        .then((r) => r.json())
        .then((data: { ticker: string; name: string }[]) => {
          setSuggestions(data.map((d) => ({ ticker: d.ticker, company_name: d.name, searched_at: "" })));
          setSearchLoading(false);
        })
        .catch((e) => { if (e.name !== "AbortError") setSearchLoading(false); });
    }, 200);

    return () => clearTimeout(timer);
  }, [query]);

  function handleSelect(item: RecentSearch) {
    setQuery("");
    setShowSuggestions(false);
    saveLocalRecent({ ...item, searched_at: new Date().toISOString() });
    setRecent(getLocalRecent());
    router.push(`/stock/${item.ticker}`);
  }

  async function handleSignOut() {
    if (supabase) await supabase.auth.signOut();
  }

  async function handlePin(ticker: string, company_name: string) {
    if (!user) { setShowAuth(true); return; }
    const pin: PinnedStock = { id: ticker, user_id: user.id, ticker, company_name, pinned_at: new Date().toISOString() };
    setPins((prev) => [pin, ...prev.filter((p) => p.ticker !== ticker)]);
    const supa = createClient();
    if (supa) {
      const { error } = await supa.from("pinned_stocks").upsert(
        { user_id: user.id, ticker, company_name, pinned_at: pin.pinned_at },
        { onConflict: "user_id,ticker" }
      );
      if (error) console.error("Pin save error:", error);
    }
  }

  async function handleUnpin(ticker: string) {
    setPins((prev) => prev.filter((p) => p.ticker !== ticker));
    const supa = createClient();
    if (supa && user) {
      const { error } = await supa.from("pinned_stocks").delete().eq("user_id", user.id).eq("ticker", ticker);
      if (error) console.error("Unpin error:", error);
    }
  }

  const isPinned = (ticker: string) => pins.some((p) => p.ticker === ticker);

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)" }}>
      <Navbar user={user} onSignIn={() => setShowAuth(true)} onSignOut={handleSignOut} />

      {/* Main */}
      <main style={{ maxWidth: "880px", margin: "0 auto", padding: "32px 20px 64px" }}>

        {/* Hero */}
        <div style={{ marginBottom: "28px" }}>
          <h1
            style={{
              fontSize: "22px",
              fontWeight: 800,
              color: "var(--color-text-primary)",
              letterSpacing: "-0.03em",
              marginBottom: "4px",
            }}
          >
            Market Intelligence
          </h1>
          <p style={{ fontSize: "13px", color: "var(--color-text-secondary)" }}>
            Charts · News · Reddit sentiment — search any ticker
          </p>
        </div>

        {/* Search */}
        <div style={{ position: "relative", marginBottom: "32px" }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "10px",
              padding: "0 14px",
              height: "44px",
              background: "var(--color-surface)",
              border: `1.5px solid ${showSuggestions && query ? "var(--color-border-focus)" : "var(--color-border)"}`,
              borderRadius: "8px",
              boxShadow: showSuggestions && query ? "0 0 0 3px rgba(0,82,204,0.10)" : "none",
              transition: "border-color 0.15s, box-shadow 0.15s",
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2.5">
              <circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" />
            </svg>
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={() => setShowSuggestions(true)}
              onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
              onKeyDown={(e) => { if (e.key === "Enter" && suggestions.length > 0) handleSelect(suggestions[0]); }}
              placeholder="Ticker or company — e.g. AAPL, Tesla"
              style={{
                flex: 1,
                background: "none",
                border: "none",
                outline: "none",
                fontSize: "13px",
                color: "var(--color-text-primary)",
                fontFamily: "var(--font-sans)",
              }}
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "2px", lineHeight: 1 }}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                  <path d="M18 6L6 18M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>

          {/* Dropdown */}
          {showSuggestions && suggestions.length > 0 && (
            <div
              style={{
                position: "absolute",
                left: 0,
                right: 0,
                top: "calc(100% + 6px)",
                zIndex: 20,
                background: "var(--color-surface)",
                border: "1px solid var(--color-border)",
                borderRadius: "8px",
                boxShadow: "0 8px 24px rgba(0,0,0,0.10)",
                overflow: "hidden",
              }}
            >
              {suggestions.map((s, i) => (
                <button
                  key={s.ticker}
                  onMouseDown={() => handleSelect(s)}
                  style={{
                    display: "flex",
                    width: "100%",
                    alignItems: "center",
                    gap: "12px",
                    padding: "9px 14px",
                    background: "none",
                    border: "none",
                    borderBottom: i < suggestions.length - 1 ? "1px solid var(--color-border)" : "none",
                    cursor: "pointer",
                    textAlign: "left",
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "var(--color-surface-2)")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "none")}
                >
                  <div
                    style={{
                      width: "28px",
                      height: "28px",
                      borderRadius: "4px",
                      background: "var(--color-accent-light)",
                      color: "var(--color-accent)",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: "9px",
                      fontWeight: 700,
                      fontFamily: "var(--font-mono)",
                      flexShrink: 0,
                    }}
                  >
                    {s.ticker.slice(0, 3)}
                  </div>
                  <div>
                    <span style={{ fontWeight: 700, fontSize: "12px", fontFamily: "var(--font-mono)", color: "var(--color-text-primary)", letterSpacing: "0.04em", marginRight: "8px" }}>
                      {s.ticker}
                    </span>
                    <span style={{ fontSize: "12px", color: "var(--color-text-muted)" }}>{s.company_name}</span>
                  </div>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Two-column grid */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "20px" }}>

          {/* Recent */}
          <div>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "8px" }}>
              <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                Recent
              </span>
              {recent.length > 0 && (
                <button
                  onClick={() => { localStorage.removeItem(LS_KEY); setRecent([]); }}
                  style={{ fontSize: "11px", color: "var(--color-text-muted)", background: "none", border: "none", cursor: "pointer" }}
                >
                  Clear
                </button>
              )}
            </div>
            <SectionTable>
              {recent.length === 0
                ? <EmptyRow text="No recent searches" />
                : recent.slice(0, 5).map((r) => (
                  <StockRow
                    key={r.ticker}
                    ticker={r.ticker}
                    name={r.company_name}
                    onPin={() => handlePin(r.ticker, r.company_name)}
                    onUnpin={() => handleUnpin(r.ticker)}
                    isPinned={isPinned(r.ticker)}
                    onClick={() => handleSelect(r)}
                  />
                ))
              }
            </SectionTable>
          </div>

          {/* Watchlist */}
          <div>
            <div style={{ marginBottom: "8px" }}>
              <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                Watchlist
              </span>
            </div>
            <SectionTable>
              {!user ? (
                <div style={{ padding: "20px 16px", cursor: "pointer" }} onClick={() => setShowAuth(true)}>
                  <p style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text-primary)", marginBottom: "4px" }}>
                    Sign in to save stocks
                  </p>
                  <p style={{ fontSize: "12px", color: "var(--color-text-muted)", marginBottom: "12px" }}>
                    Track your favourite tickers
                  </p>
                  <button
                    style={{
                      padding: "5px 14px",
                      fontSize: "12px",
                      fontWeight: 600,
                      borderRadius: "5px",
                      border: "none",
                      background: "var(--color-accent)",
                      color: "#fff",
                      cursor: "pointer",
                    }}
                  >
                    Sign in
                  </button>
                </div>
              ) : pins.length === 0 ? (
                <EmptyRow text="Hover a stock → click ★ to add" />
              ) : (
                pins.map((p) => (
                  <StockRow
                    key={p.ticker}
                    ticker={p.ticker}
                    name={p.company_name}
                    onUnpin={() => handleUnpin(p.ticker)}
                    isPinned
                    onClick={() => handleSelect({ ticker: p.ticker, company_name: p.company_name, searched_at: "" })}
                  />
                ))
              )}
            </SectionTable>
          </div>
        </div>
      </main>

      {showAuth && <AuthModal onClose={() => setShowAuth(false)} />}
    </div>
  );
}
