"use client";

import { useRef, useState } from "react";
import { api, ApiError, type CollectionState } from "@/lib/api";
import { ErrorNote, Spinner, Stat } from "./primitives";

type Props = {
  collection: CollectionState | null;
  onChange: (next: CollectionState | null) => void;
};

export function UploadPanel({ collection, onChange }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function upload(file: File) {
    setBusy(true);
    setError(null);
    try {
      await api.importCsv(file);
      onChange(await api.getCollection());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Upload thất bại. Thử lại.");
    } finally {
      setBusy(false);
    }
  }

  async function clear() {
    setBusy(true);
    try {
      await api.clearCollection();
      onChange(null);
    } finally {
      setBusy(false);
    }
  }

  if (collection && collection.uniqueCards > 0) {
    return (
      <section className="surface p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide dim">
            Collection của bạn
          </h2>
          <button
            onClick={clear}
            disabled={busy}
            className="text-xs underline underline-offset-4 dim hover:text-[var(--text)] disabled:opacity-50"
          >
            Xoá và upload lại
          </button>
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Stat label="Unique cards" value={collection.uniqueCards.toLocaleString()} />
          <Stat label="Tổng bản" value={collection.totalCopies.toLocaleString()} />
        </div>
      </section>
    );
  }

  return (
    <section className="surface p-5">
      <h2 className="text-sm font-semibold uppercase tracking-wide dim">
        Bước 1 — Import collection
      </h2>
      <p className="mt-1.5 text-sm dim">
        Trên Archidekt: <span className="text-[var(--text)]">Collection → Export → CSV</span>.
        File cần có ít nhất hai cột <code className="font-mono">Quantity</code> và{" "}
        <code className="font-mono">Name</code>.
      </p>

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          const file = e.dataTransfer.files?.[0];
          if (file) void upload(file);
        }}
        className={`mt-4 rounded-xl border-2 border-dashed px-6 py-10 text-center transition-colors ${
          dragging
            ? "border-[var(--accent)] bg-[var(--accent)]/5"
            : "border-[var(--border-strong)]"
        }`}
      >
        {busy ? (
          <div className="flex justify-center">
            <Spinner label="Đang parse CSV và enrich dữ liệu Scryfall…" />
          </div>
        ) : (
          <>
            <p className="text-sm">Kéo thả file CSV vào đây</p>
            <button
              onClick={() => inputRef.current?.click()}
              className="mt-3 rounded-lg border border-[var(--border-strong)] bg-[var(--bg-inset)] px-4 py-2 text-sm font-medium hover:border-[var(--accent)]"
            >
              Hoặc chọn file
            </button>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void upload(file);
            e.target.value = "";
          }}
        />
      </div>

      {error && <div className="mt-3">{<ErrorNote>{error}</ErrorNote>}</div>}
    </section>
  );
}
