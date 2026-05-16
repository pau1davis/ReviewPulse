import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  Loader2,
  Plus,
  X,
  XCircle,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import {
  api,
  ApiError,
  type Book,
  type SinceLastLoginResponse,
} from "@/lib/api";

// ── Sub-components ────────────────────────────────────────────────────────────

function JobBadge({ status }: { status: string }) {
  const map = {
    queued: { label: "Queued", cls: "bg-muted text-muted-foreground", Icon: Clock, spin: false },
    running: { label: "Processing…", cls: "bg-blue-100 text-blue-700", Icon: Loader2, spin: true },
    completed: { label: "Done", cls: "bg-green-100 text-green-700", Icon: CheckCircle2, spin: false },
    partial: { label: "Partial", cls: "bg-yellow-100 text-yellow-700", Icon: AlertTriangle, spin: false },
    failed: { label: "Failed", cls: "bg-red-100 text-red-700", Icon: XCircle, spin: false },
  } as const;
  const { label, cls, Icon, spin } =
    map[status as keyof typeof map] ?? map.queued;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      <Icon className={`h-3 w-3 ${spin ? "animate-spin" : ""}`} />
      {label}
    </span>
  );
}

function SentimentBar({
  breakdown,
}: {
  breakdown: { positive: number; mixed: number; negative: number };
}) {
  const total = breakdown.positive + breakdown.mixed + breakdown.negative;
  if (total === 0) return <p className="text-xs text-muted-foreground">No reviews yet</p>;
  const pct = (n: number) => `${Math.round((n / total) * 100)}%`;
  return (
    <div className="space-y-1">
      <div className="flex h-2 overflow-hidden rounded-full">
        <div className="bg-green-500" style={{ width: pct(breakdown.positive) }} />
        <div className="bg-yellow-400" style={{ width: pct(breakdown.mixed) }} />
        <div className="bg-red-500" style={{ width: pct(breakdown.negative) }} />
      </div>
      <div className="flex gap-3 text-xs text-muted-foreground">
        <span className="text-green-600">{breakdown.positive} positive</span>
        <span className="text-yellow-600">{breakdown.mixed} mixed</span>
        <span className="text-red-600">{breakdown.negative} negative</span>
      </div>
    </div>
  );
}

function BookCard({ book }: { book: Book }) {
  const job = book.metrics.latest_job;
  return (
    <Link
      to={`/books/${book.id}`}
      className="block rounded-lg border border-border bg-card p-5 shadow-sm transition-shadow hover:shadow-md"
    >
      <div className="mb-3 flex items-start justify-between gap-2">
        <h3 className="font-semibold leading-tight">{book.title}</h3>
        {job && <JobBadge status={job.status} />}
      </div>

      <div className="mb-3 flex items-center gap-4 text-sm text-muted-foreground">
        <span>{book.metrics.review_count} reviews</span>
        {book.metrics.avg_rating !== null && (
          <span>★ {book.metrics.avg_rating.toFixed(1)}</span>
        )}
        {book.metrics.total_cost_usd > 0 && (
          <span className="ml-auto text-xs">
            ${book.metrics.total_cost_usd.toFixed(4)} LLM cost
          </span>
        )}
      </div>

      <SentimentBar breakdown={book.metrics.sentiment_breakdown} />
    </Link>
  );
}

// ── Dashboard ─────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const { token, logout } = useAuth();
  const navigate = useNavigate();

  const [books, setBooks] = useState<Book[]>([]);
  const [sinceLogin, setSinceLogin] = useState<SinceLastLoginResponse | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);

  // Add-book form state
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState("");
  const [isbn, setIsbn] = useState("");
  const [url, setUrl] = useState("");
  const [addSubmitting, setAddSubmitting] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  // Keep a stable ref to the latest token for polling callbacks
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const handleAuthError = useCallback(() => {
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  const loadBooks = useCallback(async () => {
    try {
      const data = await api.books.list(tokenRef.current!);
      setBooks(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthError();
      } else {
        setPageError("Failed to load your catalog.");
      }
    }
  }, [handleAuthError]);

  // Initial load
  useEffect(() => {
    async function init() {
      setLoading(true);
      await Promise.all([
        loadBooks(),
        api
          .sinceLastLogin(token!)
          .then(setSinceLogin)
          .catch(() => {}), // supplementary — fail silently
      ]);
      setLoading(false);
    }
    init();
  }, [token, loadBooks]);

  // Poll active jobs until terminal, then refresh catalog
  useEffect(() => {
    const activeJobs = books
      .map((b) => b.metrics.latest_job)
      .filter((j) => j && (j.status === "queued" || j.status === "running"))
      .map((j) => j!.job_id);

    if (activeJobs.length === 0) return;

    const interval = setInterval(async () => {
      let anyFinished = false;
      for (const jobId of activeJobs) {
        try {
          const s = await api.jobs.status(tokenRef.current!, jobId);
          if (s.status !== "queued" && s.status !== "running") {
            anyFinished = true;
          }
        } catch {
          // network blip — try again next tick
        }
      }
      if (anyFinished) await loadBooks();
    }, 3000);

    return () => clearInterval(interval);
  }, [books, loadBooks]);

  async function handleAddBook(e: FormEvent) {
    e.preventDefault();
    setAddError(null);
    setAddSubmitting(true);
    try {
      const res = await api.books.add(token!, {
        title,
        isbn: isbn || undefined,
        url: url || undefined,
      });
      // Optimistically add the book, then reload to get metrics
      setBooks((prev) => [res.book, ...prev]);
      setTitle("");
      setIsbn("");
      setUrl("");
      setShowAdd(false);
      // Reload after a tick to pick up the job record
      setTimeout(loadBooks, 800);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        handleAuthError();
      } else {
        setAddError(err instanceof ApiError ? err.message : "Failed to add book.");
      }
    } finally {
      setAddSubmitting(false);
    }
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Your catalog</h1>
        <button
          onClick={() => setShowAdd((v) => !v)}
          className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90"
        >
          {showAdd ? (
            <X className="h-4 w-4" />
          ) : (
            <Plus className="h-4 w-4" />
          )}
          {showAdd ? "Cancel" : "Add book"}
        </button>
      </div>

      {/* Since-last-login banner */}
      {sinceLogin && sinceLogin.total_new_reviews > 0 && (
        <div className="rounded-lg border border-blue-200 bg-blue-50 px-5 py-4 text-sm">
          <p className="font-medium text-blue-900">
            {sinceLogin.total_new_reviews} new review
            {sinceLogin.total_new_reviews !== 1 ? "s" : ""} since your last
            visit
          </p>
          <div className="mt-1 flex gap-4 text-blue-700">
            {sinceLogin.negative_reviews.length > 0 && (
              <span>{sinceLogin.negative_reviews.length} negative</span>
            )}
            {sinceLogin.actionable_reviews.length > 0 && (
              <span>{sinceLogin.actionable_reviews.length} actionable</span>
            )}
            {sinceLogin.ai_flagged_count > 0 && (
              <span>{sinceLogin.ai_flagged_count} AI-flagged</span>
            )}
          </div>
        </div>
      )}

      {/* Add-book form */}
      {showAdd && (
        <form
          onSubmit={handleAddBook}
          className="rounded-lg border border-border bg-card p-5 shadow-sm"
        >
          <h2 className="mb-4 font-semibold">Add a new book</h2>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="sm:col-span-3">
              <label className="mb-1 block text-sm font-medium">
                Title <span className="text-destructive">*</span>
              </label>
              <input
                required
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="My Great Novel"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium">
                ISBN <span className="text-muted-foreground text-xs">(optional)</span>
              </label>
              <input
                value={isbn}
                onChange={(e) => setIsbn(e.target.value)}
                placeholder="978-3-16-148410-0"
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
            <div className="sm:col-span-2">
              <label className="mb-1 block text-sm font-medium">
                Store URL <span className="text-muted-foreground text-xs">(optional)</span>
              </label>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://amazon.com/dp/..."
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </div>

          {addError && (
            <p className="mt-3 rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {addError}
            </p>
          )}

          <div className="mt-4 flex gap-3">
            <button
              type="submit"
              disabled={addSubmitting}
              className="inline-flex items-center gap-1.5 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:opacity-90 disabled:opacity-50"
            >
              {addSubmitting && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
              {addSubmitting ? "Adding…" : "Add book"}
            </button>
          </div>
        </form>
      )}

      {/* Page error */}
      {pageError && (
        <p className="rounded-md bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {pageError}
        </p>
      )}

      {/* Book grid */}
      {books.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border py-16 text-center">
          <BookOpen className="h-10 w-10 text-muted-foreground" />
          <p className="text-muted-foreground">
            No books yet. Add your first book to get started.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {books.map((book) => (
            <BookCard key={book.id} book={book} />
          ))}
        </div>
      )}
    </div>
  );
}
