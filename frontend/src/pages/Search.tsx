import { useCallback, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Loader2, Search as SearchIcon } from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { api, ApiError, type SearchResult } from "@/lib/api";

const SENTIMENT_COLORS: Record<string, string> = {
  positive: "bg-green-100 text-green-700",
  mixed: "bg-yellow-100 text-yellow-700",
  negative: "bg-red-100 text-red-700",
};

export default function Search() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [query, setQuery] = useState("");
  const [k, setK] = useState(10);
  const [results, setResults] = useState<SearchResult[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleAuthError = useCallback(() => {
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  async function handleSearch(e: FormEvent) {
    e.preventDefault();
    const q = query.trim();
    if (!q) return;

    setError(null);
    setLoading(true);
    try {
      const data = await api.search(token!, q, k);
      setResults(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) handleAuthError();
      else setError(err instanceof ApiError ? err.message : "Search failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Semantic search</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Search across all your reviews using natural language. Results are
          ranked by embedding similarity.
        </p>
      </div>

      {/* Search form */}
      <form onSubmit={handleSearch} className="space-y-3">
        <div className="flex gap-2">
          <div className="relative flex-1">
            <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. "readers who loved the ending""
              className="w-full rounded-md border border-input bg-background py-2 pl-9 pr-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              autoFocus
            />
          </div>
          <button
            type="submit"
            disabled={loading || !query.trim()}
            className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
          >
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <SearchIcon className="h-4 w-4" />
            )}
            {loading ? "Searching…" : "Search"}
          </button>
        </div>

        {/* k selector */}
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <span>Show top</span>
          {[5, 10, 20].map((n) => (
            <button
              key={n}
              type="button"
              onClick={() => setK(n)}
              className={[
                "rounded px-2 py-0.5 text-sm font-medium transition-colors",
                k === n
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-muted",
              ].join(" ")}
            >
              {n}
            </button>
          ))}
          <span>results</span>
        </div>
      </form>

      {/* Error */}
      {error && (
        <p className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {/* Empty state after search */}
      {results !== null && results.length === 0 && (
        <p className="py-12 text-center text-sm text-muted-foreground">
          No results found. Try a different query.
        </p>
      )}

      {/* Results */}
      {results && results.length > 0 && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {results.length} result{results.length !== 1 ? "s" : ""}
          </p>

          {results.map((r, i) => (
            <div
              key={r.review_id}
              className="rounded-lg border border-border bg-card p-4"
            >
              <div className="mb-2 flex flex-wrap items-center gap-2">
                {/* Rank */}
                <span className="flex h-5 w-5 items-center justify-center rounded-full bg-muted text-xs font-medium text-muted-foreground">
                  {i + 1}
                </span>

                {/* Book link */}
                <Link
                  to={`/books/${r.book_id}`}
                  className="text-sm font-medium text-primary hover:underline"
                >
                  {r.book_title}
                </Link>

                {r.reviewer_name && (
                  <span className="text-xs text-muted-foreground">
                    by {r.reviewer_name}
                  </span>
                )}

                {/* Sentiment badge */}
                {r.sentiment && (
                  <span
                    className={`ml-auto rounded-full px-2 py-0.5 text-xs font-medium ${
                      SENTIMENT_COLORS[r.sentiment] ?? "bg-muted text-muted-foreground"
                    }`}
                  >
                    {r.sentiment}
                  </span>
                )}

                {/* Similarity score */}
                <span className="text-xs tabular-nums text-muted-foreground">
                  {(r.score * 100).toFixed(0)}% match
                </span>
              </div>

              {/* Snippet */}
              <p className="text-sm leading-relaxed text-foreground line-clamp-3">
                {r.snippet}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
