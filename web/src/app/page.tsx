"use client";

import { useEffect, useState } from "react";
import { api, type CollectionState, type DeckSummary, type Health } from "@/lib/api";
import { UploadPanel } from "@/components/upload-panel";
import { RankedDecks } from "@/components/ranked-decks";
import { DeckView } from "@/components/deck-view";

export default function Home() {
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [collection, setCollection] = useState<CollectionState | null>(null);
  const [decks, setDecks] = useState<DeckSummary[] | null>(null);
  const [openCommander, setOpenCommander] = useState<string | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealthError(true));
    api
      .getCollection()
      .then((c) => setCollection(c.uniqueCards > 0 ? c : null))
      .catch(() => setCollection(null));
  }, []);

  const hasCollection = (collection?.uniqueCards ?? 0) > 0;

  return (
    <main className="grid gap-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">EDH Deck Builder</h1>
        <p className="mt-1 text-sm dim">
          Chấm điểm commander theo synergy EDHREC và dựng decklist 99 card từ chính
          collection Archidekt của bạn.
        </p>
      </header>

      <SeedBanner health={health} failed={healthError} />

      <UploadPanel
        collection={collection}
        onChange={(next) => {
          setCollection(next);
          setDecks(null);
          setOpenCommander(null);
        }}
      />

      {hasCollection && (
        <RankedDecks decks={decks} onDecks={setDecks} onOpen={setOpenCommander} />
      )}

      {openCommander && (
        <DeckView commander={openCommander} onClose={() => setOpenCommander(null)} />
      )}

      <footer className="pt-4 text-xs dim">
        Dữ liệu card từ{" "}
        <a className="underline underline-offset-4" href="https://scryfall.com">
          Scryfall
        </a>{" "}
        · synergy từ{" "}
        <a className="underline underline-offset-4" href="https://edhrec.com">
          EDHREC
        </a>
        . Collection được lưu theo session ẩn danh trong 30 ngày, không cần đăng nhập.
      </footer>
    </main>
  );
}

/** Chừng nào seed chưa chạy xong thì phần chấm điểm sẽ không có gì để đọc —
 *  nói thẳng ra thay vì để người dùng bấm rồi nhận lỗi rỗng. */
function SeedBanner({ health, failed }: { health: Health | null; failed: boolean }) {
  if (failed)
    return (
      <p className="rounded-lg border border-rose-500/40 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
        Không kết nối được backend. Kiểm tra biến <code>DATABASE_URL</code> trong Vercel.
      </p>
    );

  if (!health || health.ready) return null;

  return (
    <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
      Dữ liệu nền chưa được seed ({health.seed.commanders} commander,{" "}
      {health.seed.commandersSeeded} đã có EDHREC). Chạy workflow{" "}
      <code className="font-mono">Seed dữ liệu</code> trên GitHub Actions rồi tải lại
      trang.
    </p>
  );
}
