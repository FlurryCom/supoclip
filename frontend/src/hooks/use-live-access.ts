"use client";

import { useEffect, useState } from "react";

// One request per page load. The backend answers 404 while the live_clipping flag is off.
let pending: Promise<boolean> | null = null;

/** Whether live clipping is available to the signed-in user (null while loading). */
export function useLiveAccess(enabled = true) {
  const [access, setAccess] = useState<boolean | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    pending ??= fetch("/api/live/channels", { cache: "no-store" })
      .then((response) => response.ok)
      .catch(() => false);
    void pending.then((value) => {
      if (!cancelled) setAccess(value);
    });
    return () => {
      cancelled = true;
    };
  }, [enabled]);
  return access;
}
