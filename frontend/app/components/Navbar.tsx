"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import type { User } from "../lib/supabase";

const DOG_BODY = `M403.31,174.312c-0.048-0.278-11.065-141.195-11.065-141.195c-0.15-1.945-1.537-3.572-3.434-4.028
  c-1.895-0.455-3.872,0.361-4.891,2.026l-23.47,38.341l-15.485-30.824c-0.804-1.599-2.475-2.57-4.265-2.474
  c-1.786,0.097-3.346,1.243-3.973,2.919l-13.768,36.851c-2.015-0.33-4.05-0.496-6.085-0.496
  c-15.872,0-30.038,10.036-35.463,24.755l-61.888,6.991c-1.25,0.141-2.384,0.799-3.127,1.814
  c-0.743,1.016-1.026,2.295-0.783,3.529c8.219,41.596,40.378,70.571,80.557,73.02l-2.122,7.703l-53.063,9.195
  c8.766,14.065,13.839,30.659,13.839,48.418c0,4.971-4.029,9-9,9s-9-4.029-9-9c0-40.701-33.113-73.814-73.814-73.814
  c-24.132,0-46.79,11.854-60.612,31.709c-1.979,2.841-5.41,4.294-8.829,3.742c-3.417-0.555-6.214-3.021-7.191-6.342
  c-1.866-6.339-12.406-37.636-36.681-31.852l-4.65,1.207l15.555,64.411c1.913,9.14,4.519,17.973,7.754,26.453
  c-19.99-15.077-35.064-35.734-43.406-59.935C2.106,140.161,37.437,67.656,103.712,44.81
  c4.699-1.62,7.195-6.743,5.576-11.442c-1.62-4.699-6.743-7.195-11.442-5.576C22.188,53.873-18.146,136.644,7.935,212.303
  c12.634,36.65,38.784,66.187,73.633,83.168c0.536,0.261,1.078,0.505,1.616,0.759
  c31.387,50.35,87.244,82.392,148.106,82.389c9.855,0,19.846-0.841,29.88-2.579
  c45.925-7.958,86.003-33.325,112.85-71.426C400.867,266.513,411.269,220.237,403.31,174.312z`;

const DOG_SNOUT = `M80.496,90.201c-1.166-0.254-2.386-0.034-3.39,0.612c-1.004,0.646-1.711,1.664-1.964,2.83
  L61.61,155.932c10.687,1.554,20.29,7.56,28.296,17.837c3.208,4.118,5.798,8.494,7.838,12.544
  c17.11-17.291,40.567-27.269,65.268-27.269c23.548,0,45.051,8.915,61.32,23.542
  c-8.316-16.64-21.961-30.769-39.96-39.483c-11.975-5.797-24.746-8.736-37.957-8.736
  c-12.138,0-24.185,2.555-35.289,7.44C112.563,119.782,105.465,95.635,80.496,90.201z`;

function DogLogo() {
  return (
    <>
      <div
        style={{
          width: "34px",
          height: "34px",
          borderRadius: "9px",
          background: "var(--color-accent-light)",
          border: "1px solid rgba(76,175,130,0.30)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
        }}
      >
        <div className="nb-logo-wrap">
          <svg
            viewBox="0 0 405.918 405.918"
            width="22"
            height="22"
            fill="none"
            style={{ display: "block" }}
          >
            <path d={DOG_BODY} stroke="#E8E5DF" strokeWidth="20" strokeLinecap="round" strokeLinejoin="round" />
            <path d={DOG_SNOUT} stroke="#E8E5DF" strokeWidth="20" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
      </div>
    </>
  );
}

export function Avatar({ user }: { user: User }) {
  const avatarUrl = (user.user_metadata as Record<string, string> | undefined)?.avatar_url;
  const initial = user.email?.[0]?.toUpperCase() ?? "?";
  const [imgError, setImgError] = useState(false);

  return (
    <div
      style={{
        width: "32px",
        height: "32px",
        borderRadius: "50%",
        overflow: "hidden",
        border: "1.5px solid var(--color-border)",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--color-accent-light)",
      }}
    >
      {avatarUrl && !imgError ? (
        <img
          src={avatarUrl}
          width={32}
          height={32}
          alt=""
          style={{ objectFit: "cover", width: "100%", height: "100%" }}
          onError={() => setImgError(true)}
        />
      ) : (
        <span style={{ fontSize: "13px", fontWeight: 700, color: "var(--color-accent)" }}>
          {initial}
        </span>
      )}
    </div>
  );
}

export function BackButton() {
  const router = useRouter();
  return (
    <button
      onClick={() => router.back()}
      style={{
        background: "none",
        border: "none",
        cursor: "pointer",
        color: "var(--color-text-muted)",
        padding: "6px",
        display: "flex",
        alignItems: "center",
        borderRadius: "6px",
        transition: "background 0.12s, color 0.12s",
        flexShrink: 0,
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.background = "rgba(255,255,255,0.06)";
        e.currentTarget.style.color = "var(--color-text-primary)";
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.background = "none";
        e.currentTarget.style.color = "var(--color-text-muted)";
      }}
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <path d="M19 12H5M12 19l-7-7 7-7" />
      </svg>
    </button>
  );
}

export function NavBrand() {
  const router = useRouter();
  return (
    <button
      onClick={() => router.push("/")}
      style={{
        display: "flex",
        alignItems: "center",
        gap: "10px",
        background: "none",
        border: "none",
        cursor: "pointer",
        padding: 0,
        flexShrink: 0,
      }}
    >
      <DogLogo />
      <div style={{ display: "flex", alignItems: "baseline", gap: "7px" }}>
        <span style={{ fontWeight: 800, fontSize: "17px", color: "var(--color-text-primary)", letterSpacing: "-0.04em", lineHeight: 1 }}>
          Actionfun
        </span>
        <span style={{
          padding: "2px 5px", borderRadius: "3px", fontSize: "9px", fontWeight: 700,
          background: "var(--color-accent-light)", color: "var(--color-accent)",
          fontFamily: "var(--font-mono)", letterSpacing: "0.06em", lineHeight: 1.4,
        }}>
          BETA
        </span>
      </div>
    </button>
  );
}

export const HEADER_STYLE: React.CSSProperties = {
  background: "rgba(14,14,12,0.45)",
  backdropFilter: "blur(22px)",
  WebkitBackdropFilter: "blur(22px)",
  borderBottom: "1px solid rgba(76,175,130,0.13)",
  boxShadow: "0 1px 0 rgba(0,0,0,0.6), inset 0 1px 0 rgba(255,255,255,0.03)",
  position: "sticky",
  top: 0,
  zIndex: 10,
};

export const HEADER_INNER: React.CSSProperties = {
  maxWidth: "880px",
  margin: "0 auto",
  padding: "0 20px",
  height: "66px",
  display: "flex",
  alignItems: "center",
};

interface NavbarProps {
  user: User | null;
  onSignIn?: () => void;
  onSignOut?: () => void;
}

export default function Navbar({ user, onSignIn }: NavbarProps) {
  const router = useRouter();
  return (
    <header style={HEADER_STYLE}>
      <div style={{ ...HEADER_INNER, justifyContent: "space-between" }}>
        <NavBrand />

        {/* Right: actions */}
        {user ? (
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <button
              onClick={() => router.push("/my-news")}
              style={{
                fontSize: "12px",
                fontWeight: 600,
                color: "var(--color-accent)",
                background: "rgba(76,175,130,0.10)",
                border: "1px solid rgba(76,175,130,0.28)",
                borderRadius: "20px",
                cursor: "pointer",
                padding: "5px 13px",
                letterSpacing: "0.01em",
                transition: "background 0.15s, border-color 0.15s",
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = "rgba(76,175,130,0.18)";
                e.currentTarget.style.borderColor = "rgba(76,175,130,0.50)";
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = "rgba(76,175,130,0.10)";
                e.currentTarget.style.borderColor = "rgba(76,175,130,0.28)";
              }}
            >
              Mes news
            </button>
            <button
              onClick={() => router.push("/profile")}
              title={user.email ?? "Profile"}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: 0,
                display: "flex",
              }}
            >
              <Avatar user={user} />
            </button>
          </div>
        ) : (
          <button
            onClick={onSignIn}
            style={{
              padding: "6px 16px",
              fontSize: "13px",
              fontWeight: 600,
              borderRadius: "6px",
              border: "none",
              background: "var(--color-accent)",
              color: "#fff",
              cursor: "pointer",
            }}
          >
            Sign in
          </button>
        )}
      </div>
    </header>
  );
}
