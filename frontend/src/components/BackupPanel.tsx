import { useEffect, useState } from "react";
import { api } from "@/lib/api";

interface Backup {
  id: number;
  started_at: string;
  completed_at: string | null;
  status: string;
  size_bytes: number | null;
  backup_type: string;
  error: string | null;
}

const INITIAL_SHOW = 10;

export default function BackupPanel() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [triggering, setTriggering] = useState(false);
  const [showAll, setShowAll] = useState(false);

  const load = () => api.get<Backup[]>("/backup/history").then(setBackups);
  useEffect(() => { load(); }, []);

  const trigger = async () => {
    setTriggering(true);
    try {
      await api.post("/backup/trigger");
      load();
    } finally {
      setTriggering(false);
    }
  };

  const statusColor = (status: string) => {
    if (status === "completed") return "text-[#007239] font-bold";
    if (status === "failed")    return "text-[#93000a] font-bold";
    return "text-[#3323cc] font-bold";
  };

  const statusIcon = (status: string) => {
    if (status === "completed") return "check_circle";
    if (status === "failed") return "error";
    return "pending";
  };

  const visible = showAll ? backups : backups.slice(0, INITIAL_SHOW);
  const hasMore = backups.length > INITIAL_SHOW;

  return (
    <div className="space-y-4">
      <button
        onClick={trigger}
        disabled={triggering}
        className="px-5 py-2 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-60 transition-opacity flex items-center gap-2"
      >
        <span className="material-symbols-outlined text-sm">{triggering ? "sync" : "backup"}</span>
        {triggering ? "Running..." : "Trigger Backup Now"}
      </button>

      <div className="space-y-2 border-t border-border pt-4">
        {backups.length === 0 ? (
          <p className="text-sm text-muted-foreground">No backups yet</p>
        ) : (
          <>
            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">
              {backups.length} backup{backups.length !== 1 ? "s" : ""} total
            </p>
            {visible.map((b) => (
              <div key={b.id} className="flex items-center justify-between text-xs py-2">
                <div className="flex items-center gap-3">
                  <span className="material-symbols-outlined text-sm text-muted-foreground">
                    {statusIcon(b.status)}
                  </span>
                  <div>
                    <p className="font-mono text-foreground">{b.started_at}</p>
                    <p className="text-muted-foreground">{b.backup_type}</p>
                  </div>
                </div>
                <div className="text-right">
                  <span className={statusColor(b.status)}>{b.status.toUpperCase()}</span>
                  {b.size_bytes != null && b.size_bytes > 0 && (
                    <p className="text-muted-foreground">{(b.size_bytes / 1024 / 1024).toFixed(1)} MB</p>
                  )}
                  {b.error && (
                    <p className="text-[#93000a] text-[10px] max-w-[200px] truncate" title={b.error}>{b.error}</p>
                  )}
                </div>
              </div>
            ))}
            {hasMore && !showAll && (
              <button
                onClick={() => setShowAll(true)}
                className="text-xs text-primary font-medium hover:underline mt-2"
              >
                Show all {backups.length} backups
              </button>
            )}
            {showAll && hasMore && (
              <button
                onClick={() => setShowAll(false)}
                className="text-xs text-muted-foreground font-medium hover:underline mt-2"
              >
                Show less
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
