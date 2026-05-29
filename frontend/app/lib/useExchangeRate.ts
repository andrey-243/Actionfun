"use client";

import { useState, useEffect } from "react";

interface Rates {
  usdToEur: number;
  eurToUsd: number;
}

let cached: Rates | null = null;

export function useExchangeRate() {
  const [rates, setRates] = useState<Rates | null>(cached);
  const [loading, setLoading] = useState(!cached);

  useEffect(() => {
    if (cached) return;
    fetch("https://api.frankfurter.dev/v1/latest?from=USD&to=EUR")
      .then((r) => r.json())
      .then((d) => {
        const usdToEur = d.rates.EUR as number;
        cached = { usdToEur, eurToUsd: 1 / usdToEur };
        setRates(cached);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  return { rates, loading };
}
