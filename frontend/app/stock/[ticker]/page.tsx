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
  is_refreshing?: boolean;
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
      padding: "8px 12px",
    }}>
      <div style={{ fontSize: "10px", color: "var(--color-text-muted)", marginBottom: "2px", letterSpacing: "0.04em" }}>
        {label}
      </div>
      <div style={{ fontSize: "13px", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--color-text-primary)" }}>
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

function ExpandButton({ count, expanded, onClick, accentColor }: {
  count: number; expanded: boolean; onClick: () => void; accentColor: string;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: "flex", alignItems: "center", gap: "5px",
        padding: "3px 8px 3px 10px",
        borderRadius: "12px",
        border: `1px solid ${accentColor}`,
        background: hovered ? accentColor : "transparent",
        color: hovered ? "#fff" : accentColor,
        cursor: "pointer",
        fontSize: "10px", fontWeight: 700, letterSpacing: "0.04em",
        transition: "background 0.15s, color 0.15s",
        flexShrink: 0,
      }}
    >
      {count} articles
      <svg
        width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
        style={{ transform: expanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}
      >
        <polyline points="9 18 15 12 9 6" />
      </svg>
    </button>
  );
}

function CompactArticleRow({ article, onClick, directLink }: { article: Article; onClick: () => void; directLink?: boolean }) {
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

  const handleClick = directLink && article.url
    ? () => window.open(article.url, "_blank", "noopener,noreferrer")
    : onClick;

  if (directLink) {
    return (
      <div
        onClick={handleClick}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: "flex", alignItems: "center", gap: "10px",
          padding: "8px 12px",
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
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2" style={{ marginLeft: "2px", flexShrink: 0 }}>
            <path d="M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>
          </svg>
        </div>
      </div>
    );
  }

  return (
    <div
      onClick={handleClick}
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
        padding: "10px 14px",
        cursor: "pointer",
        borderBottom: "1px solid var(--color-border)",
        background: hovered ? "var(--color-surface-2)" : "transparent",
        transition: "background 0.1s",
      }}
    >
      {/* Pic (left, spans 2 rows) + source+date / title (right) */}
      <div style={{ display: "flex", gap: "10px", marginBottom: (article.dumb_en || article.dumb_fr) ? "3px" : "0" }}>
        {/* Thumbnail */}
        <div style={{ flexShrink: 0, width: "64px" }}>
          {article.thumbnail ? (
            <div style={{ position: "relative", width: "64px", height: "48px", borderRadius: "5px", overflow: "hidden" }}>
              <img src={article.thumbnail} alt="" style={{ width: "100%", height: "100%", objectFit: "cover" }} />
              {isVideo && (
                <div style={{ position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center", background: "rgba(0,0,0,0.45)" }}>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="#fff"><polygon points="5 3 19 12 5 21 5 3" /></svg>
                </div>
              )}
            </div>
          ) : (
            <div style={{ width: "64px", height: "48px", borderRadius: "5px", background: "var(--color-surface-2)", border: "1px solid var(--color-border)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              {isVideo
                ? <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2"><polygon points="5 3 19 12 5 21 5 3" /></svg>
                : <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" /><polyline points="14 2 14 8 20 8" /></svg>
              }
            </div>
          )}
        </div>
        {/* Content : source+date / title */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: "5px", marginBottom: "4px" }}>
            <MiniSourceBadge name={article.source} />
            {(article.extra_sources || []).slice(0, 2).map((s, i) => <MiniSourceBadge key={i} name={s.name} />)}
            {ago && <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>{ago}</span>}
            {isVideo && <span style={{ fontSize: "9px", fontWeight: 700, letterSpacing: "0.06em", padding: "1px 5px", borderRadius: "3px", background: "var(--color-surface-2)", color: "var(--color-text-muted)" }}>VIDEO</span>}
          </div>
          <div style={{ fontSize: "13px", fontWeight: 600, color: "var(--color-text-primary)", lineHeight: 1.4 }}>
            {article.title}
          </div>
        </div>
      </div>

      {/* Dumb — pleine largeur */}
      {(article.dumb_en || article.dumb_fr) && (
        <div style={{ fontSize: "12px", color: "var(--color-accent)", lineHeight: 1.5 }}>
          {article.dumb_en || article.dumb_fr}
        </div>
      )}
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
  const [expandedSections, setExpandedSections] = useState<Record<string, number>>({});
  const [expandedYears, setExpandedYears] = useState<Record<string, boolean>>({});

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
    if (!news?.is_refreshing || !ticker) return;
    let active = true;
    const poll = () => {
      setTimeout(() => {
        if (!active) return;
        fetch(`${API}/api/news/${ticker.toUpperCase()}`)
          .then((r) => r.json())
          .then((d) => { setNews(d); if (active && d.is_refreshing) poll(); })
          .catch(() => { if (active) poll(); });
      }, 20_000);
    };
    poll();
    return () => { active = false; };
  }, [news?.is_refreshing, ticker]);

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
    setExpandedSections({});
    setExpandedYears({});
    const ARCHIVE_PERIODS = new Set(["1mo", "6mo", "1y", "5y"]);
    const apiPeriod = chartPeriod === "5y" ? "all" : chartPeriod;
    if (ARCHIVE_PERIODS.has(chartPeriod)) {
      setPeriodLoading(true);
      setPeriodArticles([]);
      setPeriodSummaries([]);
      const cn = stock?.name ? `&company_name=${encodeURIComponent(stock.name)}` : "";
      fetch(`${API}/api/articles/${ticker.toUpperCase()}?period=${apiPeriod}${cn}`)
        .then((r) => r.json())
        .then((d) => {
          const summaries: typeof periodSummaries = d.summaries ?? [];
          setPeriodArticles(d.articles ?? []);
          setPeriodSummaries(summaries);
          // Ouvrir l'année la plus récente par défaut en 5Y
          if (chartPeriod === "5y") {
            const firstYear = summaries.find(s => s.type === "year")?.year;
            if (firstYear) setExpandedYears({ [firstYear]: true });
          }
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
      return all.filter(a => a.pub_date && new Date(a.pub_date).getTime() >= now - 26 * 3_600_000 && (a.dumb_en || a.dumb_fr));
    }
    if (chartPeriod === "5d") {
      // 1W : bucket par jour, max 3/jour, tri par longueur brutalised
      const cutoff = now - 7 * DAY;
      const inRange = all.filter(a => a.pub_date && new Date(a.pub_date).getTime() >= cutoff && (a.dumb_en || a.dumb_fr));
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

      <main style={{ maxWidth: "880px", margin: "0 auto", padding: "24px 20px 80px" }}>

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
                const cn2 = stock?.name ? `&company_name=${encodeURIComponent(stock.name)}` : "";
                fetch(`${API}/api/articles/${ticker.toUpperCase()}?period=${apiPeriod}${cn2}`)
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
            <div style={{ marginBottom: "20px", position: "relative" }}>
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
              {news && (chartPeriod === "1d" || chartPeriod === "5d") && (() => {
                const sentiment = chartPeriod === "1d" ? news.sentiment_24h : news.sentiment;
                if (!sentiment) return null;
                return <div style={{ position: "absolute", top: 0, right: 0 }}><SentimentBadge sentiment={sentiment} /></div>;
              })()}
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
                <div style={{ height: "240px" }}>
                  <Line data={chartData} options={chartOptions as Parameters<typeof Line>[0]["options"]} plugins={[verticalLinesPlugin]} />
                </div>
              </div>
            )}

            {/* Stats */}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "8px", marginBottom: "20px" }}>
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
          {chartPeriod !== "1d" && chartPeriod !== "5d" && (
            <span style={{ fontSize: "11px", color: "var(--color-text-muted)" }}>
              {chartPeriod === "1mo" ? "1 mois" : chartPeriod === "6mo" ? "6 mois" : chartPeriod === "1y" ? "1 an" : "5 ans"}
            </span>
          )}
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
              if (!activeSummary && !news.is_refreshing) return null;
              if (!activeSummary && news.is_refreshing) return (
                <div style={{
                  background: "var(--color-surface)", border: "1px solid var(--color-border)",
                  borderRadius: "8px", padding: "22px 24px", marginBottom: "12px",
                  display: "flex", justifyContent: "center",
                }}>
                  <DogLoader />
                </div>
              );
              return (
                <div style={{
                  background: "var(--color-surface)", border: "1px solid var(--color-border)",
                  borderRadius: "8px", padding: "14px 18px", marginBottom: "12px",
                }}>
                  <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span style={{ fontSize: "11px", fontWeight: 700, letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-text-muted)" }}>
                        AI Overview{!is24h ? " · 7 jours" : ""}
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
                      {news.is_refreshing && (
                        <span style={{
                          display: "inline-flex", alignItems: "center", gap: "5px",
                          fontSize: "10px", padding: "2px 8px", borderRadius: "4px",
                          background: "rgba(76,175,130,0.12)", color: "var(--color-accent)",
                          fontFamily: "var(--font-mono)", letterSpacing: "0.05em", fontWeight: 600,
                          border: "1px solid rgba(76,175,130,0.25)",
                        }}>
                          <span className="refreshing-icon">↻</span> mise à jour...
                        </span>
                      )}
                    </div>
                    {news.cached_at && (() => {
                      try {
                        const d = new Date(news.cached_at);
                        return <span className="ow-date-blink" style={{ fontSize: "11px", fontFamily: "var(--font-mono)", color: "var(--color-accent)", fontWeight: 600 }}>{d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" })} {d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}</span>;
                      } catch { return null; }
                    })()}
                  </div>
                  {is24h && news.no_24h_news ? (
                    <p style={{ fontSize: "13px", color: "var(--color-text-muted)", margin: 0, fontStyle: "italic" }}>
                      Aucune news dans les dernières 24h — basé sur les données disponibles.
                    </p>
                  ) : (
                    <p style={{ fontSize: "15px", color: "var(--color-text-primary)", lineHeight: 1.75, margin: 0, fontWeight: 400 }}>
                      {activeSummary}
                    </p>
                  )}
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
                  {["1mo","6mo","1y","5y"].includes(chartPeriod) && periodSummaries.length > 0 ? (() => {
                    /* Helper : rendu d'une section mensuelle/hebdo */
                    const renderSection = (s: typeof periodSummaries[0], i: number) => {
                      const key = s.year_month ? `${s.year_month}-${s.week_num ?? "m"}` : s.year ?? String(i);
                      const accentColor = s.type === "week" ? "#4CAF82" : "#5B9BD5";
                      const sectionLabel = s.skewed ? `${s.skewed_range} · ${s.label}` : s.label;
                      const sectionArticles = s.top_articles ?? [];
                      const shown = expandedSections[key] ?? 0;
                      const visibleArticles = sectionArticles.slice(0, shown);
                      const hasMore = sectionArticles.length > shown;
                      const isExpanded = shown > 0;
                      const toggleExpand = () => setExpandedSections(prev => ({ ...prev, [key]: isExpanded ? 0 : 10 }));
                      const loadMore = () => setExpandedSections(prev => ({ ...prev, [key]: shown + 10 }));

                      return (
                        <div key={key} style={{ marginBottom: "10px" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                            <div style={{ height: "1px", width: "12px", background: accentColor, flexShrink: 0 }} />
                            <span style={{ fontSize: "10px", fontWeight: 700, letterSpacing: "0.07em", textTransform: "uppercase", color: accentColor, whiteSpace: "nowrap" }}>
                              {sectionLabel}
                            </span>
                            {s.article_count ? (
                              <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>· {s.article_count} articles</span>
                            ) : null}
                            <div style={{ flex: 1, height: "1px", background: "var(--color-border)" }} />
                            {sectionArticles.length > 0 && (
                              <ExpandButton count={sectionArticles.length} expanded={isExpanded} onClick={toggleExpand} accentColor={accentColor} />
                            )}
                          </div>
                          {s.skewed ? (
                            <>
                              {sectionArticles.length === 0 && (
                                <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "8px 12px", marginBottom: "4px", background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "6px" }}>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--color-text-muted)" strokeWidth="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                                  <span style={{ fontSize: "11px", color: "var(--color-text-muted)", fontStyle: "italic" }}>Récap hebdomadaire indisponible</span>
                                </div>
                              )}
                              {isExpanded && visibleArticles.length > 0 && (
                                <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
                                  {visibleArticles.map((article) => (
                                    <CompactArticleRow key={article.id} article={article} onClick={() => handleArticleClick(article)} directLink={["6mo", "1y"].includes(chartPeriod)} />
                                  ))}
                                  {hasMore && (
                                    <button onClick={loadMore} style={{ width: "100%", padding: "7px", background: "none", border: "none", borderTop: "1px solid var(--color-border)", cursor: "pointer", color: "var(--color-text-muted)", fontSize: "11px", display: "flex", alignItems: "center", justifyContent: "center", gap: "4px" }}>
                                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="6 9 12 15 18 9" /></svg>
                                      {Math.min(10, sectionArticles.length - shown)} de plus
                                    </button>
                                  )}
                                </div>
                              )}
                            </>
                          ) : (
                            <>
                              {s.ai_summary && (
                                <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", padding: "14px 16px", marginBottom: isExpanded && visibleArticles.length > 0 ? "3px" : 0 }}>
                                  <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.65, margin: 0 }}>{s.ai_summary}</p>
                                </div>
                              )}
                              {isExpanded && visibleArticles.length > 0 && (
                                <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", overflow: "hidden" }}>
                                  {visibleArticles.map((article) => (
                                    <CompactArticleRow key={article.id} article={article} onClick={() => handleArticleClick(article)} directLink={["6mo", "1y"].includes(chartPeriod)} />
                                  ))}
                                  {hasMore && (
                                    <button onClick={loadMore} style={{ width: "100%", padding: "7px", background: "none", border: "none", borderTop: "1px solid var(--color-border)", cursor: "pointer", color: "var(--color-text-muted)", fontSize: "11px", display: "flex", alignItems: "center", justifyContent: "center", gap: "4px" }}>
                                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><polyline points="6 9 12 15 18 9" /></svg>
                                      {Math.min(10, sectionArticles.length - shown)} de plus
                                    </button>
                                  )}
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      );
                    };

                    /* Vue 5Y : grouper par année */
                    if (chartPeriod === "5y") {
                      const yearGroups: { year: string; yearData: typeof periodSummaries[0]; months: typeof periodSummaries }[] = [];
                      let currentGroup: (typeof yearGroups)[0] | null = null;
                      for (const s of periodSummaries) {
                        if (s.type === "year") {
                          currentGroup = { year: s.year!, yearData: s, months: [] };
                          yearGroups.push(currentGroup);
                        } else if (currentGroup) {
                          currentGroup.months.push(s);
                        }
                      }
                      return yearGroups.map(({ year, yearData, months }) => {
                        const isYearOpen = expandedYears[year] ?? false;
                        const totalArticles = months.reduce((acc, m) => acc + (m.article_count ?? 0), 0);
                        return (
                          <div key={year} style={{ marginBottom: "16px" }}>
                            {/* Header année — dropdown */}
                            <button
                              onClick={() => setExpandedYears(prev => ({ ...prev, [year]: !isYearOpen }))}
                              style={{
                                width: "100%", display: "flex", alignItems: "center", gap: "10px",
                                background: "none", border: "none", cursor: "pointer",
                                marginBottom: isYearOpen ? "10px" : 0, padding: 0,
                              }}
                            >
                              <div style={{ height: "1px", width: "12px", background: "#9B77CF", flexShrink: 0 }} />
                              <span style={{ fontSize: "11px", fontWeight: 800, letterSpacing: "0.08em", textTransform: "uppercase", color: "#9B77CF", whiteSpace: "nowrap" }}>
                                {year}
                              </span>
                              {totalArticles > 0 && (
                                <span style={{ fontSize: "10px", color: "var(--color-text-muted)" }}>· {totalArticles} articles</span>
                              )}
                              <div style={{ flex: 1, height: "1px", background: "var(--color-border)" }} />
                              <svg
                                width="13" height="13" viewBox="0 0 24 24" fill="none"
                                stroke="#9B77CF" strokeWidth="2.5"
                                style={{ flexShrink: 0, transform: isYearOpen ? "rotate(90deg)" : "none", transition: "transform 0.18s" }}
                              >
                                <polyline points="9 18 15 12 9 6" />
                              </svg>
                            </button>
                            {/* Résumé annuel */}
                            {isYearOpen && yearData.ai_summary && (
                              <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-border)", borderRadius: "8px", padding: "14px 16px", marginBottom: "10px" }}>
                                <p style={{ fontSize: "13px", color: "var(--color-text-secondary)", lineHeight: 1.65, margin: 0 }}>{yearData.ai_summary}</p>
                              </div>
                            )}
                            {/* Mois du dropdown */}
                            {isYearOpen && (
                              <div style={{ paddingLeft: "16px", borderLeft: "2px solid rgba(155,119,207,0.3)" }}>
                                {months.map((m, i) => renderSection(m, i))}
                              </div>
                            )}
                          </div>
                        );
                      });
                    }

                    /* Vues 1M / 6M / 1Y : rendu plat */
                    return periodSummaries.map((s, i) => renderSection(s, i));
                  })() : null}
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
