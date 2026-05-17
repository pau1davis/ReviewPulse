// ── Types (mirror backend Pydantic schemas) ────────────────────────────────────

export interface JobSummary {
  job_id: string;
  status: "queued" | "running" | "completed" | "failed" | "partial";
  reviews_found: number;
  reviews_processed: number;
}

export interface BookMetrics {
  review_count: number;
  avg_rating: number | null;
  sentiment_breakdown: { positive: number; mixed: number; negative: number };
  total_cost_usd: number;
  latest_job: JobSummary | null;
}

export interface Book {
  id: string;
  title: string;
  isbn: string | null;
  url: string | null;
  created_at: string;
  metrics: BookMetrics;
}

export interface AddBookResponse {
  book: Book;
  job_id: string;
}

export interface JobStatus {
  job_id: string;
  book_id: string;
  book_title: string;
  status: "queued" | "running" | "completed" | "failed" | "partial";
  reviews_found: number;
  reviews_processed: number;
  error: string | null;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
}

export interface AnalysisSummary {
  sentiment: "positive" | "mixed" | "negative";
  sentiment_confidence: number;
  themes: string[];
  is_ai_generated: boolean;
  ai_generated_confidence: number;
  summary: string;
  is_actionable: boolean;
  tokens_used: number;
  cost_usd: number;
}

export interface Review {
  id: string;
  external_id: string;
  reviewer_name: string | null;
  rating: number | null;
  body: string;
  review_date: string | null;
  analysis: AnalysisSummary | null;
}

export interface PaginatedReviews {
  total: number;
  page: number;
  page_size: number;
  results: Review[];
}

export interface SentimentWeek {
  week: string;
  positive: number;
  mixed: number;
  negative: number;
  total: number;
  delta_positive: number;
  delta_negative: number;
}

export interface ThemeWeek {
  week: string;
  theme: string;
  count: number;
}

export interface BookComparison {
  book_id: string;
  title: string;
  review_count: number;
  avg_rating: number | null;
  sentiment_distribution: { positive: number; mixed: number; negative: number };
  top_themes: string[];
  ai_flagged_rate: number;
  reviews_per_week: number;
}

export interface SearchResult {
  review_id: string;
  book_id: string;
  book_title: string;
  snippet: string;
  score: number;
  reviewer_name: string | null;
  sentiment: string | null;
}

export interface DigestReview {
  review_id: string;
  book_title: string;
  reviewer_name: string | null;
  rating: number | null;
  snippet: string;
  sentiment: string;
  is_actionable: boolean;
  summary: string;
}

export interface DigestResponse {
  period_start: string;
  period_end: string;
  total_new_reviews: number;
  overall_sentiment_shift: "improving" | "declining" | "stable";
  rising_themes: string[];
  urgent_reviews: DigestReview[];
  books: {
    book_id: string;
    title: string;
    new_review_count: number;
    positive: number;
    mixed: number;
    negative: number;
    top_actionable: DigestReview[];
    ai_flagged_count: number;
  }[];
}

export interface SinceLastLoginResponse {
  last_seen_at: string;
  total_new_reviews: number;
  negative_reviews: DigestReview[];
  actionable_reviews: DigestReview[];
  ai_flagged_count: number;
  review_count_by_book: Record<string, number>;
}

// ── API client ────────────────────────────────────────────────────────────────

// In dev: Vite proxies /api → localhost:8000 (see vite.config.ts).
// In production: VITE_API_URL should be the full backend URL, e.g.
// https://reviewpulse-api.onrender.com (no trailing slash).
const BASE = import.meta.env.VITE_API_URL ?? "/api";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? "Request failed");
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const api = {
  auth: {
    register: (email: string, password: string) =>
      request<{ author_id: string; email: string }>("/auth/register", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      }),

    login: (email: string, password: string) =>
      request<{ access_token: string; expires_in: number; author_id: string }>(
        "/auth/login",
        { method: "POST", body: JSON.stringify({ email, password }) },
      ),
  },

  // ── Books ────────────────────────────────────────────────────────────────────

  books: {
    list: (token: string) => request<Book[]>("/books", {}, token),

    get: (token: string, bookId: string) =>
      request<Book>(`/books/${bookId}`, {}, token),

    add: (token: string, payload: { title: string; isbn?: string; url?: string }) =>
      request<AddBookResponse>("/books", {
        method: "POST",
        body: JSON.stringify(payload),
      }, token),
  },

  // ── Jobs ─────────────────────────────────────────────────────────────────────

  jobs: {
    status: (token: string, jobId: string) =>
      request<JobStatus>(`/jobs/${jobId}`, {}, token),
  },

  // ── Reviews ──────────────────────────────────────────────────────────────────

  reviews: {
    list: (
      token: string,
      bookId: string,
      params: {
        sentiment?: string;
        is_actionable?: boolean;
        is_ai_generated?: boolean;
        theme?: string;
        page?: number;
        page_size?: number;
        sort_by?: string;
        sort_order?: string;
      } = {},
    ) => {
      const qs = new URLSearchParams(
        Object.entries(params)
          .filter(([, v]) => v !== undefined)
          .map(([k, v]) => [k, String(v)]),
      ).toString();
      return request<PaginatedReviews>(
        `/books/${bookId}/reviews${qs ? `?${qs}` : ""}`,
        {},
        token,
      );
    },
  },

  // ── Trends ───────────────────────────────────────────────────────────────────

  trends: {
    sentiment: (token: string, bookId: string) =>
      request<{ book_id: string; series: SentimentWeek[] }>(
        `/books/${bookId}/trends/sentiment`,
        {},
        token,
      ),

    themes: (token: string, bookId: string) =>
      request<{ book_id: string; series: ThemeWeek[] }>(
        `/books/${bookId}/trends/themes`,
        {},
        token,
      ),
  },

  // ── Comparison ───────────────────────────────────────────────────────────────

  compare: (token: string, bookIds: string[]) =>
    request<{ books: BookComparison[] }>("/authors/me/compare", {
      method: "POST",
      body: JSON.stringify({ book_ids: bookIds }),
    }, token),

  // ── Search ───────────────────────────────────────────────────────────────────

  search: (token: string, query: string, k = 10) =>
    request<SearchResult[]>("/authors/me/search", {
      method: "POST",
      body: JSON.stringify({ query, k }),
    }, token),

  // ── Digest + since-last-login ─────────────────────────────────────────────

  digest: (token: string) =>
    request<DigestResponse>("/authors/me/digest", {}, token),

  sinceLastLogin: (token: string) =>
    request<SinceLastLoginResponse>("/authors/me/since-last-login", {}, token),
};

export { ApiError };
