import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "EDH Deck Builder — build Commander decks from your collection",
  description:
    "Phân tích collection Archidekt của bạn, chấm điểm commander theo synergy EDHREC, và gợi ý decklist 99 card tối ưu.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="vi">
      <body className="min-h-dvh">
        <div className="relative mx-auto max-w-6xl px-5 py-8 sm:px-8">{children}</div>
      </body>
    </html>
  );
}
