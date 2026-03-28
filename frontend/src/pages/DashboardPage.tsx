import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

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

export default function DashboardPage() {
  const [stats, setStats] = useState<DashboardData | null>(null);
  const [queue, setQueue] = useState<QueueData | null>(null);

  useEffect(() => {
    api.get<DashboardData>("/stats/dashboard").then(setStats);
    api.get<QueueData>("/queue/status").then(setQueue);
  }, []);

  if (!stats) return <div>Loading...</div>;

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Processed This Month</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{stats.processed_this_month}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Needs Review</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{stats.pending_review_count}</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Queue</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{queue?.pending ?? 0} pending</div></CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2"><CardTitle className="text-sm font-medium">Processing</CardTitle></CardHeader>
          <CardContent><div className="text-2xl font-bold">{queue?.processing ?? 0}</div></CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <Card>
          <CardHeader><CardTitle>Expenses by Category</CardTitle></CardHeader>
          <CardContent>
            {stats.total_expenses_by_category.length === 0 ? (
              <p className="text-muted-foreground">No data yet</p>
            ) : (
              <div className="space-y-2">
                {stats.total_expenses_by_category.map((e) => (
                  <div key={e.category} className="flex justify-between">
                    <span>{e.category || "Uncategorized"}</span>
                    <span className="font-mono">{e.total?.toFixed(2)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Recent Activity</CardTitle></CardHeader>
          <CardContent>
            <div className="space-y-2">
              {stats.recent_activity.map((a) => (
                <div key={a.id} className="flex items-center justify-between text-sm">
                  <a href={`/documents/${a.id}`} className="hover:underline truncate max-w-[200px]">
                    {a.original_filename}
                  </a>
                  <Badge variant={a.status === "processed" ? "default" : a.status === "failed" ? "destructive" : "secondary"}>
                    {a.status}
                  </Badge>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
