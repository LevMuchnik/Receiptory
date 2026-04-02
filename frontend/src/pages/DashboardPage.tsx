import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useFileDrop } from "@/lib/useFileDrop";

interface DashboardData {
  processed_this_month: number;
  pending_review_count: number;
  total_expenses_by_category: { category: string; total: number }[];
  recent_activity: { id: number; original_filename: string; status: string; submission_date: string }[];
}

interface QueueData {
  pending: number;
  processing: number;
}

const statusChipClass = (status: string) => {
  switch (status) {
    case "processed":    return "chip-processed";
    case "pending":
    case "processing":   return "chip-pending";
    case "failed":       return "chip-failed";
    case "needs_review": return "chip-review";
    default:             return "chip-pending";
  }
};

const statusIcon = (status: string) => {
  switch (status) {
    case "processed":    return "check_circle";
    case "failed":       return "error";
    case "needs_review": return "warning";
    default:             return "receipt";
  }
};

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardData | null>(null);
  const [queue, setQueue] = useState<QueueData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [backingUp, setBackingUp] = useState(false);
  const [uploadCount, setUploadCount] = useState<number | null>(null);

  const handleDrop = useCallback(async (files: File[]) => {
    await api.upload(files);
    setUploadCount(files.length);
    setTimeout(() => setUploadCount(null), 3000);
  }, []);
  const { dragging, ...dropHandlers } = useFileDrop(handleDrop);

  useEffect(() => {
    api.get<DashboardData>("/stats/dashboard")
      .then(setStats)
      .catch((e) => setError(e.message || "Failed to load dashboard"));
    api.get<QueueData>("/queue/status")
      .then(setQueue)
      .catch(() => {});
  }, []);

  const triggerBackup = async () => {
    setBackingUp(true);
    try {
      await api.post("/backup/trigger");
    } finally {
      setBackingUp(false);
    }
  };

  if (error) return (
    <div className="flex items-center gap-3 bg-[#ffdad6] text-[#93000a] rounded-xl px-5 py-4 font-medium">
      <span className="material-symbols-outlined">error</span>
      Failed to load dashboard: {error}
    </div>
  );

  if (!stats) return (
    <div className="flex items-center gap-3 text-muted-foreground">
      <span className="material-symbols-outlined animate-spin">progress_activity</span>
      Loading dashboard...
    </div>
  );

  const totalProcessed = stats.processed_this_month;
  const pending = stats.pending_review_count;
  const queuePending = queue?.pending ?? 0;
  const queueProcessing = queue?.processing ?? 0;

  // For the bar chart, use category data
  const categoryData = stats.total_expenses_by_category.slice(0, 6);
  const maxCategoryTotal = Math.max(...categoryData.map((c) => c.total), 1);

  return (
    <div className="space-y-8">
      {/* ── Page header ─────────────────────────────────────────────────── */}
      <section className="flex flex-col md:flex-row md:items-end justify-between gap-6">
        <div>
          <h2 className="text-3xl font-headline font-extrabold text-primary tracking-tight mb-1">Dashboard</h2>
          <p className="text-muted-foreground font-medium">
            Financial performance and ingestion health.
          </p>
        </div>
        {/* Summary totals — currency breakdown if available */}
        <div className="flex gap-3 flex-wrap">
          <div className="bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-4 min-w-[140px]">
            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-1">Queue</p>
            <p className="text-2xl font-headline font-extrabold text-primary">{queuePending}</p>
            <p className="text-xs text-muted-foreground">pending docs</p>
          </div>
          <div className="bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-4 min-w-[140px]">
            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-1">Processing</p>
            <p className="text-2xl font-headline font-extrabold text-primary">{queueProcessing}</p>
            <p className="text-xs text-muted-foreground">active jobs</p>
          </div>
        </div>
      </section>

      {/* ── Bento grid ──────────────────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">

        {/* Monthly Processing */}
        <div className="lg:col-span-4 bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6 flex flex-col justify-between">
          <div>
            <div className="flex justify-between items-start mb-4">
              <h3 className="font-bold font-headline text-primary">Monthly Processing</h3>
              {pending > 0 && (
                <span className="chip-review">{pending} to review</span>
              )}
            </div>
            <div className="flex items-baseline gap-2">
              <span className="text-5xl font-headline font-extrabold text-primary">{totalProcessed.toLocaleString()}</span>
              <span className="text-muted-foreground font-medium">Documents</span>
            </div>
          </div>
          <div className="mt-6 space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Processed Successfully</span>
              <span className="font-bold">{totalProcessed}</span>
            </div>
            <div className="w-full bg-muted rounded-full h-1.5 overflow-hidden">
              <div className="bg-[#006d37] h-full rounded-full" style={{ width: pending > 0 ? `${Math.round(totalProcessed / (totalProcessed + pending) * 100)}%` : "100%" }} />
            </div>
            <div className="flex items-center justify-between text-sm">
              <span className="text-muted-foreground">Pending Review</span>
              <span className="font-bold text-[#16008a]">{pending}</span>
            </div>
          </div>
        </div>

        {/* Expense Distribution chart */}
        <div className="lg:col-span-8 bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6 relative overflow-hidden">
          <div className="flex justify-between items-center mb-6">
            <h3 className="font-bold font-headline text-primary">Expense Distribution</h3>
          </div>
          {categoryData.length === 0 ? (
            <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
              No expense data yet
            </div>
          ) : (
            <div className="flex items-end justify-between h-48 gap-3 px-2">
              {categoryData.map((cat) => {
                const heightPct = Math.round((cat.total / maxCategoryTotal) * 100);
                return (
                  <div key={cat.category} className="flex-1 flex flex-col items-center gap-2 group">
                    <span className="text-[10px] font-bold font-headline text-primary hidden group-hover:block">
                      {cat.total.toFixed(0)}
                    </span>
                    <div className="w-full bg-muted rounded-t-lg relative flex items-end h-32">
                      <div
                        className="w-full bg-primary rounded-t-lg transition-all group-hover:bg-[#2c3e50]"
                        style={{ height: `${heightPct}%`, minHeight: "4px" }}
                      />
                    </div>
                    <span className="text-[9px] font-bold text-muted-foreground uppercase tracking-wide text-center leading-tight">
                      {(cat.category || "Other").substring(0, 10)}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Ingestion Channels */}
        <div className="lg:col-span-5 bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6">
          <h3 className="font-bold font-headline text-primary mb-5">Ingestion Channels</h3>
          <div className="space-y-3">
            {[
              { icon: "mail",          label: "Gmail Auto-Sync",   sub: "IMAP polling active",     color: "bg-red-50 text-red-600",    active: true  },
              { icon: "send",          label: "Telegram Bot",       sub: "Webhook listener",        color: "bg-sky-50 text-sky-600",    active: true  },
              { icon: "folder_shared", label: "Watched Folder",     sub: "Filesystem monitor",      color: "bg-orange-50 text-orange-600", active: false },
            ].map((ch) => (
              <div key={ch.label} className="flex items-center justify-between p-3 bg-card rounded-lg">
                <div className="flex items-center gap-3">
                  <div className={`p-2 rounded-lg ${ch.color}`}>
                    <span className="material-symbols-outlined text-lg">{ch.icon}</span>
                  </div>
                  <div>
                    <p className="text-sm font-bold text-primary">{ch.label}</p>
                    <p className="text-xs text-muted-foreground">{ch.sub}</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <div className={`h-2 w-2 rounded-full ${ch.active ? "bg-[#006d37]" : "bg-[#16008a]"}`} />
                  <span className={`text-[10px] font-extrabold uppercase ${ch.active ? "text-[#007239]" : "text-[#3323cc]"}`}>
                    {ch.active ? "Active" : "Idle"}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Recent Activity */}
        <div className="lg:col-span-7 bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-6 overflow-hidden flex flex-col">
          <h3 className="font-bold font-headline text-primary mb-5">Recent Activity</h3>
          <div className="flex-1 space-y-4">
            {stats.recent_activity.length === 0 ? (
              <p className="text-sm text-muted-foreground">No recent activity</p>
            ) : (
              stats.recent_activity.slice(0, 5).map((a, idx) => (
                <div key={a.id} className="flex gap-4">
                  <div className="relative flex-shrink-0">
                    <div className="w-8 h-8 bg-accent rounded-full flex items-center justify-center text-primary">
                      <span className="material-symbols-outlined text-sm">{statusIcon(a.status)}</span>
                    </div>
                    {idx < stats.recent_activity.length - 1 && idx < 4 && (
                      <div className="absolute top-8 left-1/2 -translate-x-1/2 w-px h-full bg-accent" />
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <a
                      href={`/documents/${a.id}`}
                      className="text-sm font-bold text-primary hover:underline truncate block"
                    >
                      {a.original_filename}
                    </a>
                    <p className="text-xs text-muted-foreground mb-1">{a.submission_date}</p>
                    <span className={statusChipClass(a.status)}>{a.status}</span>
                  </div>
                  <span className="text-[10px] font-medium text-slate-400 ml-auto flex-shrink-0">
                    {a.submission_date ? new Date(a.submission_date).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : ""}
                  </span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {/* ── Quick Actions ────────────────────────────────────────────────── */}
      <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <label
          {...dropHandlers}
          className={`flex items-center justify-between p-6 rounded-xl transition-all group cursor-pointer ${
            dragging
              ? "bg-primary/80 text-white ring-2 ring-white ring-offset-2 ring-offset-primary scale-[1.02]"
              : "bg-primary text-white hover:opacity-95 active:scale-[0.98]"
          }`}
        >
          <div className="flex items-center gap-4">
            <div className="p-3 bg-card/10 rounded-lg group-hover:scale-110 transition-transform">
              <span className="material-symbols-outlined">{dragging ? "file_download" : "upload_file"}</span>
            </div>
            <div className="text-left">
              <h4 className="font-bold font-headline">
                {uploadCount ? `${uploadCount} file${uploadCount > 1 ? "s" : ""} uploaded!` : dragging ? "Drop files here" : "Quick Upload"}
              </h4>
              <p className="text-sm text-white/70">
                {dragging ? "Release to upload" : "Drag and drop or click to browse"}
              </p>
            </div>
          </div>
          <span className="material-symbols-outlined text-white/50">{dragging ? "file_download" : "chevron_right"}</span>
          <input type="file" multiple className="hidden" accept=".pdf,.jpg,.jpeg,.png,.html,.htm" onChange={async (e) => {
            if (!e.target.files) return;
            await handleDrop(Array.from(e.target.files));
            e.target.value = "";
          }} />
        </label>

        <button
          onClick={triggerBackup}
          disabled={backingUp}
          className="flex items-center justify-between p-6 bg-card text-primary rounded-xl border border-primary/5 hover:bg-muted active:scale-[0.98] transition-all group disabled:opacity-60"
        >
          <div className="flex items-center gap-4">
            <div className="p-3 bg-primary/5 rounded-lg group-hover:scale-110 transition-transform">
              <span className="material-symbols-outlined">{backingUp ? "sync" : "backup"}</span>
            </div>
            <div className="text-left">
              <h4 className="font-bold font-headline">{backingUp ? "Backup Running..." : "Trigger Backup"}</h4>
              <p className="text-sm text-muted-foreground">Sync current state to secure vault</p>
            </div>
          </div>
          <span className="material-symbols-outlined text-muted-foreground/50">chevron_right</span>
        </button>
      </section>
    </div>
  );
}
