"use client";

import { useState, useEffect, useCallback } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface InitStatus {
  ticker: string;
  phase: string;
  status: string;
  pending_fr: boolean;
  label: string;
  eta_seconds: number;
  done: boolean;
  error: string | null;
}

interface Props {
  ticker: string;
  lang?: string;
  onPhaseComplete?: (phase: string) => void;
}

const PHASE_ORDER = ["1m", "6m", "1y"];

function formatEta(seconds: number): string {
  if (seconds < 60) return `~${seconds}s`;
  return `~${Math.round(seconds / 60)} min`;
}

export default function ColdStartBanner({ ticker, lang = "fr", onPhaseComplete }: Props) {
  const [status, setStatus] = useState<InitStatus | null>(null);
  const [visible, setVisible] = useState(false);
  const [triggered, setTriggered] = useState(false);
  const [completedPhases, setCompletedPhases] = useState<Set<string>>(new Set());

  const triggerInit = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/ticker/${ticker}/initialize?lang=${lang}`, { method: "POST" });
      const data = await r.json();
      if (data.message === "already_initialized" || data.message === "already_running") return;
      setVisible(true);
    } catch {
      // silently fail — cold start is best-effort
    }
  }, [ticker, lang]);

  const poll = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/ticker/${ticker}/init-status?lang=${lang}`);
      const data: InitStatus = await r.json();
      setStatus(data);

      // Notifier si une phase vient de se terminer
      if (data.status === "done" && !completedPhases.has(data.phase)) {
        setCompletedPhases((prev) => new Set([...prev, data.phase]));
        onPhaseComplete?.(data.phase);
      }

      // Tout terminé → masquer la banner après 3s
      if (data.done) {
        setTimeout(() => setVisible(false), 3000);
        return false; // stop polling
      }

      return data.status === "running" || data.status === "idle";
    } catch {
      return false;
    }
  }, [ticker, lang, completedPhases, onPhaseComplete]);

  // Au mount : vérifier si cold start nécessaire
  useEffect(() => {
    if (triggered) return;
    setTriggered(true);

    fetch(`${API}/api/ticker/${ticker}/init-status?lang=${lang}`)
      .then((r) => r.json())
      .then((data: InitStatus) => {
        setStatus(data);
        if (data.phase === "uninitialized" || (data.status === "running")) {
          setVisible(true);
          if (data.phase === "uninitialized") {
            triggerInit();
          }
        }
      })
      .catch(() => {});
  }, [ticker, lang, triggered, triggerInit]);

  // Poll toutes les 10s quand visible
  useEffect(() => {
    if (!visible) return;
    const interval = setInterval(async () => {
      const shouldContinue = await poll();
      if (!shouldContinue) clearInterval(interval);
    }, 10_000);
    return () => clearInterval(interval);
  }, [visible, poll]);

  if (!visible || !status) return null;
  if (status.done) {
    return (
      <div style={{
        margin: "0 0 12px 0",
        padding: "10px 16px",
        borderRadius: "8px",
        background: "var(--color-positive-bg)",
        border: "1px solid var(--color-positive)",
        display: "flex", alignItems: "center", gap: "10px",
        fontSize: "13px", color: "var(--color-positive)",
      }}>
        ✓ Données historiques chargées
      </div>
    );
  }

  const phase = status.phase;
  const isRunning = status.status === "running";
  const phaseIdx = PHASE_ORDER.indexOf(phase);
  const label = status.label || "Chargement des données historiques";
  const eta = status.eta_seconds;

  return (
    <div style={{
      margin: "0 0 12px 0",
      padding: "12px 16px",
      borderRadius: "8px",
      background: "var(--color-surface)",
      border: "1px solid var(--color-border)",
      fontSize: "13px",
    }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "8px" }}>
        <span style={{ fontSize: "15px" }}>⏳</span>
        <span style={{ color: "var(--color-text-primary)", fontWeight: 600 }}>
          Action peu connue
        </span>
        <span style={{
          padding: "2px 8px", borderRadius: "4px", fontSize: "10px", fontWeight: 700,
          background: "rgba(255,180,0,0.15)", color: "#C8960C",
          fontFamily: "var(--font-mono)", letterSpacing: "0.06em",
        }}>
          NOUVEAU
        </span>
      </div>

      {/* Label + ETA */}
      <div style={{ color: "var(--color-text-secondary)", marginBottom: "10px" }}>
        {label}
        {isRunning && eta > 0 && (
          <span style={{ color: "var(--color-text-muted)", marginLeft: "6px" }}>
            ({formatEta(eta)})
          </span>
        )}
      </div>

      {/* Progress steps */}
      <div style={{ display: "flex", gap: "6px" }}>
        {PHASE_ORDER.map((p, i) => {
          const isDone = completedPhases.has(p) || (phaseIdx > i);
          const isCurrent = p === phase && isRunning;
          const isPending = phaseIdx < i && !isDone;
          return (
            <div key={p} style={{ display: "flex", alignItems: "center", gap: "6px" }}>
              <div style={{
                padding: "3px 10px",
                borderRadius: "4px",
                fontSize: "11px",
                fontFamily: "var(--font-mono)",
                fontWeight: isDone ? 700 : 500,
                background: isDone
                  ? "var(--color-positive-bg)"
                  : isCurrent
                  ? "rgba(255,180,0,0.15)"
                  : "var(--color-surface-2)",
                color: isDone
                  ? "var(--color-positive)"
                  : isCurrent
                  ? "#C8960C"
                  : "var(--color-text-muted)",
                display: "flex", alignItems: "center", gap: "5px",
              }}>
                {isDone ? "✓ " : isCurrent ? (
                  <span style={{ display: "inline-block", animation: "spin 1s linear infinite", fontSize: "10px" }}>◌</span>
                ) : ""}
                {p === "1m" ? "1 mois" : p === "6m" ? "6 mois" : "1 an"}
              </div>
              {i < PHASE_ORDER.length - 1 && (
                <span style={{ color: "var(--color-border)", fontSize: "10px" }}>→</span>
              )}
            </div>
          );
        })}
      </div>

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
}
