"use client";

import { useEffect, useState } from "react";
import { api, ApiError, type BuildResult, type Card } from "@/lib/api";
import { ErrorNote, GradeBadge, ScoreBreakdown, Spinner, pct, usd } from "./primitives";

const TABS = ["Decklist", "99 card", "Swap", "Buylist", "Mana base"] as const;
type Tab = (typeof TABS)[number];

export function DeckView({
  commander,
  onClose,
}: {
  commander: string;
  onClose: () => void;
}) {
  const [result, setResult] = useState<BuildResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("Decklist");

  useEffect(() => {
    let cancelled = false;
    setResult(null);
    setError(null);
    api
      .build(commander)
      .then((r) => !cancelled && setResult(r))
      .catch(
        (e) =>
          !cancelled &&
          setError(e instanceof ApiError ? e.message : "Build deck thất bại."),
      );
    return () => {
      cancelled = true;
    };
  }, [commander]);

  return (
    <section className="surface p-5">
      <div className="flex flex-wrap items-center gap-3">
        <h2 className="text-lg font-semibold">{commander}</h2>
        {result && (
          <GradeBadge
            grade={result.deck.score.grade}
            score={result.deck.score.composite}
          />
        )}
        <button
          onClick={onClose}
          className="ml-auto text-sm underline underline-offset-4 dim hover:text-[var(--text)]"
        >
          Đóng
        </button>
      </div>

      {!result && !error && (
        <div className="mt-6">
          <Spinner label="Đang build 99 card…" />
        </div>
      )}
      {error && <div className="mt-4">{<ErrorNote>{error}</ErrorNote>}</div>}

      {result && (
        <>
          <p className="mt-2 text-sm dim">{result.deck.score.summary}</p>

          <div className="mt-4 grid gap-5 lg:grid-cols-[260px_1fr]">
            <div className="grid content-start gap-4">
              <ScoreBreakdown components={result.deck.score.components} />
              <dl className="grid gap-1 text-xs dim">
                <Row
                  label="Owned"
                  value={`${result.deck.ownedCount}/${result.deck.cardCount}`}
                />
                <Row label="Thiếu" value={String(result.deck.missingCount)} />
                <Row
                  label="Tiền mua thêm"
                  value={
                    result.buylist.totalUsd > 0 ? usd(result.buylist.totalUsd) : "—"
                  }
                />
              </dl>
            </div>

            <div>
              <div className="flex flex-wrap gap-1 border-b border-[var(--border)]">
                {TABS.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTab(t)}
                    className={`-mb-px border-b-2 px-3 py-2 text-sm ${
                      tab === t
                        ? "border-[var(--accent)] text-[var(--text)]"
                        : "border-transparent dim hover:text-[var(--text)]"
                    }`}
                  >
                    {t}
                  </button>
                ))}
              </div>
              <div className="pt-4">
                {tab === "Decklist" && <DecklistTab text={result.decklist} />}
                {tab === "99 card" && <CardsTab cards={result.deck.cards} />}
                {tab === "Swap" && <SwapTab swaps={result.swaps.swaps} />}
                {tab === "Buylist" && <BuylistTab buylist={result.buylist} />}
                {tab === "Mana base" && <ManaTab deck={result.deck} />}
              </div>
            </div>
          </div>
        </>
      )}
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd className="font-mono tabular-nums text-[var(--text)]">{value}</dd>
    </div>
  );
}

function DecklistTab({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="flex gap-2">
        <button
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          }}
          className="rounded-lg border border-[var(--border-strong)] px-3 py-1.5 text-sm hover:border-[var(--accent)]"
        >
          {copied ? "Đã copy" : "Copy cho Moxfield"}
        </button>
      </div>
      <pre className="inset-panel mt-3 max-h-[28rem] overflow-auto p-3 font-mono text-xs leading-relaxed">
        {text}
      </pre>
    </div>
  );
}

function CardsTab({ cards }: { cards: Card[] }) {
  const bySlot = cards.reduce<Record<string, Card[]>>((acc, c) => {
    (acc[c.slot] ??= []).push(c);
    return acc;
  }, {});

  return (
    <div className="grid gap-4 sm:grid-cols-2">
      {Object.entries(bySlot).map(([slot, list]) => (
        <div key={slot}>
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wide dim">
            {slot} · {list.length}
          </h4>
          <ul className="grid gap-0.5 text-sm">
            {list
              .slice()
              .sort((a, b) => b.synergy - a.synergy)
              .map((c) => (
                <li key={c.name} className="flex items-baseline gap-2">
                  <span className={c.isOwned ? "" : "dim italic"}>{c.name}</span>
                  {!c.isOwned && (
                    <span className="text-[0.65rem] uppercase text-amber-400/80">
                      thiếu
                    </span>
                  )}
                  <span className="ml-auto font-mono text-xs tabular-nums dim">
                    {pct(c.synergy)}
                  </span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function SwapTab({ swaps }: { swaps: BuildResult["swaps"]["swaps"] }) {
  if (swaps.length === 0)
    return <p className="text-sm dim">Không tìm thấy swap nào cải thiện được deck.</p>;

  return (
    <ul className="grid gap-2">
      {swaps.map((s) => (
        <li key={`${s.outCard}->${s.inCard}`} className="inset-panel px-3 py-2 text-sm">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="dim line-through">{s.outCard}</span>
            <span className="dim">→</span>
            <span className="font-medium">{s.inCard}</span>
            <span className="ml-auto font-mono text-xs text-emerald-300">
              +{pct(s.synergyDelta)}
            </span>
          </div>
          <p className="mt-1 text-xs dim">{s.reason}</p>
        </li>
      ))}
    </ul>
  );
}

function BuylistTab({ buylist }: { buylist: BuildResult["buylist"] }) {
  if (buylist.items.length === 0)
    return <p className="text-sm dim">Bạn đã sở hữu toàn bộ 99 card. Không cần mua gì.</p>;

  return (
    <div>
      <p className="text-sm dim">
        {buylist.items.length} card thiếu · tổng{" "}
        <span className="font-mono text-[var(--text)]">{usd(buylist.totalUsd)}</span>
        {buylist.unpricedCount > 0 && (
          <span> · {buylist.unpricedCount} card chưa có giá</span>
        )}
      </p>
      <ul className="mt-3 grid gap-0.5 text-sm">
        {buylist.items.map((c) => (
          <li key={c.name} className="flex items-baseline gap-2">
            <span>{c.name}</span>
            <span className="text-xs dim">{c.slot}</span>
            <span className="ml-auto font-mono text-xs tabular-nums dim">
              {pct(c.synergy)}
            </span>
            <span className="w-16 text-right font-mono text-xs tabular-nums">
              {c.priceUsd ? usd(c.priceUsd) : "—"}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const MANA_BG: Record<string, string> = {
  W: "bg-mana-w",
  U: "bg-mana-u",
  B: "bg-mana-b",
  R: "bg-mana-r",
  G: "bg-mana-g",
};

function ManaTab({ deck }: { deck: BuildResult["deck"] }) {
  if (!deck.manaBase)
    return <p className="text-sm dim">Deck này không có basic land để phân bổ.</p>;

  return (
    <div>
      <p className="font-mono text-xs dim">{deck.manaBase.summary}</p>
      <ul className="mt-4 grid gap-3">
        {deck.manaBase.basics.map((b) => (
          <li key={b.color} className="grid grid-cols-[auto_1fr_auto] items-center gap-3">
            <span className="w-20 text-sm">{b.basic}</span>
            <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-inset)]">
              <div
                className={`h-full rounded-full ${MANA_BG[b.color] ?? "bg-neutral-400"}`}
                style={{ width: `${Math.max(2, b.pipRatio * 100)}%` }}
              />
            </div>
            <span className="font-mono text-xs tabular-nums dim">
              ×{b.count} · {pct(b.pipRatio)} pip
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-4 text-xs dim">
        Basic land được chia theo tỉ lệ pip mana thực tế của 99 card (Largest Remainder
        Method), không chia đều theo số màu.
      </p>
    </div>
  );
}
