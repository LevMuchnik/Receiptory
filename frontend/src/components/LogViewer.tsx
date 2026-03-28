import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Button } from "@/components/ui/button";

export default function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [level, setLevel] = useState("all");

  const load = () => {
    const params = level !== "all" ? `?level=${level}` : "";
    api.get<{ lines: string[] }>(`/logs${params}`).then((d) => setLines(d.lines));
  };

  useEffect(() => { load(); }, [level]);

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <Select value={level} onValueChange={(v) => setLevel(v ?? "all")}>
          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All</SelectItem>
            <SelectItem value="ERROR">Error</SelectItem>
            <SelectItem value="WARNING">Warning</SelectItem>
            <SelectItem value="INFO">Info</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="outline" onClick={load}>Refresh</Button>
      </div>
      <pre className="bg-muted p-4 rounded text-xs overflow-auto max-h-96 font-mono">
        {lines.length > 0 ? lines.join("") : "No logs available"}
      </pre>
    </div>
  );
}
