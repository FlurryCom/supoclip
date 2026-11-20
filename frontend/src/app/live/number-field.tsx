"use client";

import { useEffect, useState } from "react";

import { Input } from "@/components/ui/input";

// A small whole-number field that saves on Enter or when it loses focus.
export function NumberField({ value, min, max, label, suffix, onCommit }: {
  value: number;
  min: number;
  max: number;
  label: string;
  suffix: string;
  onCommit: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const next = Math.round(Number(draft));
    if (Number.isFinite(next) && next >= min && next <= max && next !== value) {
      onCommit(next);
    } else {
      setDraft(String(value));
    }
  };
  return (
    <div className="flex items-center gap-2">
      <Input
        aria-label={label}
        type="number"
        min={min}
        max={max}
        className="h-8 w-20"
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={(event) => { if (event.key === "Enter") commit(); }}
      />
      <span className="text-sm text-muted-foreground">{suffix}</span>
    </div>
  );
}
