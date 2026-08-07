/** Kiểu dữ liệu khớp với serializers.py và client gọi API. */

export type ScoreComponents = {
  synergy: number;
  coverage: number;
  curve: number;
  chains: number;
  slotBalance: number;
};

export type DeckScore = {
  grade: "A" | "B" | "C" | "D";
  composite: number;
  summary: string;
  components: ScoreComponents;
  archetype: { label: string; confidence: number; description: string } | null;
  curve: {
    avgCmc: number;
    distribution: Record<string, number>;
    verdict: string;
    archetypeFit: string;
  } | null;
  chains: {
    dominantTheme: string | null;
    pairCount: number;
    topPairs: string[];
  } | null;
};

export type Card = {
  name: string;
  slot: string;
  synergy: number;
  isOwned: boolean;
  cmc: number;
  typeLine: string;
  priceUsd: number | null;
};

export type DeckSummary = {
  commander: string;
  slug: string;
  score: DeckScore;
  cardCount: number;
  ownedCount: number;
  missingCount: number;
  totalPriceMissing: number;
  keyCards: Card[];
  topMissing: Card[];
  chainBuffs: { name: string; multiplier: number }[];
};

export type ManaBase = {
  summary: string;
  basics: { color: string; basic: string; count: number; pipRatio: number }[];
};

export type DeckDetail = DeckSummary & {
  cards: Card[];
  missingCards: Card[];
  manaBase: ManaBase | null;
  curveSummary: Record<string, number>;
};

export type Swap = {
  outCard: string;
  inCard: string;
  slot: string;
  synergyOut: number;
  synergyIn: number;
  synergyDelta: number;
  reason: string;
};

export type BuildResult = {
  deck: DeckDetail;
  decklist: string;
  swaps: { commander: string; swaps: Swap[] };
  buylist: {
    commander: string;
    items: Card[];
    totalUsd: number;
    pricedCount: number;
    unpricedCount: number;
  };
};

export type Health = {
  status: string;
  database: string;
  ready: boolean;
  /** Chỉ có khi database hỏng — lý do đã được scrub khỏi DSN. */
  error?: string | null;
  dsnVar?: string | null;
  seed?: {
    commanders: number;
    commandersSeeded: number;
    bannedCards: number;
    scryfallCards: number;
    edhrecRows: number;
    lastSeeded: string | null;
  };
};

export type CollectionState = {
  cards: { name: string; quantity: number }[];
  uniqueCards: number;
  totalCopies: number;
};

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (!res.ok) {
    let detail = `Lỗi ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      // Response không phải JSON — giữ nguyên thông báo mặc định.
    }
    throw new ApiError(detail, res.status);
  }
  return res.json() as Promise<T>;
}

export const api = {
  // 503 vẫn trả body chẩn đoán có ích, nên đọc thẳng thay vì để request() ném.
  health: async (): Promise<Health> => {
    const res = await fetch("/api/health", { credentials: "same-origin" });
    return res.json() as Promise<Health>;
  },

  getCollection: () => request<CollectionState>("/api/collection"),

  clearCollection: () =>
    request<{ cleared: boolean }>("/api/collection", { method: "DELETE" }),

  importCsv: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{
      imported: number;
      uniqueCards: number;
      totalCopies: number;
      enrichedFromScryfall: number;
    }>("/api/collection/import", { method: "POST", body: form });
  },

  rank: (top: number, ownedOnly: boolean) =>
    request<{
      decks: DeckSummary[];
      /** Số commander thực sự được chấm điểm, không phải số deck trả về. */
      candidatesScored: number;
      /** Số còn lại sau từng bước lọc — dùng để chẩn đoán khi kết quả rỗng. */
      funnel: Partial<{
        totalCommanders: number;
        afterOwnershipFilter: number;
        afterColorFilter: number;
        scored: number;
      }>;
      elapsedMs: number;
    }>(`/api/commanders/rank?top=${top}&ownedOnly=${ownedOnly}`),

  build: (commander: string, partner?: string) =>
    request<BuildResult>("/api/decks/build", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ commander, partner: partner || null }),
    }),
};
