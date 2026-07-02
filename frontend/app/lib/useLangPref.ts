"use client";

import { useState, useEffect, useCallback } from "react";
import { createClient } from "./supabase";

const LS_KEY = "sv_lang_pref";

export type Lang = "en" | "fr";

export function useLangPref() {
  const [lang, setLangState] = useState<Lang>("en");
  const [userId, setUserId] = useState<string | null>(null);

  useEffect(() => {
    const local = (localStorage.getItem(LS_KEY) as Lang) ?? "en";
    setLangState(local);

    const supa = createClient();
    if (!supa) return;

    supa.auth.getUser().then(({ data }) => {
      const uid = data.user?.id ?? null;
      setUserId(uid);
      if (!uid) return;

      supa
        .from("user_preferences")
        .select("lang")
        .eq("user_id", uid)
        .single()
        .then(({ data: row }) => {
          if (row?.lang) {
            setLangState(row.lang as Lang);
            localStorage.setItem(LS_KEY, row.lang);
          }
        });
    });
  }, []);

  const setLang = useCallback(
    (l: Lang) => {
      setLangState(l);
      localStorage.setItem(LS_KEY, l);

      if (!userId) return;
      const supa = createClient();
      if (!supa) return;
      supa.from("user_preferences").upsert({ user_id: userId, lang: l, updated_at: new Date().toISOString() });
    },
    [userId]
  );

  return { lang, setLang };
}
