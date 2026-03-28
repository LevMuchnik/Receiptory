import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

export default function ExportPage() {
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [month, setMonth] = useState("");
  const [year, setYear] = useState(new Date().getFullYear().toString());
  const [exporting, setExporting] = useState(false);

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
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Export</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>Quick Presets</CardTitle></CardHeader>
          <CardContent className="space-y-2">
            <Button className="w-full" variant="outline" disabled={exporting} onClick={() => doExport({ preset: "since_last_export" })}>
              All Since Last Export
            </Button>
            <div className="flex gap-2">
              <Input type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
              <Button variant="outline" disabled={exporting || !month} onClick={() => doExport({ preset: "month", month })}>
                Export Month
              </Button>
            </div>
            <div className="flex gap-2">
              <Input type="number" value={year} onChange={(e) => setYear(e.target.value)} min="2020" max="2030" />
              <Button variant="outline" disabled={exporting} onClick={() => doExport({ preset: "full_year", year: parseInt(year) })}>
                Export Year
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Custom Date Range</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <div><Label>From</Label><Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></div>
            <div><Label>To</Label><Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></div>
            <Button disabled={exporting || (!dateFrom && !dateTo)} onClick={() => doExport({ date_from: dateFrom, date_to: dateTo })}>
              Export Range
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
