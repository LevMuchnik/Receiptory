import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function LogViewer() {
  const [lines, setLines] = useState<string[]>([]);
  const [level, setLevel] = useState("all");

  const load = () => {
    const params = level !== "all" ? `?level=${level}` : "";
    api.get<{ lines: string[] }>(`/logs${params}`).then((d) => setLines(d.lines));
  };

  useEffect(() => { load(); }, [level]);

  const parseLevel = (line: string) => {
    if (line.includes(" ERROR ") || line.includes("[ERROR]")) return "error";
    if (line.includes(" WARNING ") || line.includes(" WARN ") || line.includes("[WARN]")) return "warn";
    if (line.includes(" INFO ") || line.includes("[INFO]")) return "info";
    if (line.includes(" DEBUG ") || line.includes("[DEBUG]")) return "debug";
    return "trace";
  };

  const levelClass = (lvl: string) => {
    switch (lvl) {
      case "error": return "text-red-400 bg-red-500/10";
      case "warn":  return "text-amber-400 bg-amber-500/10";
      case "info":  return "text-green-400";
      case "debug": return "text-blue-400";
      default:      return "text-slate-500 italic";
    }
  };

  return (
    <div className="space-y-3">
      {/* Controls */}
      <div className="flex items-center gap-3">
        <div className="relative">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="bg-slate-900 border-none rounded-lg py-1.5 pl-3 pr-8 text-xs text-slate-200 focus:ring-1 focus:ring-primary appearance-none cursor-pointer"
          >
            <option value="all">All Levels</option>
            <option value="ERROR">Error</option>
            <option value="WARNING">Warning</option>
            <option value="INFO">Info</option>
          </select>
        </div>
        <button
          onClick={load}
          className="flex items-center gap-1.5 bg-primary text-white px-3 py-1.5 rounded-lg text-xs font-bold hover:opacity-90 transition-opacity"
        >
          <span className="material-symbols-outlined text-sm">refresh</span>
          Refresh
        </button>
      </div>

      {/* Log lines */}
      <div className="h-[400px] overflow-y-auto font-mono text-xs p-4 space-y-0.5 terminal-scroll bg-[#0c0e0f] rounded-lg">
        {lines.length > 0 ? (
          lines.map((line, i) => {
            const lvl = parseLevel(line);
            return (
              <div
                key={i}
                className={`flex gap-3 hover:bg-white/5 p-1 rounded transition-colors ${lvl === "error" ? "bg-red-500/10" : lvl === "warn" ? "bg-amber-500/10" : ""}`}
              >
                <span className={`flex-1 ${levelClass(lvl)} break-all`}>{line}</span>
              </div>
            );
          })
        ) : (
          <p className="text-slate-500 italic p-2">No logs available</p>
        )}
      </div>
    </div>
  );
}
