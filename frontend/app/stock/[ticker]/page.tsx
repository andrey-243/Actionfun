"use client";

import { useState, useEffect, useRef, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import { useCurrencyPref } from "../../lib/useCurrencyPref";
import { useExchangeRate } from "../../lib/useExchangeRate";
import { createClient } from "../../lib/supabase";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Filler,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";
import ArticleDrawer from "../../components/ArticleDrawer";
import VideoModal from "../../components/VideoModal";
import EventDrawer from "../../components/EventDrawer";
import DogLoader from "../../components/DogLoader";
import ColdStartBanner from "../../components/ColdStartBanner";
import { NavBrand, BackButton, Avatar, HEADER_STYLE, HEADER_INNER } from "../../components/Navbar";
import type { User } from "../../lib/supabase";
import type { Article } from "../../components/ArticleDrawer";
import { sourceStyle } from "../../components/ArticleDrawer";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

function getBoundaryIndices(labels: string[], period: string): number[] {
  if (period === "1d") {
    // 5-min candles → line every 12 = every hour
    const out: number[] = [];
    for (let i = 12; i < labels.length; i += 12) out.push(i);
    return out;
  }
  const out: number[] = [];
  for (let i = 1; i < labels.length; i++) {
    const p = labels[i - 1], c = labels[i];
    let boundary = false;
    if (period === "5d")                         boundary = c !== p;
    else if (period === "1mo")                   boundary = new Date(c).getDay() === 1;
    else if (period === "6mo" || period === "1y") boundary = c.slice(0, 7) !== p.slice(0, 7);
    else if (period === "5y")                    boundary = c.slice(0, 4) !== p.slice(0, 4);
    if (boundary) out.push(i);
  }
  return out;
}

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface StockData {
  ticker: string;
  name: string;
  currency: string;
  current_price: number | null;
  previous_close: number | null;
  change_pct: number | null;
  market_cap: number | null;
  pe_ratio: number | null;
  week_52_high: number | null;
  week_52_low: number | null;
  chart: { date: string; close: number }[];
  website?: string | null;
}

interface NewsData {
  ticker: string;
  articles: Article[];
  summary: string;
  sentiment: string;
  sentiment_reason: string;
  summary_24h: string;
  sentiment_24h: string;
  sentiment_reason_24h: string;
  no_24h_news: boolean;
  cached_at: string;
}

function fmt(n: number | null, digits = 2): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtCap(n: number | null): string {
  if (!n) return "—";
  if (n >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (n >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  return n.toFixed(0);
}

function ChangeBadge({ value }: { value: number | null }) {
  if (value === null) return <span style={{ color: "var(--color-text-muted)", fontFamily: "var(--font-mono)", fontSize: "12px" }}>—</span>;
  const pos = value >= 0;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: "3px",
      padding: "3px 8px", borderRadius: "4px", fontSize: "13px", fontWeight: 700,
      fontFamily: "var(--font-mono)", letterSpacing: "0.02em",
      background: pos ? "var(--color-positive-bg)" : "var(--color-negative-bg)",
      color: pos ? "var(--color-positive)" : "var(--color-negative)",
    }}>
      {pos ? "▲" : "▼"} {Math.abs(value).toFixed(2)}%
    </span>
  );
}

function SentimentBadge({ sentiment }: { sentiment: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    BULLISH: { bg: "var(--color-positive-bg)", color: "var(--color-positive)", label: "BULLISH" },
    BEARISH: { bg: "var(--color-negative-bg)", color: "var(--color-negative)", label: "BEARISH" },
    NEUTRAL: { bg: "var(--color-surface-2)", color: "var(--color-text-secondary)", label: "NEUTRAL" },
  };
  const s = map[sentiment?.toUpperCase()] ?? map.NEUTRAL;
  return (
    <span style={{
      padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 700,
      fontFamily: "var(--font-mono)", letterSpacing: "0.08em",
      background: s.bg, color: s.color,
    }}>
      {s.label}
    </span>
  );
}

function StatBox({ label, value }: { label: string; value: string }) {
  return (
    <div style={{
      background: "var(--color-surface)",
      border: "1px solid var(--color-border)",
      borderRadius: "8px",
      padding: "14px 16px",
    }}>
      <div style={{ fontSize: "11px", color: "var(--color-text-muted)", marginBottom: "4px", letterSpacing: "0.04em" }}>
        {label}
      </div>
      <div style={{ fontSize: "14px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--color-text-primary)" }}>
        {value}
      </div>
    </div>
  );
}

function MiniSourceBadge({ name }: { name: string }) {
  const { bg, text } = sourceStyle(name);
  return (
    <span style={{
      padding: "1px 5px", borderRadius: "3px", fontSize: "9px", fontWeight: 700,
      letterSpacing: "0.04em", textTransform: "uppercase", background: bg, color: text,
    }}>
      {name.length > 12 ? name.slice(0, 12) + "…" : name}
    </span>
  );
}

function CompactArticleRow({ article, onClick }: { article: Article; onClick: () => void }) {
  const [hovered, setHovered] = useState(false);
  const ago = article.pub_date
    ? (() => {
        const diff = Date.now() - new Date(article.pub_date).getTime();
        const h = Math.floor(diff / 3600000);
        if (h < 1) return `${Math.floor(diff / 60000)}m`;
        if (h < 24) return `${h}h`;
        return `${Math.floor(h / 24)}d`;
      })()
    : "";
  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex", alignItems: "center", gap: "10px",
        padding: "7px 12px",
        cursor: "pointer",
        borderBottom: "1px solid var(--color-border)",
        background: hovered ? "var(--color-surface-2)" : "transparent",
        transition: "background 0.1s",
      }}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <span style={{ fontSize: "11px", fontWeight: 500, color: "var(--color-text-primary)", lineHeight: 1.3 }}>
          {article.title}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "4px", flexShrink: 0 }}>
        <MiniSourceBadge name={article.source} />
        {(article.extra_sources || []).slice(0, 2).map((s, i) => <MiniSourceBadge key={i} name={s.name} />)}
        {ago && <span style={{ fontSize: "10px", color: "var(--color-text-muted)", marginLeft: "4px" }}>{ago}</span>}
      </div>
    </div>
  );
}

function NewsCard({ article, onClick }: { article: Article; onClick: () => void }) {
  const [hovered, setHovered] = useState(false);
  const isVideo = article.type === "VIDEO";
  const ago = article.pub_date
    ? (() => {
        const diff = Date.now() - new Date(article.pub_date).getTime();
        const h = Math.floor(diff / 3600000);
        if (h < 1) return `${Math.floor(diff / 60000)}m ago`;
        if (h < 24) return `${h}h ago`;
        return `${Math.floor(h / 24)}d ago`;
      })()
    : "";

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex",
        gap: "14px",
        padding: "14px 16px",
        cursor: "pointer",
        borderBottom: "1px solid var(--color-border)",
        background: hovered ? "var(--color-surface-2)" : "transparent",
        transition: "background 0.1s",
      }}
    >
      {/* Thumbnail or type indicator */}
      <div style={{ flexShrink: 0, width: "72px" }}>
        {article.thumbnail ? (
          <div style={{ position: "relative", width: "72px", height: "48px", borderRadius: "6px", overflow: "hidden" }}>
            <img src={article.thumbnail} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
            {isVideo && (
              <div style={{
                position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
                background: "rgba(0,0,0,0.4)",
              }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="#fff"><polygon points="5 3 19 12 5 21 5 3" /></svg>
              </div>
            )}
          </div>
        ) : (
          <div style={{
            width: "72px", height: "48px", borderRadius: "6px",
            background: "var(--color-surface-2)", border: "1px solid var(--color-border)",
            display: "flex", alignItems: "center", justifyContent: "center",
          }}>
            {isVideo
              ? <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2"><polygon points="5 3 19 12 5 21 5 3" /></svg>
              : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
            }
          </div>
        )}
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "center", gap: "5px", marginBottom: "5px" }}>
          <MiniSourceBadge name={article.source} />
          {(article.extra_sources || []).slice(0, 2).map((s, i) => <MiniSourceBadge key={i} name={s.name} />)}
          {ago && <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>· {ago}</span>}
          {isVideo && (
            <span style={{
              fontSize: "9px", fontWeight: 700, letterSpacing: "0.06em",
              padding: "1px 5px", borderRadius: "3px",
              background: "var(--color-surface-2)", color: "var(--color-text-muted)",
            }}>
              VIDEO
            </span>
          )}
        </div>
        <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text-primary)", lineHeight: 1.4, marginBottom: "6px" }}>
          {article.title}
        </div>
        {(article.dumb_en || article.dumb_fr) && (
          <div style={{ fontSize: "12px", color: "var(--color-accent)", lineHeight: 1.5 }}>
            {article.dumb_en || article.dumb_fr}
          </div>
        )}
      </div>
    </div>
  );
}

export default function StockPage() {
  const { ticker } = useParams<{ ticker: string }>();
  const router = useRouter();
  const supaRef = useRef(createClient());

  const [stock, setStock] = useState<StockData | null>(null);
  const [news, setNews] = useState<NewsData | null>(null);
  const [stockLoading, setStockLoading] = useState(true);
  const [newsLoading, setNewsLoading] = useState(true);
  const [stockError, setStockError] = useState("");
  const [newsError, setNewsError] = useState("");

  const [selectedArticle, setSelectedArticle] = useState<Article | null>(null);
  const [selectedVideo, setSelectedVideo] = useState<Article | null>(null);
  const [chartPeriod, setChartPeriod] = useState("1d");
  const [chartLoading, setChartLoading] = useState(false);
  const [eventPoint, setEventPoint] = useState<{ date: string; changePct: number } | null>(null);
  const [isPinned, setIsPinned] = useState(false);
  const [pinLoading, setPinLoading] = useState(false);
  const [userId, setUserId] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);
  const [periodArticles, setPeriodArticles] = useState<Article[]>([]);
  const [periodSummaries, setPeriodSummaries] = useState<{type: string; year_month?: string; year?: string; week_num?: number | null; label: string; ai_summary?: string | null; article_count?: number; top_articles?: Article[]; skewed?: boolean; skewed_range?: string}[]>([]);
  const [periodLoading, setPeriodLoading] = useState(false);

  const [coldStartPhase, setColdStartPhase] = useState<string | null>(null);

  const { currency, setCurrency } = useCurrencyPref();
  const { rates } = useExchangeRate();

  function convert(value: number | null): number | null {
    if (value === null || !rates || !stock) return value;
    if (stock.currency === currency) return value;
    if (currency === "EUR") return value * rates.usdToEur;
    return value * rates.eurToUsd;
  }

  const displaySymbol = currency === "USD" ? "$" : "€";

  useEffect(() => {
    if (!ticker) return;
    const t = ticker.toUpperCase();

    setStockLoading(true);
    fetch(`${API}/api/stock/${t}?period=${chartPeriod}`)
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then((d) => { setStock(d); setStockLoading(false); })
      .catch((e) => { setStockError(e.message); setStockLoading(false); });

    setNewsLoading(true);
    fetch(`${API}/api/news/${t}`)
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then((d) => { setNews(d); setNewsLoading(false); })
      .catch((e) => { setNewsError(e.message); setNewsLoading(false); });
  }, [ticker]);

  useEffect(() => {
    if (!ticker || !stock) return;
    setChartLoading(true);
    fetch(`${API}/api/stock/${ticker.toUpperCase()}?period=${chartPeriod}`)
      .then((r) => r.json())
      .then((d) => { setStock((prev) => prev ? { ...prev, chart: d.chart } : prev); setChartLoading(false); })
      .catch(() => setChartLoading(false));
  }, [chartPeriod]);

  // Articles par période : Finnhub/archive pour 1M+, Yahoo filtré pour 24H/1W
  useEffect(() => {
    if (!ticker) return;
    const ARCHIVE_PERIODS = new Set(["1mo", "6mo", "1y", "5y"]);
    const apiPeriod = chartPeriod === "5y" ? "all" : chartPeriod;
    if (ARCHIVE_PERIODS.has(chartPeriod)) {
      setPeriodLoading(true);
      setPeriodArticles([]);
      setPeriodSummaries([]);
      fetch(`${API}/api/articles/${ticker.toUpperCase()}?period=${apiPeriod}`)
        .then((r) => r.json())
        .then((d) => {
          setPeriodArticles(d.articles ?? []);
          setPeriodSummaries(d.summaries ?? []);
          setPeriodLoading(false);
        })
        .catch(() => setPeriodLoading(false));
    } else {
      setPeriodArticles([]);
      setPeriodSummaries([]);
    }
  }, [chartPeriod, ticker]);

  useEffect(() => {
    const supa = supaRef.current;
    if (!supa || !ticker) return;
    supa.auth.getUser().then(async ({ data }) => {
      const uid = data.user?.id;
      if (!uid) return;
      setUserId(uid);
      setUser(data.user as User);
      const { data: pin } = await supa.from("pinned_stocks").select("ticker").eq("user_id", uid).eq("ticker", ticker.toUpperCase()).maybeSingle();
      setIsPinned(!!pin);
    });
  }, [ticker]);

  async function handlePin() {
    const supa = supaRef.current;
    if (!supa || !ticker || !userId) return;
    const t = ticker.toUpperCase();
    const newPinned = !isPinned;
    setIsPinned(newPinned); // optimistic
    setPinLoading(true);
    if (!newPinned) {
      const { error } = await supa.from("pinned_stocks").delete().eq("user_id", userId).eq("ticker", t);
      if (error) setIsPinned(!newPinned);
    } else {
      const companyName = stock?.name ?? t;
      const { error } = await supa.from("pinned_stocks").upsert(
        { user_id: userId, ticker: t, company_name: companyName, pinned_at: new Date().toISOString() },
        { onConflict: "user_id,ticker" }
      );
      if (error) setIsPinned(!newPinned);
    }
    setPinLoading(false);
  }

  const periodChangePct = useMemo(() => {
    if (!stock?.chart?.length || stock.chart.length < 2) return stock?.change_pct ?? null;
    const first = stock.chart[0].close;
    const last = stock.chart[stock.chart.length - 1].close;
    if (!first) return stock?.change_pct ?? null;
    return Math.round(((last - first) / first) * 10000) / 100;
  }, [stock?.chart, stock?.change_pct]);

  const displayChangePct = chartPeriod === "1d" ? (stock?.change_pct ?? null) : periodChangePct;
  const isPositive = (displayChangePct ?? 0) >= 0;

  const verticalLinesPlugin = useMemo(() => ({
    id: "verticalLines",
    beforeDraw: (chartInstance: any) => {
      if (!chartInstance.chartArea) return;
      const labels = (chartInstance.data.labels ?? []) as string[];
      const indices = getBoundaryIndices(labels, chartPeriod);
      if (!indices.length) return;
      const { ctx, chartArea: { top, bottom }, scales: { x } } = chartInstance;
      ctx.save();
      ctx.strokeStyle = "rgba(60, 58, 54, 0.7)";
      ctx.setLineDash([3, 6]);
      ctx.lineWidth = 1;
      for (const i of indices) {
        const xPos = x.getPixelForValue(i);
        ctx.beginPath();
        ctx.moveTo(xPos, top);
        ctx.lineTo(xPos, bottom);
        ctx.stroke();
      }
      ctx.restore();
    },
  }), [chartPeriod]);

  const outlierIndices = (() => {
    const data = stock?.chart ?? [];
    if (data.length < 3 || chartLoading) return new Map<number, number>();

    // Pas de dots sur 24H (5min candles = trop bruité) ni ALL (pas de news Finnhub)
    if (chartPeriod === "1d" || chartPeriod === "5y") return new Map<number, number>();

    // Peaks & valleys pour toutes les périodes — les vrais points de retournement
    const WINDOW: Record<string, number> = { "5d": 3, "1mo": 2, "6mo": 4, "1y": 5 };
    const MAX_DOTS: Record<string, number> = { "5d": 5, "1mo": 8, "6mo": 10, "1y": 10 };
    const w = WINDOW[chartPeriod] ?? 4;
    const maxDots = MAX_DOTS[chartPeriod] ?? 10;

    const candidates: { idx: number; pct: number }[] = [];

    for (let i = w; i < data.length - w; i++) {
      const cur = data[i].close;
      const before = data.slice(i - w, i).map((p) => p.close);
      const after = data.slice(i + 1, i + w + 1).map((p) => p.close);

      const isPeak = cur > Math.max(...before) && cur > Math.max(...after);
      const isValley = cur < Math.min(...before) && cur < Math.min(...after);
      if (!isPeak && !isValley) continue;

      const neighbors = [...before, ...after];
      const avg = neighbors.reduce((a, b) => a + b, 0) / neighbors.length;
      const pct = (cur - avg) / avg * 100;
      candidates.push({ idx: i, pct });
    }

    candidates.sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct));
    return new Map(candidates.slice(0, maxDots).map(({ idx, pct }) => [idx, pct]));
  })();

  const chart = stock?.chart ?? [];
  const chartData = {
    labels: chart.map((p) => p.date),
    datasets: [
      {
        data: chart.map((p) => p.close),
        borderColor: isPositive ? "#4CAF82" : "#E05C4B",
        backgroundColor: isPositive ? "rgba(76,175,130,0.08)" : "rgba(224,92,75,0.08)",
        borderWidth: 1.5,
        fill: true,
        pointRadius: chart.map((_, i) => outlierIndices.has(i) ? 5 : 0),
        pointHoverRadius: chart.map((_, i) => outlierIndices.has(i) ? 7 : 0),
        pointBackgroundColor: chart.map((_, i) => {
          const pct = outlierIndices.get(i);
          if (pct === undefined) return "transparent";
          return pct >= 0 ? "#4CAF82" : "#E05C4B";
        }),
        pointBorderColor: "var(--color-surface)",
        pointBorderWidth: 2,
        tension: 0.2,
      },
    ],
  };

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    onClick: (_: unknown, elements: { index: number }[]) => {
      if (!elements.length) return;
      const idx = elements[0].index;
      const pct = outlierIndices.get(idx);
      if (pct === undefined) return;
      setEventPoint({ date: chart[idx].date, changePct: pct });
    },
    plugins: { legend: { display: false }, tooltip: {
      mode: "index" as const,
      intersect: false,
      backgroundColor: "#1E1D1B",
      borderColor: "#302F2C",
      borderWidth: 1,
      titleColor: "#9E9B95",
      bodyColor: "#E8E5DF",
      titleFont: { family: "Space Mono", size: 10 },
      bodyFont: { family: "Space Mono", size: 12 },
      padding: 10,
      displayColors: false,
      callbacks: {
        title: (items: { label: string }[]) => items[0]?.label ?? "",
        label: (item: { parsed: { y: number } }) => {
          const converted = stock && rates && stock.currency !== currency
            ? item.parsed.y * (currency === "EUR" ? rates.usdToEur : rates.eurToUsd)
            : item.parsed.y;
          return `${displaySymbol}${converted.toFixed(2)}`;
        },
      },
    }},
    scales: {
      x: {
        display: false,
        grid: { display: false },
      },
      y: {
        display: true,
        position: "right" as const,
        grid: { color: "#302F2C", drawBorder: false },
        ticks: {
          color: "#5C5A56",
          font: { family: "Space Mono", size: 10 },
          callback: (v: number | string) => {
            const converted = stock && rates && stock.currency !== currency
              ? Number(v) * (currency === "EUR" ? rates.usdToEur : rates.eurToUsd)
              : Number(v);
            return `${displaySymbol}${converted.toFixed(0)}`;
          },
          maxTicksLimit: 5,
        },
        border: { display: false },
      },
    },
    interaction: { mode: "index" as const, intersect: false },
  };

  function getDisplayedArticles(): Article[] {
    const all = news?.articles ?? [];
    const now = Date.now();
    const DAY = 86_400_000;

    if (chartPeriod === "1d") {
      // 24H : tous les articles dans les 24h
      return all.filter(a => a.pub_date && now - new Date(a.pub_date).getTime() < DAY);
    }
    if (chartPeriod === "5d") {
      // 1W : bucket par jour, max 3/jour, tri par longueur brutalised
      const cutoff = now - 7 * DAY;
      const inRange = all.filter(a => a.pub_date && new Date(a.pub_date).getTime() >= cutoff);
      const slots: Record<number, Article[]> = {};
      for (const a of inRange) {
        const key = Math.floor(new Date(a.pub_date).getTime() / DAY);
        (slots[key] ??= []).push(a);
      }
      const result: Article[] = [];
      for (const key of Object.keys(slots).map(Number).sort((a, b) => b - a)) {
        const bucket = slots[key].sort((a, b) => ((b.dumb_en || b.dumb_fr)?.length ?? 0) - ((a.dumb_en || a.dumb_fr)?.length ?? 0));
        result.push(...bucket.slice(0, 3));
      }
      return result;
    }
    if (chartPeriod === "1mo" || chartPeriod === "6mo" || chartPeriod === "1y" || chartPeriod === "5y") {
      return periodArticles;
    }
    return all;
  }

  function handleArticleClick(article: Article) {
    if (article.type === "VIDEO") {
      setSelectedVideo(article);
    } else {
      setSelectedArticle(article);
    }
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--color-bg)" }}>
      {/* Topbar */}
      <header style={HEADER_STYLE}>
        <div style={{ ...HEADER_INNER, justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "4px" }}>
            <BackButton />
            <NavBrand />
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <button
              onClick={() => setCurrency(currency === "USD" ? "EUR" : "USD")}
              style={{
                padding: "3px 10px", borderRadius: "4px", fontSize: "11px", fontWeight: 700,
                fontFamily: "var(--font-mono)", letterSpacing: "0.06em", cursor: "pointer",
                border: "1px solid var(--color-border)",
                background: "var(--color-surface-2)", color: "var(--color-text-secondary)",
              }}
            >
              {currency === "USD" ? "→ EUR" : "→ USD"}
            </button>
            {user && (
              <button
                onClick={() => router.push("/profile")}
                title={user.email ?? "Profile"}
                style={{ background: "none", border: "none", cursor: "pointer", padding: 0, display: "flex" }}
              >
                <Avatar user={user} />
              </button>
            )}
          </div>
        </div>
      </header>

      <main style={{ maxWidth: "720px", margin: "0 auto", padding: "24px 20px 80px" }}>

        {/* Cold start banner — tickers rares hors top 250 */}
        {ticker && (
          <ColdStartBanner
            ticker={ticker.toUpperCase()}
            lang={typeof window !== "undefined" ? (navigator.language.startsWith("fr") ? "fr" : "en") : "fr"}
            onPhaseComplete={(phase) => {
              setColdStartPhase(phase);
              // Recharger les articles quand une phase se termine
              if (["1m", "6m", "1y"].includes(phase)) {
                const apiPeriod = chartPeriod === "5y" ? "all" : chartPeriod;
                fetch(`${API}/api/articles/${ticker.toUpperCase()}?period=${apiPeriod}`)
                  .then((r) => r.json())
                  .then((d) => { setPeriodArticles(d.articles ?? []); setPeriodSummaries(d.summaries ?? []); })
                  .catch(() => {});
              }
            }}
          />
        )}

        {/* Stock header */}
        {stockLoading ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--color-text-muted)", fontSize: "13px" }}>
            Loading…
          </div>
        ) : stockError ? (
          <div style={{ padding: "40px 0", textAlign: "center", color: "var(--color-negative)", fontSize: "13px" }}>
            {stockError}
          </div>
        ) : stock && (
          <>
            <div style={{ marginBottom: "20px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "4px" }}>
                <h1 style={{
                  fontSize: "24px", fontWeight: 800, fontFamily: "var(--font-mono)",
                  color: "var(--color-text-primary)", letterSpacing: "0.02em", margin: 0,
                }}>
                  {stock.ticker}
                </h1>
                <span style={{ fontSize: "14px", color: "var(--color-text-secondary)" }}>{stock.name}</span>
                {stock.website && (
                  <a
                    href={`https://${stock.website}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={stock.website}
                    style={{ color: "var(--color-text-muted)", display: "flex", alignItems: "center", marginLeft: "2px" }}
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                      <polyline points="15 3 21 3 21 9" />
                      <line x1="10" y1="14" x2="21" y2="3" />
                    </svg>
                  </a>
                )}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <span style={{
                  fontSize: "28px", fontWeight: 800, fontFamily: "var(--font-mono)",
                  color: "var(--color-text-primary)", letterSpacing: "-0.02em",
                }}>
                  {displaySymbol}{fmt(convert(stock.current_price))}
                </span>
                <ChangeBadge value={displayChangePct} />
                {/* Star moved here from header */}
                {userId && (
                  <button
                    onClick={handlePin}
                    disabled={pinLoading}
                    title={isPinned ? "Retirer des favoris" : "Ajouter aux favoris"}
                    style={{
                      background: "none", border: "none", cursor: "pointer", padding: "4px",
                      color: isPinned ? "var(--color-accent)" : "var(--color-text-muted)",
                      opacity: pinLoading ? 0.5 : 1,
                      display: "flex", alignItems: "center",
                    }}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill={isPinned ? "currentColor" : "none"} stroke="currentColor" strokeWidth="2">
                      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                    </svg>
                  </button>
                )}
              </div>
            </div>

            {/* Chart */}
            {stock.chart.length > 0 && (
              <div style={{
                background: "var(--color-surface)", border: "1px solid var(--color-border)",
                borderRadius: "8px", padding: "16px", marginBottom: "20px",
              }}>
                {/* Period selector */}
                <div style={{ display: "flex", gap: "4px", marginBottom: "12px" }}>
                  {(["1d","5d","1mo","6mo","1y","5y"] as const).map((p) => (
                    <button
                      key={p}
                      onClick={() => setChartPeriod(p)}
                      style={{
                        padding: "3px 8px", borderRadius: "4px", fontSize: "10px", fontWeight: 700,
                        fontFamily: "var(--font-mono)", letterSpacing: "0.04em", cursor: "pointer", border: "none",
                        background: chartPeriod === p ? "var(--color-accent)" : "transparent",
                        color: chartPeriod === p ? "#fff" : "var(--color-text-muted)",
                        transition: "background 0.15s, color 0.15s",
                      }}
                    >
                      {p === "1d" ? "24H" : p === "5d" ? "1W" : p === "1mo" ? "1M" : p === "6mo" ? "6M" : p === "1y" ? "1Y" : "5Y"}
                    </button>
                  ))}
                  {chartLoading && (
                    <span style={{ marginLeft: "8px", fontSize: "10px", color: "var(--color-text-muted)", alignSelf: "center" }}>…</span>
                  )}
                </div>
                <div style={{ height: "180px" }}>
                  <Line data={chartData} options={chartOptions as Parameters<typeof Line>[0]["options"]} plugins={[verticalLinesPlugin]} />
                </div>
              </div>
            )}

            {/* Stats */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "10px", marginBottom: "28px" }}>
              <StatBox label="Market Cap" value={fmtCap(stock.market_cap)} />
              <StatBox label="P/E Ratio" value={fmt(stock.pe_ratio)} />
              <StatBox label="52W High" value={`${displaySymbol}${fmt(convert(stock.week_52_high))}`} />
              <StatBox label="52W Low" value={`${displaySymbol}${fmt(convert(stock.week_52_low))}`} />
            </div>
          </>
        )}

        {/* News section */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "12px" }}>
          <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
            News & Analysis
          </span>
          <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
            {chartPeriod === "1d" ? "24h" : chartPeriod === "5d" ? "7 jours" : chartPeriod === "1mo" ? "1 mois" : chartPeriod === "6mo" ? "6 mois" : chartPeriod === "1y" ? "1 an" : "5 ans"}
          </span>
        </div>

        {newsLoading ? (
          <div style={{
            background: "var(--color-surface)", border: "1px solid var(--color-border)",
            borderRadius: "8px",
          }}>
            <DogLoader text="Récupération des news & analyse IA…" />
          </div>
        ) : newsError ? (
          <div style={{
            background: "var(--color-surface)", border: "1px solid var(--color-border)",
            borderRadius: "8px", padding: "24px", textAlign: "center", color: "var(--color-negative)", fontSize: "13px",
          }}>
            {newsError}
          </div>
        ) : news && (
          <>
            {/* AI Summary + Sentiment — Yahoo, uniquement pour 24H / 1W */}
            {(chartPeriod === "1d" || chartPeriod === "5d") && (() => {
              const is24h = chartPeriod === "1d";
              const activeSummary = is24h ? news.summary_24h : news.summary;
              const activeSentiment = is24h ? news.sentiment_24h : news.sentiment;
              const activeReason = is24h ? news.sentiment_reason_24h : news.sentiment_reason;
              if (!activeSummary) return null;
              return (
                <div style={{
                  background: "var(--color-surface)", border: "1px solid var(--color-border)",
                  borderRadius: "8px", padding: "22px 24px", marginBottom: "12px",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "16px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                        AI Overview · {is24h ? "24h" : "7 jours"}
                      </span>
                      {is24h && news.no_24h_news && (
                        <span style={{
                          fontSize: "10px", fontWeight: 600, padding: "2px 7px", borderRadius: "4px",
                          background: "rgba(255,180,0,0.1)", color: "#C8960C",
                          fontFamily: "var(--font-mono)", letterSpacing: "0.05em",
                        }}>
                          AUCUNE NEWS 24H
                        </span>
                      )}
                    </div>
                    <SentimentBadge sentiment={activeSentiment} />
                  </div>
                  {is24h && news.no_24h_news && (
                    <p style={{ fontSize: "12px", color: "var(--color-text-muted)", marginBottom: "12px", fontStyle: "italic" }}>
                      Aucune actualité publiée dans les dernières 24h — analyse basée sur les données récentes disponibles.
                    </p>
                  )}
                  <p style={{ fontSize: "15px", color: "var(--color-text-primary)", lineHeight: 1.75, margin: 0, fontWeight: 400 }}>
                    {activeSummary}
                  </p>
                </div>
              );
            })()}

            {/* Articles list */}
            {periodLoading ? (
              <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px" }}>
                <DogLoader text="Chargement des articles…" />
              </div>
            ) : (() => {
              const displayed = getDisplayedArticles();
              return (
                <>
                  {/* Sections semaine (1M) ou mois (6M/1Y/ALL) */}
                  {["1mo","6mo","1y","5y"].includes(chartPeriod) && periodSummaries.length > 0 ? periodSummaries.map((s, i) => {
                    const key = s.year_month ? `${s.year_month}-${s.week_num ?? "m"}` : s.year ?? String(i);
                    const accentColor = s.skewed
                      ? "var(--color-text-muted)"
                      : s.type === "year" ? "#9B77CF" : s.type === "week" ? "#4CAF82" : "#5B9BD5";
                    const sectionLabel = s.skewed
                      ? `${s.skewed_range} · ${s.label}`
                      : s.type === "year" ? `Année ${s.year}` : s.label;
                    const sectionArticles = s.top_articles ?? [];
                    return (
                      <div key={key} style={{ marginBottom: "16px" }}>
                        {/* Header section */}
                        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                          <div style={{ height: "1px", width: "12px", background: accentColor, flexShrink: 0 }} />
                          <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.07em", textTransform: "uppercase", color: accentColor, whiteSpace: "nowrap" }}>
                            {sectionLabel}
                          </span>
                          {s.article_count ? (
                            <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>· {s.article_count} articles</span>
                          ) : null}
                          <div style={{ flex: 1, height: "1px", background: "var(--color-border)" }} />
                        </div>

                        {s.skewed ? (
                          /* Cas skewed — pas de récap hebdo possible */
                          <>
                            <div style={{
                              display: "flex", alignItems: "center", gap: "8px",
                              padding: "8px 12px", marginBottom: "4px",
                              background: "var(--color-surface)", border: "1px solid var(--color-border)",
                              borderRadius: "6px",
                            }}>
                              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2">
                                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                              </svg>
                              <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontStyle: "italic" }}>
                                Récap hebdomadaire indisponible — couverture insuffisante sur cette période
                              </span>
                            </div>
                            {sectionArticles.length > 0 && (
                              <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
                                {sectionArticles.map((article) => (
                                  <CompactArticleRow key={article.id} article={article} onClick={() => handleArticleClick(article)} />
                                ))}
                              </div>
                            )}
                          </>
                        ) : (
                          /* Cas normal — résumé AI + articles compacts */
                          <>
                            {s.ai_summary && (
                              <div style={{
                                background: "var(--color-surface)", border: "1px solid var(--color-border)",
                                borderRadius: "8px", padding: "14px 16px",
                                marginBottom: sectionArticles.length > 0 ? "3px" : 0,
                              }}>
                                <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.65, margin: 0 }}>
                                  {s.ai_summary}
                                </p>
                              </div>
                            )}
                            {sectionArticles.length > 0 && (
                              <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
                                {sectionArticles.map((article) => (
                                  <CompactArticleRow key={article.id} article={article} onClick={() => handleArticleClick(article)} />
                                ))}
                              </div>
                            )}
                          </>
                        )}
                      </div>
                    );
                  }) : null}
                  {/* Articles bruts (fallback semaines sans summary) */}
                  {displayed.length > 0 && (
                    <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
                      {displayed.map((article) => (
                        <NewsCard key={article.id} article={article} onClick={() => handleArticleClick(article)} />
                      ))}
                    </div>
                  )}
                  {["1mo","6mo","1y","5y"].includes(chartPeriod) && periodSummaries.length === 0 && displayed.length === 0 && !periodLoading && (
                    <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", padding: "32px", textAlign: "center", color: "var(--color-text-muted)", fontSize: "13px" }}>
                      Aucun article disponible pour cette période.
                    </div>
                  )}
                </>
              );
            })()}
          </>
        )}
      </main>

      {/* Drawer + Modal */}
      <ArticleDrawer
        article={selectedArticle}
        onClose={() => setSelectedArticle(null)}
      />
      <VideoModal
        article={selectedVideo}
        onClose={() => setSelectedVideo(null)}
      />
      {eventPoint && ticker && (
        <EventDrawer
          ticker={ticker.toUpperCase()}
          date={eventPoint.date}
          changePct={eventPoint.changePct}
          onClose={() => setEventPoint(null)}
          apiUrl={API}
        />
      )}
    </div>
  );
}
