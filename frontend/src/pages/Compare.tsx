import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { api, ApiError, type Book, type BookComparison } from "@/lib/api";

// ── Sentiment bar (inline, no import needed) ──────────────────────────────────

function MiniSentimentBar({
  dist,
}: {
  dist: { positive: number; mixed: number; negative: number };
}) {
  const total = dist.positive + dist.mixed + dist.negative;
  if (total === 0) return <span className="text-xs text-muted-foreground">—</span>;
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`;
  return (
    <div className="w-full space-y-1">
      <div className="flex h-2 overflow-hidden rounded-full">
        <div className="bg-green-500" style={{ width: pct(dist.positive) }} />
        <div className="bg-yellow-400" style={{ width: pct(dist.mixed) }} />
        <div className="bg-red-500" style={{ width: pct(dist.negative) }} />
      </div>
      <div className="flex justify-between text-xs text-muted-foreground">
        <span className="text-green-600">{Math.round((dist.positive / total) * 100)}%+</span>
        <span className="text-red-600">{Math.round((dist.negative / total) * 100)}%-</span>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Compare() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [books, setBooks] = useState<Book[]>([]);
  const [booksLoading, setBooksLoading] = useState(true);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const [results, setResults] = useState<BookComparison[] | null>(null);
  const [comparing, setComparing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAuthError = useCallback(() => {
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  useEffect(() => {
    api.books
      .list(token!)
      .then(setBooks)
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) handleAuthError();
      })
      .finally(() => setBooksLoading(false));
  }, [token, handleAuthError]);

  function toggleBook(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    setResults(null); // clear stale results on selection change
    setError(null);
  }

  async function handleCompare(e: FormEvent) {
    e.preventDefault();
    if (selected.size < 2) {
      setError("Select at least 2 books to compare.");
      return;
    }
    setError(null);
    setComparing(true);
    try {
      const res = await api.compare(token!, [...selected]);
      setResults(res.books);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) handleAuthError();
      else setError(err instanceof ApiError ? err.message : "Comparison failed.");
    } finally {
      setComparing(false);
    }
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Compare books</h1>

      {/* Book selector */}
      <form onSubmit={handleCompare} className="space-y-4">
        <p className="text-sm text-muted-foreground">
          Select 2 or more books to compare side by side.
        </p>

        {booksLoading ? (
          <div className="flex h-24 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : books.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No books yet.{" "}
            <Link to="/" className="text-primary hover:underline">
              Add one from the catalog.
            </Link>
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {books.map((book) => {
              const isSelected = selected.has(book.id);
              return (
                <label
                  key={book.id}
                  className={[
                    "flex cursor-pointer items-center gap-3 rounded-lg border p-4 transition-colors",
                    isSelected
                      ? "border-primary bg-primary/5"
                      : "border-border bg-card hover:bg-muted/50",
                  ].join(" ")}
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggleBook(book.id)}
                    className="shrink-0 rounded"
                  />
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{book.title}</p>
                    <p className="text-xs text-muted-foreground">
                      {book.metrics.review_count} reviews
                    </p>
                  </div>
                </label>
              );
            })}
          </div>
        )}

        {error && (
          <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={selected.size < 2 || comparing}
          className="inline-flex items-center gap-2 rounded-md bg-primary px-5 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
        >
          {comparing && <Loader2 className="h-4 w-4 animate-spin" />}
          {comparing ? "Comparing…" : `Compare ${selected.size > 0 ? selected.size : ""} books`}
        </button>
      </form>

      {/* Results */}
      {results && results.length > 0 && (
        <div className="space-y-6">
          <h2 className="text-lg font-semibold">Results</h2>

          {/* Stats table */}
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                    Book
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-muted-foreground">
                    Reviews
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-muted-foreground">
                    Avg rating
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-muted-foreground">
                    /week
                  </th>
                  <th className="px-4 py-3 text-right font-medium text-muted-foreground">
                    AI-flagged
                  </th>
                  <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                    Sentiment
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {results.map((b) => (
                  <tr key={b.book_id} className="bg-card">
                    <td className="px-4 py-3 font-medium">
                      <Link
                        to={`/books/${b.book_id}`}
                        className="hover:text-primary hover:underline"
                      >
                        {b.title}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-right">{b.review_count}</td>
                    <td className="px-4 py-3 text-right">
                      {b.avg_rating !== null ? `★ ${b.avg_rating.toFixed(1)}` : "—"}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {b.reviews_per_week.toFixed(1)}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {(b.ai_flagged_rate * 100).toFixed(0)}%
                    </td>
                    <td className="px-4 py-3 min-w-36">
                      <MiniSentimentBar dist={b.sentiment_distribution} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Top themes per book */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {results.map((b) => (
              <div
                key={b.book_id}
                className="rounded-lg border border-border bg-card p-4"
              >
                <p className="mb-2 truncate font-medium text-sm">{b.title}</p>
                <p className="mb-2 text-xs text-muted-foreground">Top themes</p>
                {b.top_themes.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {b.top_themes.map((t) => (
                      <span
                        key={t}
                        className="rounded-full bg-muted px-2 py-0.5 text-xs text-muted-foreground"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">None yet</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
