import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

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

  return (
    <div className="space-y-4">
      <Button onClick={trigger} disabled={triggering}>{triggering ? "Running..." : "Trigger Backup Now"}</Button>
      <div className="space-y-2">
        {backups.map((b) => (
          <div key={b.id} className="flex items-center gap-2 p-2 border rounded text-sm">
            <span>{b.started_at}</span>
            <Badge variant={b.status === "completed" ? "default" : b.status === "failed" ? "destructive" : "secondary"}>{b.status}</Badge>
            <span>{b.backup_type}</span>
            {b.size_bytes && <span className="text-muted-foreground">{(b.size_bytes / 1024 / 1024).toFixed(1)} MB</span>}
            {b.error && <span className="text-destructive text-xs">{b.error}</span>}
          </div>
        ))}
        {backups.length === 0 && <p className="text-muted-foreground">No backups yet</p>}
      </div>
    </div>
  );
}
