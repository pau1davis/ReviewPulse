import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import * as Tabs from "@radix-ui/react-tabs";
import {
  AlertTriangle,
  ArrowLeft,
  Bot,
  ChevronLeft,
  ChevronRight,
  Loader2,
  Zap,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import {
  api,
  ApiError,
  type Book,
  type DigestResponse,
  type PaginatedReviews,
  type SentimentWeek,
  type ThemeWeek,
} from "@/lib/api";
import SentimentChart from "@/components/SentimentChart";
import ThemeChart from "@/components/ThemeChart";

// ── Helpers ───────────────────────────────────────────────────────────────────

const SENTIMENT_COLORS = {
  positive: "bg-green-100 text-green-700",
  mixed: "bg-yellow-100 text-yellow-700",
  negative: "bg-red-100 text-red-700",
} as const;

function SentimentBadge({ s }: { s: string }) {
  const cls =
    SENTIMENT_COLORS[s as keyof typeof SENTIMENT_COLORS] ??
    "bg-muted text-muted-foreground";
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}>
      {s}
    </span>
  );
}

function Stars({ rating }: { rating: number | null }) {
  if (rating === null) return null;
  return (
    <span className="text-xs text-yellow-500">
      {"★".repeat(Math.round(rating))}
      {"☆".repeat(5 - Math.round(rating))}
    </span>
  );
}

// ── Reviews tab ───────────────────────────────────────────────────────────────

function ReviewsTab({ bookId, token }: { bookId: string; token: string }) {
  const [data, setData] = useState<PaginatedReviews | null>(null);
  const [loading, setLoading] = useState(true);

  // Filters
  const [sentiment, setSentiment] = useState("");
  const [isActionable, setIsActionable] = useState(false);
  const [isAiGenerated, setIsAiGenerated] = useState(false);
  const [theme, setTheme] = useState("");
  const [sortBy, setSortBy] = useState("review_date");
  const [sortOrder, setSortOrder] = useState("desc");
  const [page, setPage] = useState(1);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.reviews
      .list(token, bookId, {
        sentiment: sentiment || undefined,
        is_actionable: isActionable || undefined,
        is_ai_generated: isAiGenerated || undefined,
        theme: theme || undefined,
        sort_by: sortBy,
        sort_order: sortOrder,
        page,
        page_size: 10,
      })
      .then((d) => { if (!cancelled) setData(d); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [bookId, token, sentiment, isActionable, isAiGenerated, theme, sortBy, sortOrder, page]);

  // Reset to page 1 on filter change
  function resetPage() { setPage(1); }

  const totalPages = data ? Math.ceil(data.total / 10) : 1;

  return (
    <div className="space-y-4">
      {/* Filter bar */}
      <div className="flex flex-wrap items-end gap-3 rounded-lg border border-border bg-card p-4">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Sentiment
          </label>
          <select
            value={sentiment}
            onChange={(e) => { setSentiment(e.target.value); resetPage(); }}
            className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="">All</option>
            <option value="positive">Positive</option>
            <option value="mixed">Mixed</option>
            <option value="negative">Negative</option>
          </select>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Sort by
          </label>
          <div className="flex gap-1">
            <select
              value={sortBy}
              onChange={(e) => { setSortBy(e.target.value); resetPage(); }}
              className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="review_date">Date</option>
              <option value="rating">Rating</option>
              <option value="sentiment_confidence">Confidence</option>
            </select>
            <select
              value={sortOrder}
              onChange={(e) => { setSortOrder(e.target.value); resetPage(); }}
              className="rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
            >
              <option value="desc">↓</option>
              <option value="asc">↑</option>
            </select>
          </div>
        </div>

        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Theme
          </label>
          <input
            value={theme}
            onChange={(e) => { setTheme(e.target.value); resetPage(); }}
            placeholder="e.g. pacing"
            className="w-32 rounded-md border border-input bg-background px-2 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>

        <label className="flex cursor-pointer items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={isActionable}
            onChange={(e) => { setIsActionable(e.target.checked); resetPage(); }}
            className="rounded"
          />
          Actionable only
        </label>

        <label className="flex cursor-pointer items-center gap-1.5 text-sm">
          <input
            type="checkbox"
            checked={isAiGenerated}
            onChange={(e) => { setIsAiGenerated(e.target.checked); resetPage(); }}
            className="rounded"
          />
          AI-generated only
        </label>

        {data && (
          <span className="ml-auto text-xs text-muted-foreground">
            {data.total} review{data.total !== 1 ? "s" : ""}
          </span>
        )}
      </div>

      {/* Review list */}
      {loading ? (
        <div className="flex h-40 items-center justify-center">
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        </div>
      ) : data?.results.length === 0 ? (
        <p className="py-10 text-center text-sm text-muted-foreground">
          No reviews match your filters.
        </p>
      ) : (
        <div className="space-y-3">
          {data?.results.map((review) => (
            <div
              key={review.id}
              className="rounded-lg border border-border bg-card p-4"
            >
              {/* Header row */}
              <div className="mb-2 flex flex-wrap items-center gap-2">
                <span className="font-medium text-sm">
                  {review.reviewer_name ?? "Anonymous"}
                </span>
                <Stars rating={review.rating} />
                {review.review_date && (
                  <span className="text-xs text-muted-foreground">
                    {new Date(review.review_date).toLocaleDateString()}
                  </span>
                )}
                {review.analysis && (
                  <SentimentBadge s={review.analysis.sentiment} />
                )}
                {review.analysis?.is_actionable && (
                  <span className="inline-flex items-center gap-0.5 rounded-full bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                    <Zap className="h-3 w-3" />
                    Actionable
                  </span>
                )}
                {review.analysis?.is_ai_generated && (
                  <span className="inline-flex items-center gap-0.5 rounded-full bg-purple-100 px-2 py-0.5 text-xs font-medium text-purple-700">
                    <Bot className="h-3 w-3" />
                    AI-generated
                  </span>
                )}
              </div>

              {/* Body */}
              <p className="mb-2 text-sm leading-relaxed text-foreground line-clamp-3">
                {review.body}
              </p>

              {/* Analysis summary + themes */}
              {review.analysis && (
                <div className="space-y-1.5">
                  {review.analysis.summary && (
                    <p className="text-xs italic text-muted-foreground">
                      "{review.analysis.summary}"
                    </p>
                  )}
                  {review.analysis.themes.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {review.analysis.themes.map((t) => (
                        <span
                          key={t}
                          className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                        >
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-2 pt-2">
          <button
            onClick={() => setPage((p) => Math.max(1, p - 1))}
            disabled={page === 1}
            className="rounded-md border border-border p-1.5 text-sm hover:bg-muted disabled:opacity-40"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
          <span className="text-sm text-muted-foreground">
            Page {page} of {totalPages}
          </span>
          <button
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            disabled={page === totalPages}
            className="rounded-md border border-border p-1.5 text-sm hover:bg-muted disabled:opacity-40"
          >
            <ChevronRight className="h-4 w-4" />
          </button>
        </div>
      )}
    </div>
  );
}

// ── Trends tab ────────────────────────────────────────────────────────────────

function TrendsTab({ bookId, token }: { bookId: string; token: string }) {
  const [sentimentSeries, setSentimentSeries] = useState<SentimentWeek[]>([]);
  const [themeSeries, setThemeSeries] = useState<ThemeWeek[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      api.trends.sentiment(token, bookId),
      api.trends.themes(token, bookId),
    ])
      .then(([s, t]) => {
        setSentimentSeries(s.series);
        setThemeSeries(t.series);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [bookId, token]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (sentimentSeries.length === 0) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        Not enough data yet. Trends appear once reviews have been processed.
      </p>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h3 className="mb-3 font-medium">Sentiment over time</h3>
        <SentimentChart series={sentimentSeries} />
      </div>
      <div>
        <h3 className="mb-3 font-medium">Theme frequency by week</h3>
        <ThemeChart series={themeSeries} />
      </div>
    </div>
  );
}

// ── Digest tab ────────────────────────────────────────────────────────────────

function DigestTab({
  bookId,
  token,
}: {
  bookId: string;
  token: string;
}) {
  const [digest, setDigest] = useState<DigestResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .digest(token)
      .then(setDigest)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  const bookDigest = digest?.books.find((b) => b.book_id === bookId);

  if (!digest || !bookDigest) {
    return (
      <p className="py-16 text-center text-sm text-muted-foreground">
        No digest data available yet.
      </p>
    );
  }

  const shiftColor =
    digest.overall_sentiment_shift === "improving"
      ? "text-green-600"
      : digest.overall_sentiment_shift === "declining"
        ? "text-red-600"
        : "text-muted-foreground";

  return (
    <div className="space-y-6">
      {/* Period summary */}
      <div className="rounded-lg border border-border bg-card p-5">
        <p className="mb-1 text-xs text-muted-foreground">
          {new Date(digest.period_start).toLocaleDateString()} –{" "}
          {new Date(digest.period_end).toLocaleDateString()}
        </p>
        <div className="flex flex-wrap gap-6 text-sm">
          <span>
            <span className="font-semibold">{bookDigest.new_review_count}</span>{" "}
            new reviews
          </span>
          <span className={`font-semibold capitalize ${shiftColor}`}>
            {digest.overall_sentiment_shift}
          </span>
          {bookDigest.ai_flagged_count > 0 && (
            <span className="flex items-center gap-1 text-purple-600">
              <Bot className="h-4 w-4" />
              {bookDigest.ai_flagged_count} AI-flagged
            </span>
          )}
        </div>
      </div>

      {/* Sentiment breakdown */}
      <div className="grid grid-cols-3 gap-3">
        {(
          [
            ["Positive", bookDigest.positive, "text-green-600"],
            ["Mixed", bookDigest.mixed, "text-yellow-600"],
            ["Negative", bookDigest.negative, "text-red-600"],
          ] as const
        ).map(([label, count, cls]) => (
          <div
            key={label}
            className="rounded-lg border border-border bg-card p-4 text-center"
          >
            <p className={`text-2xl font-semibold ${cls}`}>{count}</p>
            <p className="text-xs text-muted-foreground">{label}</p>
          </div>
        ))}
      </div>

      {/* Rising themes */}
      {digest.rising_themes.length > 0 && (
        <div>
          <h3 className="mb-2 font-medium">Rising themes</h3>
          <div className="flex flex-wrap gap-2">
            {digest.rising_themes.map((t) => (
              <span
                key={t}
                className="rounded-full bg-blue-100 px-3 py-1 text-sm text-blue-700"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Top actionable */}
      {bookDigest.top_actionable.length > 0 && (
        <div>
          <h3 className="mb-2 font-medium">
            <span className="inline-flex items-center gap-1">
              <AlertTriangle className="h-4 w-4 text-yellow-500" />
              Needs attention
            </span>
          </h3>
          <div className="space-y-3">
            {bookDigest.top_actionable.map((r) => (
              <div
                key={r.review_id}
                className="rounded-lg border border-border bg-card p-4"
              >
                <div className="mb-1 flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {r.reviewer_name ?? "Anonymous"}
                  </span>
                  <Stars rating={r.rating} />
                  <SentimentBadge s={r.sentiment} />
                </div>
                <p className="text-sm text-muted-foreground line-clamp-2">
                  {r.snippet}
                </p>
                {r.summary && (
                  <p className="mt-1 text-xs italic text-muted-foreground">
                    "{r.summary}"
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const TABS = [
  { value: "reviews", label: "Reviews" },
  { value: "trends", label: "Trends" },
  { value: "digest", label: "Digest" },
];

export default function BookDetail() {
  const { bookId } = useParams<{ bookId: string }>();
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [book, setBook] = useState<Book | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState("reviews");

  // Track which tabs have been mounted (lazy render)
  const [mounted, setMounted] = useState(new Set(["reviews"]));

  const handleAuthError = useCallback(() => {
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  useEffect(() => {
    if (!bookId) return;
    api.books
      .get(token!, bookId)
      .then(setBook)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) handleAuthError();
        else navigate("/", { replace: true }); // 404 or other — go home
      })
      .finally(() => setLoading(false));
  }, [bookId, token, handleAuthError, navigate]);

  function handleTabChange(value: string) {
    setTab(value);
    setMounted((prev) => new Set([...prev, value]));
  }

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (!book) return null;

  const job = book.metrics.latest_job;

  return (
    <div className="space-y-6">
      {/* Back + header */}
      <div>
        <Link
          to="/"
          className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Catalog
        </Link>

        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h1 className="text-2xl font-semibold">{book.title}</h1>
            {book.isbn && (
              <p className="mt-0.5 text-sm text-muted-foreground">
                ISBN {book.isbn}
              </p>
            )}
            {book.url && (
              <a
                href={book.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline"
              >
                View store page ↗
              </a>
            )}
          </div>

          <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
            <span>{book.metrics.review_count} reviews</span>
            {book.metrics.avg_rating !== null && (
              <span>★ {book.metrics.avg_rating.toFixed(1)} avg</span>
            )}
            {job && (
              <span>
                Job:{" "}
                <span className="font-medium capitalize">{job.status}</span>
                {job.status !== "completed" &&
                  ` (${job.reviews_processed}/${job.reviews_found})`}
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs.Root value={tab} onValueChange={handleTabChange}>
        <Tabs.List className="flex gap-1 border-b border-border">
          {TABS.map(({ value, label }) => (
            <Tabs.Trigger
              key={value}
              value={value}
              className="px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground data-[state=active]:border-b-2 data-[state=active]:border-primary data-[state=active]:text-foreground"
            >
              {label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>

        <div className="pt-6">
          <Tabs.Content value="reviews" forceMount>
            <div className={tab !== "reviews" ? "hidden" : ""}>
              {mounted.has("reviews") && bookId && (
                <ReviewsTab bookId={bookId} token={token!} />
              )}
            </div>
          </Tabs.Content>

          <Tabs.Content value="trends" forceMount>
            <div className={tab !== "trends" ? "hidden" : ""}>
              {mounted.has("trends") && bookId && (
                <TrendsTab bookId={bookId} token={token!} />
              )}
            </div>
          </Tabs.Content>

          <Tabs.Content value="digest" forceMount>
            <div className={tab !== "digest" ? "hidden" : ""}>
              {mounted.has("digest") && bookId && (
                <DigestTab bookId={bookId} token={token!} />
              )}
            </div>
          </Tabs.Content>
        </div>
      </Tabs.Root>
    </div>
  );
}
