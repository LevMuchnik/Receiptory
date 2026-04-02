import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

export default function ExportPage() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [month, setMonth] = useState("");
  const [year, setYear] = useState(new Date().getFullYear().toString());
  const [exporting, setExporting] = useState(false);
  const [lastExport, setLastExport] = useState<string | null>(null);

  const doExport = async (body: any) => {
    setExporting(true);
    try {
      const blob = await api.exportDocs(body);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "receiptory_export.zip";
      a.click();
      URL.revokeObjectURL(url);
      setLastExport(new Date().toLocaleString());
    } finally {
      setExporting(false);
    }
  };

  const PRESETS = [
    {
      icon: "history",
      label: "All since last export",
      sub: "All pending documents",
      action: () => doExport({ preset: "since_last_export" }),
    },
    {
      icon: "calendar_today",
      label: "This Month",
      sub: month || new Date().toLocaleString("default", { month: "long", year: "numeric" }),
      action: () => doExport({ preset: "month", month: month || new Date().toISOString().slice(0, 7) }),
    },
    {
      icon: "calendar_month",
      label: "Full Year",
      sub: `FY ${year}`,
      action: () => doExport({ preset: "full_year", year: parseInt(year) }),
    },
  ];

  return (
    <div className="space-y-8">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
        <div>
          <h2 className="text-3xl font-headline font-extrabold text-primary tracking-tight">Export Repository</h2>
          <p className="text-muted-foreground font-medium mt-1">Configure and generate high-fidelity document packages.</p>
        </div>
        {lastExport && (
          <div className="text-right hidden sm:block">
            <p className="text-[10px] uppercase font-bold text-muted-foreground tracking-wider mb-1">Last Export</p>
            <p className="text-sm font-bold text-primary">{lastExport}</p>
          </div>
        )}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* ── Left: configuration ──────────────────────────────────── */}
        <div className="lg:col-span-8 space-y-8">

          {/* Quick Presets */}
          <section className="bg-card rounded-xl shadow-[0_8px_32px_rgba(25,28,30,0.04)] p-6">
            <h3 className="text-xs font-black uppercase text-muted-foreground tracking-[0.2em] mb-6">Quick Export Presets</h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  disabled={exporting}
                  onClick={preset.action}
                  className="group flex flex-col items-start p-5 bg-muted rounded-lg hover:bg-primary hover:text-white transition-all duration-300 text-left disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <span className="material-symbols-outlined mb-3 text-primary group-hover:text-white">{preset.icon}</span>
                  <span className="text-sm font-bold block text-foreground group-hover:text-white">{preset.label}</span>
                  <span className="text-[11px] text-muted-foreground group-hover:text-white/70 mt-1">{preset.sub}</span>
                </button>
              ))}
            </div>
          </section>

          {/* Custom Configuration */}
          <section className="bg-card rounded-xl shadow-[0_8px_32px_rgba(25,28,30,0.04)] p-6">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-xs font-black uppercase text-muted-foreground tracking-[0.2em]">Custom Configuration</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-12 gap-y-6">
              {/* Month picker */}
              <div>
                <Label className="block text-[11px] font-bold text-muted-foreground uppercase mb-2">Export by Month</Label>
                <div className="flex gap-3">
                  <Input
                    type="month"
                    value={month}
                    onChange={(e) => setMonth(e.target.value)}
                    className="bg-muted border-none rounded-lg text-sm focus-visible:ring-primary/20 flex-1"
                  />
                  <button
                    disabled={exporting || !month}
                    onClick={() => doExport({ preset: "month", month })}
                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
                  >
                    Export
                  </button>
                </div>
              </div>

              {/* Year picker */}
              <div>
                <Label className="block text-[11px] font-bold text-muted-foreground uppercase mb-2">Export by Year</Label>
                <div className="flex gap-3">
                  <Input
                    type="number"
                    value={year}
                    onChange={(e) => setYear(e.target.value)}
                    min="2020"
                    max="2030"
                    className="bg-muted border-none rounded-lg text-sm focus-visible:ring-primary/20 flex-1"
                  />
                  <button
                    disabled={exporting}
                    onClick={() => doExport({ preset: "full_year", year: parseInt(year) })}
                    className="px-4 py-2 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity"
                  >
                    Export
                  </button>
                </div>
              </div>

              {/* Date range */}
              <div>
                <Label className="block text-[11px] font-bold text-muted-foreground uppercase mb-2">Date Range — From</Label>
                <div className="bg-muted px-3 py-2 rounded-lg flex items-center justify-between">
                  <Input
                    type="date"
                    value={dateFrom}
                    onChange={(e) => setDateFrom(e.target.value)}
                    className="bg-transparent border-none p-0 text-sm focus-visible:ring-0 text-foreground font-bold"
                  />
                </div>
              </div>

              <div>
                <Label className="block text-[11px] font-bold text-muted-foreground uppercase mb-2">Date Range — To</Label>
                <div className="bg-muted px-3 py-2 rounded-lg flex items-center justify-between">
                  <Input
                    type="date"
                    value={dateTo}
                    onChange={(e) => setDateTo(e.target.value)}
                    className="bg-transparent border-none p-0 text-sm focus-visible:ring-0 text-foreground font-bold"
                  />
                </div>
              </div>
            </div>

            {/* Custom range export button */}
            {(dateFrom || dateTo) && (
              <div className="mt-6">
                <button
                  disabled={exporting || (!dateFrom && !dateTo)}
                  onClick={() => doExport({ date_from: dateFrom, date_to: dateTo })}
                  className="px-6 py-2.5 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity flex items-center gap-2"
                >
                  <span className="material-symbols-outlined text-sm">date_range</span>
                  Export Date Range
                </button>
              </div>
            )}
          </section>

          {/* Generate Zip Banner */}
          <section className="p-6 bg-primary text-white rounded-xl flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-5">
              <div className="w-14 h-14 bg-card/10 rounded-lg flex items-center justify-center">
                <span className="material-symbols-outlined text-3xl">folder_zip</span>
              </div>
              <div>
                <p className="text-sm font-bold">Ready for bundling</p>
                <p className="text-xs text-[#96a9be]">Generates PDF + CSV package of selected documents</p>
              </div>
            </div>
            <button
              disabled={exporting}
              onClick={() => doExport({ preset: "since_last_export" })}
              className="w-full md:w-auto px-8 py-3 bg-card text-primary rounded-lg font-black text-sm hover:bg-[#d1e4fb] disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex items-center gap-2 justify-center"
            >
              {exporting ? (
                <>
                  <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
                  Generating...
                </>
              ) : (
                <>
                  <span className="material-symbols-outlined text-sm">download</span>
                  Generate Zip Bundle
                </>
              )}
            </button>
          </section>
        </div>

        {/* ── Right: format info ───────────────────────────────────── */}
        <div className="lg:col-span-4 space-y-6">
          {/* Output Standard */}
          <section className="bg-muted rounded-xl p-6">
            <h3 className="text-xs font-black uppercase text-muted-foreground tracking-[0.2em] mb-4">Output Standard</h3>
            <div className="space-y-4">
              {[
                { label: "High-Res PDFs",  sub: "Category-based directory structure" },
                { label: "CSV Metadata",   sub: "Universal mapping for accounting software" },
              ].map((f) => (
                <div key={f.label} className="flex items-start gap-3">
                  <span className="material-symbols-outlined text-[#007239] bg-[#7bf8a1]/30 p-1 rounded text-base">check_circle</span>
                  <div>
                    <p className="text-xs font-bold text-primary">{f.label}</p>
                    <p className="text-[10px] text-muted-foreground">{f.sub}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* Help card */}
          <div className="relative overflow-hidden bg-[#2c3e50] rounded-xl p-6 text-white">
            <div className="relative z-10">
              <h4 className="text-sm font-bold mb-2">Need a different format?</h4>
              <p className="text-xs opacity-70 mb-4">Export direct to accounting software via custom integrations.</p>
              <a href="/settings" className="text-xs font-bold underline underline-offset-4 decoration-[#7bf8a1]">
                Configure in Settings
              </a>
            </div>
            <div className="absolute -right-4 -bottom-4 opacity-10">
              <span className="material-symbols-outlined text-8xl">account_tree</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
