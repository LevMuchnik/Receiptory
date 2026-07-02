import { useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

function BasisToggle({
  value,
  onChange,
}: {
  value: "ingestion" | "receipt";
  onChange: (v: "ingestion" | "receipt") => void;
}) {
  return (
    <div className="flex gap-1 bg-muted rounded-lg p-0.5">
      <button
        onClick={() => onChange("ingestion")}
        className={`px-2.5 py-1 rounded text-[10px] font-bold transition-colors ${
          value === "ingestion" ? "bg-card text-primary shadow-sm" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Upload Date
      </button>
      <button
        onClick={() => onChange("receipt")}
        className={`px-2.5 py-1 rounded text-[10px] font-bold transition-colors ${
          value === "receipt" ? "bg-card text-primary shadow-sm" : "text-muted-foreground hover:text-foreground"
        }`}
      >
        Issue Date
      </button>
    </div>
  );
}

function StepBtn({ icon, onClick, label }: { icon: string; onClick: () => void; label: string }) {
  return (
    <button
      onClick={onClick}
      aria-label={label}
      className="h-10 w-10 flex items-center justify-center bg-muted rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/80 transition-colors shrink-0"
    >
      <span className="material-symbols-outlined text-base leading-none">{icon}</span>
    </button>
  );
}

function currentMonth() {
  return new Date().toISOString().slice(0, 7);
}

export default function ExportPage() {
  const [month, setMonth] = useState(currentMonth());
  const [monthBasis, setMonthBasis] = useState<"ingestion" | "receipt">("ingestion");

  const [year, setYear] = useState(new Date().getFullYear().toString());
  const [yearBasis, setYearBasis] = useState<"ingestion" | "receipt">("ingestion");

  const [rangeFrom, setRangeFrom] = useState("");
  const [rangeTo, setRangeTo] = useState("");
  const [rangeBasis, setRangeBasis] = useState<"ingestion" | "receipt">("ingestion");

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

  const stepMonth = (delta: number) => {
    const [y, m] = month.split("-").map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    setMonth(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`);
  };

  const stepYear = (delta: number) => {
    const y = parseInt(year);
    if (!isNaN(y)) setYear(String(y + delta));
  };

  const yearValid = !exporting && !!year && !isNaN(parseInt(year)) && parseInt(year) >= 1900;

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

          <section className="bg-card rounded-xl shadow-[0_8px_32px_rgba(25,28,30,0.04)] p-6 space-y-8">
            <h3 className="text-xs font-black uppercase text-muted-foreground tracking-[0.2em]">Export Configuration</h3>

            {/* Export by Month */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-[11px] font-bold text-muted-foreground uppercase">Export by Month</Label>
                <BasisToggle value={monthBasis} onChange={setMonthBasis} />
              </div>
              <div className="flex gap-2 items-center">
                <StepBtn icon="chevron_left" onClick={() => stepMonth(-1)} label="Previous month" />
                <Input
                  type="month"
                  value={month}
                  onChange={(e) => setMonth(e.target.value)}
                  className="h-10 bg-muted border-none rounded-lg text-sm focus-visible:ring-primary/20 flex-1"
                />
                <StepBtn icon="chevron_right" onClick={() => stepMonth(1)} label="Next month" />
                <button
                  disabled={exporting || !month}
                  onClick={() => doExport({ preset: "month", month, date_basis: monthBasis })}
                  className="h-10 px-4 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity shrink-0"
                >
                  Export
                </button>
              </div>
            </div>

            {/* Export by Year */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-[11px] font-bold text-muted-foreground uppercase">Export by Year</Label>
                <BasisToggle value={yearBasis} onChange={setYearBasis} />
              </div>
              <div className="flex gap-2 items-center">
                <StepBtn icon="chevron_left" onClick={() => stepYear(-1)} label="Previous year" />
                <Input
                  type="number"
                  value={year}
                  onChange={(e) => setYear(e.target.value)}
                  min="2020"
                  max="2030"
                  className="h-10 bg-muted border-none rounded-lg text-sm focus-visible:ring-primary/20 flex-1"
                />
                <StepBtn icon="chevron_right" onClick={() => stepYear(1)} label="Next year" />
                <button
                  disabled={!yearValid}
                  onClick={() => doExport({ preset: "full_year", year: parseInt(year), date_basis: yearBasis })}
                  className="h-10 px-4 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity shrink-0"
                >
                  Export
                </button>
              </div>
            </div>

            {/* Custom Date Range */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label className="text-[11px] font-bold text-muted-foreground uppercase">Custom Date Range</Label>
                <BasisToggle value={rangeBasis} onChange={setRangeBasis} />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="bg-muted px-3 py-2 rounded-lg">
                  <p className="text-[10px] text-muted-foreground mb-1">From</p>
                  <Input
                    type="date"
                    value={rangeFrom}
                    onChange={(e) => setRangeFrom(e.target.value)}
                    className="bg-transparent border-none p-0 text-sm focus-visible:ring-0 text-foreground font-bold"
                  />
                </div>
                <div className="bg-muted px-3 py-2 rounded-lg">
                  <p className="text-[10px] text-muted-foreground mb-1">To</p>
                  <Input
                    type="date"
                    value={rangeTo}
                    onChange={(e) => setRangeTo(e.target.value)}
                    className="bg-transparent border-none p-0 text-sm focus-visible:ring-0 text-foreground font-bold"
                  />
                </div>
              </div>
              <button
                disabled={exporting || (!rangeFrom && !rangeTo)}
                onClick={() => doExport({ date_basis: rangeBasis, date_from: rangeFrom || undefined, date_to: rangeTo || undefined })}
                className="h-10 px-4 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 disabled:opacity-40 disabled:cursor-not-allowed transition-opacity flex items-center gap-2"
              >
                <span className="material-symbols-outlined text-sm">date_range</span>
                Export
              </button>
            </div>
          </section>

          {/* Generate Zip Banner */}
          <section className="p-6 bg-primary text-white rounded-xl flex flex-col md:flex-row items-center justify-between gap-6">
            <div className="flex items-center gap-5">
              <div className="w-14 h-14 bg-card/10 rounded-lg flex items-center justify-center">
                <span className="material-symbols-outlined text-3xl">folder_zip</span>
              </div>
              <div>
                <p className="text-sm font-bold">Ready for bundling</p>
                <p className="text-xs text-[#96a9be]">Generates PDF + CSV package of all documents not yet exported</p>
              </div>
            </div>
            <button
              disabled={exporting}
              onClick={() => doExport({ preset: "since_last_export", date_basis: "ingestion" })}
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
