"use client";

import { useState, useEffect, useCallback } from "react";
import { createClient } from "./supabase";

const LS_KEY = "sv_currency_pref";

export type Currency = "USD" | "EUR";

export function useCurrencyPref() {
  const [currency, setCurrencyState] = useState<Currency>("USD");
  const [userId, setUserId] = useState<string | null>(null);

  // Load user + preference on mount
  useEffect(() => {
    const local = (localStorage.getItem(LS_KEY) as Currency) ?? "USD";
    setCurrencyState(local);

    const supa = createClient();
    if (!supa) return;

    supa.auth.getUser().then(({ data }) => {
      const uid = data.user?.id ?? null;
      setUserId(uid);
      if (!uid) return;

      supa
        .from("user_preferences")
        .select("currency")
        .eq("user_id", uid)
        .single()
        .then(({ data: row }) => {
          if (row?.currency) {
            setCurrencyState(row.currency as Currency);
            localStorage.setItem(LS_KEY, row.currency);
          }
        });
    });
  }, []);

  const setCurrency = useCallback(
    (c: Currency) => {
      setCurrencyState(c);
      localStorage.setItem(LS_KEY, c);

      if (!userId) return;
      const supa = createClient();
      if (!supa) return;
      supa.from("user_preferences").upsert({ user_id: userId, currency: c, updated_at: new Date().toISOString() });
    },
    [userId]
  );

  return { currency, setCurrency };
}
