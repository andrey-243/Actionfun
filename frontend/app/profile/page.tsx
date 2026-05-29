"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { createClient } from "../lib/supabase";
import { useCurrencyPref, type Currency } from "../lib/useCurrencyPref";
import type { User } from "../lib/supabase";

interface PinnedStock { ticker: string; company_name: string; }

function Toggle({ value, onChange }: { value: boolean; onChange: (v: boolean) => void }) {
  return (
    <div
      onClick={() => onChange(!value)}
      style={{
        width: "40px", height: "22px", borderRadius: "11px", cursor: "pointer",
        background: value ? "var(--color-accent)" : "var(--color-border)",
        position: "relative", transition: "background 0.2s", flexShrink: 0,
      }}
    >
      <div style={{
        position: "absolute", top: "3px",
        left: value ? "21px" : "3px",
        width: "16px", height: "16px", borderRadius: "50%",
        background: "#fff", transition: "left 0.2s",
      }} />
    </div>
  );
}

export default function ProfilePage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [pins, setPins] = useState<PinnedStock[]>([]);
  const [notifyEmail, setNotifyEmailState] = useState(true);
  const [digestTickers, setDigestTickers] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);
  const { currency, setCurrency } = useCurrencyPref();

  useEffect(() => {
    const supa = createClient();
    if (!supa) return;
    supa.auth.getUser().then(async ({ data }) => {
      const u = data.user;
      setUser(u);
      if (!u) return;

      // Load pins
      const { data: pinData } = await supa.from("pinned_stocks").select("ticker, company_name").eq("user_id", u.id);
      if (pinData) setPins(pinData);

      // Load preferences
      const { data: prefs } = await supa.from("user_preferences").select("notify_email, digest_tickers").eq("user_id", u.id).single();
      if (prefs) {
        setNotifyEmailState(prefs.notify_email ?? true);
        setDigestTickers(new Set(prefs.digest_tickers ?? []));
      } else {
        // Default: all tickers selected
        if (pinData) setDigestTickers(new Set(pinData.map((p: PinnedStock) => p.ticker)));
      }
    });
  }, []);

  // When pins load and no digest_tickers preference exists yet, select all by default
  useEffect(() => {
    if (pins.length > 0 && digestTickers.size === 0) {
      setDigestTickers(new Set(pins.map((p) => p.ticker)));
    }
  }, [pins]);

  async function savePrefs(updates: { notify_email?: boolean; digest_tickers?: string[] }) {
    const supa = createClient();
    if (!supa || !user) return;
    setSaving(true);
    await supa.from("user_preferences").upsert({ user_id: user.id, ...updates, updated_at: new Date().toISOString() });
    setSaving(false);
  }

  async function handleNotifyEmail(val: boolean) {
    setNotifyEmailState(val);
    await savePrefs({ notify_email: val });
  }

  async function toggleDigestTicker(ticker: string) {
    const next = new Set(digestTickers);
    if (next.has(ticker)) next.delete(ticker); else next.add(ticker);
    setDigestTickers(next);
    await savePrefs({ digest_tickers: [...next] });
  }

  async function handleSignOut() {
    const supa = createClient();
    if (supa) await supa.auth.signOut();
    router.push("/");
  }

  const currencyOptions: { value: Currency; label: string; desc: string }[] = [
    { value: "USD", label: "USD — $", desc: "Dollar américain" },
    { value: "EUR", label: "EUR — €", desc: "Euro (taux BCE via Frankfurter)" },
  ];

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)" }}>
      <header style={{ background: "var(--color-surface)", borderBottom: "1px solid var(--color-border)", position: "sticky", top: 0, zIndex: 10 }}>
        <div style={{ maxWidth: "720px", margin: "0 auto", padding: "0 20px", height: "52px", display: "flex", alignItems: "center", gap: "12px" }}>
          <button onClick={() => router.push("/")} style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text-muted)", padding: "4px", display: "flex", alignItems: "center" }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M19 12H5M12 19l-7-7 7-7" /></svg>
          </button>
          <span style={{ fontWeight: 700, fontSize: "15px", color: "var(--color-text-primary)", letterSpacing: "-0.02em" }}>Profile</span>
          {saving && <span style={{ marginLeft: "auto", fontSize: "11px", color: "var(--color-text-muted)" }}>Saving…</span>}
        </div>
      </header>

      <main style={{ maxWidth: "720px", margin: "0 auto", padding: "24px 20px 80px" }}>

        {/* Shortcuts */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "28px" }}>
          {[
            { label: "Mes news", icon: "📰", path: "/my-news", desc: "Digest du jour" },
            { label: "Watchlist", icon: "⭐", path: "/", desc: `${pins.length} action${pins.length !== 1 ? "s" : ""} suivie${pins.length !== 1 ? "s" : ""}` },
          ].map((s) => (
            <button key={s.path} onClick={() => router.push(s.path)} style={{
              display: "flex", alignItems: "center", gap: "12px", padding: "14px 16px",
              background: "var(--color-surface)", border: "1px solid var(--color-border)",
              borderRadius: "8px", cursor: "pointer", textAlign: "left",
            }}>
              <span style={{ fontSize: "20px" }}>{s.icon}</span>
              <div>
                <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text-primary)" }}>{s.label}</div>
                <div style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>{s.desc}</div>
              </div>
            </button>
          ))}
        </div>

        {/* Account */}
        <div style={{ marginBottom: "28px" }}>
          <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "10px" }}>Account</div>
          <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", padding: "16px 18px" }}>
            {user ? (
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div>
                  <div style={{ fontSize: "14px", fontWeight: 600, color: "var(--color-text-primary)" }}>{user.email?.split("@")[0]}</div>
                  <div style={{ fontSize: "12px", color: "var(--color-text-muted)", marginTop: "2px" }}>{user.email}</div>
                </div>
                <button onClick={handleSignOut} style={{ padding: "5px 14px", fontSize: "12px", fontWeight: 600, borderRadius: "5px", border: "1px solid var(--color-border)", background: "transparent", color: "var(--color-text-secondary)", cursor: "pointer" }}>
                  Sign out
                </button>
              </div>
            ) : (
              <div style={{ fontSize: "13px", color: "var(--color-text-muted)" }}>Non connecté — préférences sauvegardées localement seulement.</div>
            )}
          </div>
        </div>

        {/* Notifications */}
        {user && (
          <div style={{ marginBottom: "28px" }}>
            <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "10px" }}>Notifications</div>
            <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>

              {/* Email toggle */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 18px", borderBottom: pins.length > 0 ? "1px solid var(--color-border)" : "none" }}>
                <div>
                  <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text-primary)" }}>Digest quotidien par email</div>
                  <div style={{ fontSize: "12px", color: "var(--color-text-muted)", marginTop: "2px" }}>Un seul email EOD avec toutes tes actions</div>
                </div>
                <Toggle value={notifyEmail} onChange={handleNotifyEmail} />
              </div>

              {/* Per-ticker selection */}
              {pins.length > 0 && (
                <div style={{ padding: "12px 18px" }}>
                  <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginBottom: "10px" }}>
                    Actions incluses dans le digest
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                    {pins.map((p) => {
                      const active = digestTickers.has(p.ticker);
                      return (
                        <button
                          key={p.ticker}
                          onClick={() => toggleDigestTicker(p.ticker)}
                          style={{
                            padding: "4px 12px", borderRadius: "4px", fontSize: "12px", fontWeight: 700,
                            fontFamily: "var(--font-mono)", cursor: "pointer", border: "none",
                            background: active ? "var(--color-accent)" : "var(--color-surface-2)",
                            color: active ? "#fff" : "var(--color-text-muted)",
                            opacity: !notifyEmail ? 0.4 : 1,
                            transition: "all 0.15s",
                          }}
                          disabled={!notifyEmail}
                        >
                          {p.ticker}
                        </button>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Currency */}
        <div>
          <div style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)", marginBottom: "10px" }}>Display Currency</div>
          <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
            {currencyOptions.map((opt, i) => {
              const selected = currency === opt.value;
              return (
                <div key={opt.value} onClick={() => setCurrency(opt.value)} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "14px 18px", cursor: "pointer",
                  borderBottom: i < currencyOptions.length - 1 ? "1px solid var(--color-border)" : "none",
                  background: selected ? "var(--color-accent-light)" : "transparent",
                  transition: "background 0.1s",
                }}>
                  <div>
                    <div style={{ fontSize: "13px", fontWeight: 600, color: selected ? "var(--color-accent)" : "var(--color-text-primary)", fontFamily: "var(--font-mono)" }}>{opt.label}</div>
                    <div style={{ fontSize: "12px", color: "var(--color-text-muted)", marginTop: "2px" }}>{opt.desc}</div>
                  </div>
                  {selected && <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-accent)" strokeWidth="2.5"><polyline points="20 6 9 17 4 12" /></svg>}
                </div>
              );
            })}
          </div>
          {!user && <p style={{ fontSize: "11px", color: "var(--color-text-muted)", marginTop: "8px" }}>Connecte-toi pour synchroniser cette préférence sur tous tes appareils.</p>}
        </div>
      </main>
    </div>
  );
}
