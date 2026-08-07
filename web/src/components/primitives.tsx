import type { ScoreComponents } from "@/lib/api";

const GRADE_TONE: Record<string, string> = {
  A: "text-emerald-300 border-emerald-500/40 bg-emerald-500/10",
  B: "text-sky-300 border-sky-500/40 bg-sky-500/10",
  C: "text-amber-300 border-amber-500/40 bg-amber-500/10",
  D: "text-rose-300 border-rose-500/40 bg-rose-500/10",
};

export function GradeBadge({ grade, score }: { grade: string; score: number }) {
  return (
    <span
      className={`inline-flex items-baseline gap-1.5 rounded-md border px-2.5 py-1 font-mono text-sm font-semibold ${
        GRADE_TONE[grade] ?? GRADE_TONE.D
      }`}
    >
      {grade}
      <span className="text-xs opacity-70">{pct(score)}</span>
    </span>
  );
}

export function pct(n: number): string {
  return `${Math.round(n * 100)}%`;
}

export function usd(n: number): string {
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

const COMPONENT_LABEL: Record<keyof ScoreComponents, [string, number]> = {
  synergy: ["Synergy EDHREC", 40],
  coverage: ["Coverage", 20],
  curve: ["Mana curve", 15],
  chains: ["Synergy chains", 15],
  slotBalance: ["Slot balance", 10],
};

/** Breakdown 5 thành phần kèm trọng số — cho thấy điểm tổng đến từ đâu. */
export function ScoreBreakdown({ components }: { components: ScoreComponents }) {
  return (
    <dl className="grid gap-2">
      {(Object.keys(COMPONENT_LABEL) as (keyof ScoreComponents)[]).map((key) => {
        const [label, weight] = COMPONENT_LABEL[key];
        const value = components[key] ?? 0;
        return (
          <div key={key} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-1">
            <dt className="text-xs dim">
              {label} <span className="opacity-60">· {weight}%</span>
            </dt>
            <dd className="font-mono text-xs tabular-nums">{pct(value)}</dd>
            <div className="col-span-2 h-1 overflow-hidden rounded-full bg-[var(--bg-inset)]">
              <div
                className="h-full rounded-full bg-[var(--accent)]"
                style={{ width: `${Math.min(100, Math.max(0, value * 100))}%` }}
              />
            </div>
          </div>
        );
      })}
    </dl>
  );
}

export function Stat({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="inset-panel px-3 py-2">
      <div className="text-[0.68rem] uppercase tracking-wide dim">{label}</div>
      <div className="mt-0.5 font-mono text-base tabular-nums">{value}</div>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 text-sm dim">
      <span className="size-4 animate-spin rounded-full border-2 border-[var(--border-strong)] border-t-[var(--accent)]" />
      {label}
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      {children}
    </p>
  );
}
