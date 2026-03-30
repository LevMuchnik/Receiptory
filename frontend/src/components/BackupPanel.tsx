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

export default function BackupPanel() {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [triggering, setTriggering] = useState(false);

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

      <div className="space-y-2 border-t border-[rgba(116,119,125,0.1)] pt-4">
        {backups.length === 0 ? (
          <p className="text-sm text-[#43474c]">No backups yet</p>
        ) : (
          backups.map((b) => (
            <div key={b.id} className="flex items-center justify-between text-xs py-2">
              <div className="flex items-center gap-3">
                <span className="material-symbols-outlined text-sm text-[#43474c]">
                  {b.status === "completed" ? "check_circle" : b.status === "failed" ? "error" : "pending"}
                </span>
                <div>
                  <p className="font-mono text-[#191c1e]">{b.started_at}</p>
                  <p className="text-[#74777d]">{b.backup_type}</p>
                </div>
              </div>
              <div className="text-right">
                <span className={statusColor(b.status)}>{b.status.toUpperCase()}</span>
                {b.size_bytes && (
                  <p className="text-[#74777d]">{(b.size_bytes / 1024 / 1024).toFixed(1)} MB</p>
                )}
                {b.error && (
                  <p className="text-[#93000a] text-[10px] max-w-[200px] truncate">{b.error}</p>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
