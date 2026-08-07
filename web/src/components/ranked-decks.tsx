"use client";

import { useState } from "react";
import { api, ApiError, type DeckSummary } from "@/lib/api";
import { ErrorNote, GradeBadge, ScoreBreakdown, Spinner, pct, usd } from "./primitives";

type Props = {
  decks: DeckSummary[] | null;
  onDecks: (decks: DeckSummary[]) => void;
  onOpen: (commander: string) => void;
};

export function RankedDecks({ decks, onDecks, onOpen }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [top, setTop] = useState(5);
  const [ownedOnly, setOwnedOnly] = useState(true);
  const [elapsed, setElapsed] = useState<number | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const res = await api.rank(top, ownedOnly);
      onDecks(res.decks);
      setElapsed(res.elapsedMs);
      if (res.decks.length === 0) {
        setError(
          "Không build được deck nào — dữ liệu EDHREC cho các commander này chưa được seed.",
        );
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Chấm điểm thất bại.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="surface p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide dim">
        Bước 2 — Chấm điểm commander
      </h2>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <label className="flex items-center gap-2 text-sm">
          Top
          <select
            value={top}
            onChange={(e) => setTop(Number(e.target.value))}
            className="inset-panel px-2 py-1 font-mono text-sm"
          >
            {[3, 5, 10, 15].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={ownedOnly}
            onChange={(e) => setOwnedOnly(e.target.checked)}
            className="size-4 accent-[var(--accent)]"
          />
          Chỉ commander đang sở hữu
        </label>

        <button
          onClick={run}
          disabled={busy}
          className="ml-auto rounded-lg border border-[var(--accent)] bg-[var(--accent)]/15 px-4 py-2 text-sm font-medium text-[var(--text)] hover:bg-[var(--accent)]/25 disabled:opacity-50"
        >
          {decks ? "Chấm lại" : "Chấm điểm"}
        </button>
      </div>

      {busy && (
        <div className="mt-4">
          <Spinner label="Đang lọc theo color identity, chấm điểm và build deck…" />
        </div>
      )}
      {error && <div className="mt-4">{<ErrorNote>{error}</ErrorNote>}</div>}

      {decks && decks.length > 0 && (
        <>
          {elapsed !== null && (
            <p className="mt-4 text-xs dim">
              {decks.length} deck trong {(elapsed / 1000).toFixed(1)}s
            </p>
          )}
          <ul className="mt-2 grid gap-3">
            {decks.map((deck, i) => (
              <DeckRow key={deck.slug} rank={i + 1} deck={deck} onOpen={onOpen} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

function DeckRow({
  rank,
  deck,
  onOpen,
}: {
  rank: number;
  deck: DeckSummary;
  onOpen: (commander: string) => void;
}) {
  const [open, setOpen] = useState(false);

  return (
    <li className="inset-panel overflow-hidden">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
        <span className="font-mono text-sm dim">#{rank}</span>
        <button
          onClick={() => onOpen(deck.commander)}
          className="text-left text-base font-semibold underline-offset-4 hover:underline"
        >
          {deck.commander}
        </button>
        <GradeBadge grade={deck.score.grade} score={deck.score.composite} />
        {deck.score.archetype && (
          <span className="rounded border border-[var(--border-strong)] px-2 py-0.5 text-xs uppercase tracking-wide dim">
            {deck.score.archetype.label}
          </span>
        )}
        <span className="ml-auto flex items-center gap-4 font-mono text-xs tabular-nums dim">
          <span>
            {deck.ownedCount}/{deck.cardCount} owned
          </span>
          <span>{deck.totalPriceMissing > 0 ? usd(deck.totalPriceMissing) : "—"}</span>
          <button
            onClick={() => setOpen((v) => !v)}
            className="underline underline-offset-4 hover:text-[var(--text)]"
          >
            {open ? "Ẩn" : "Chi tiết"}
          </button>
        </span>
      </div>

      {open && (
        <div className="grid gap-5 border-t border-[var(--border)] px-4 py-4 sm:grid-cols-2">
          <div>
            <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide dim">
              Điểm thành phần
            </h4>
            <ScoreBreakdown components={deck.score.components} />
          </div>

          <div className="grid gap-3 text-sm">
            {deck.score.archetype && (
              <p className="dim">
                <span className="text-[var(--text)]">
                  {deck.score.archetype.label.toUpperCase()}
                </span>{" "}
                ({pct(deck.score.archetype.confidence)} tin cậy) —{" "}
                {deck.score.archetype.description}
              </p>
            )}
            {deck.score.curve && (
              <p className="dim">
                Curve trung bình{" "}
                <span className="font-mono text-[var(--text)]">
                  {deck.score.curve.avgCmc.toFixed(1)}
                </span>{" "}
                — {deck.score.curve.verdict}
              </p>
            )}
            {deck.score.chains?.dominantTheme && (
              <p className="dim">
                Theme{" "}
                <span className="text-[var(--text)]">
                  {deck.score.chains.dominantTheme}
                </span>{" "}
                · {deck.score.chains.pairCount} synergy pair
              </p>
            )}
            {deck.keyCards.length > 0 && (
              <p className="dim">
                <span className="text-[var(--text)]">Card chủ lực:</span>{" "}
                {deck.keyCards.map((c) => c.name).join(", ")}
              </p>
            )}
            <button
              onClick={() => onOpen(deck.commander)}
              className="justify-self-start rounded-lg border border-[var(--border-strong)] px-3 py-1.5 text-sm hover:border-[var(--accent)]"
            >
              Mở decklist đầy đủ →
            </button>
          </div>
        </div>
      )}
    </li>
  );
}
