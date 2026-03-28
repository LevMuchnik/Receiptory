import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import CategoryManager from "@/components/CategoryManager";
import BackupPanel from "@/components/BackupPanel";
import LogViewer from "@/components/LogViewer";

export default function SettingsPage() {
  const [settings, setSettings] = useState<any>({});
  const [costs, setCosts] = useState<any>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [telegramStatus, setTelegramStatus] = useState<any>(null);

  useEffect(() => {
    api.get("/settings").then(setSettings);
    api.get("/stats/processing-costs").then(setCosts);
    api.get("/settings/telegram-status").then(setTelegramStatus).catch(() => {});
  }, []);

  const save = async (updates: Record<string, any>) => {
    await api.patch("/settings", { settings: updates });
    const fresh = await api.get("/settings");
    setSettings(fresh);
  };

  const testLlm = async () => {
    setTestResult("Testing...");
    try {
      const res: any = await api.post("/settings/test-llm");
      const reply = res.response || "(no content)";
      setTestResult(`Connected to ${res.model}. Response: ${reply}`);
    } catch (e: any) {
      setTestResult(`Failed: ${e.message}`);
    }
  };

  const checkTelegram = async () => {
    setTelegramStatus({ status: "checking" });
    try {
      const res = await api.get("/settings/telegram-status");
      setTelegramStatus(res);
    } catch (e: any) {
      setTelegramStatus({ status: "error", message: e.message });
    }
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Settings</h1>
      <Tabs defaultValue="general">
        <TabsList>
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="llm">LLM</TabsTrigger>
          <TabsTrigger value="telegram">Telegram</TabsTrigger>
          <TabsTrigger value="categories">Categories</TabsTrigger>
          <TabsTrigger value="backup">Backup</TabsTrigger>
          <TabsTrigger value="logs">Logs</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Business Information</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label>Business Names (semicolon-separated, multi-language)</Label>
                <Input
                  value={Array.isArray(settings.business_names) ? settings.business_names.join("; ") : (settings.business_names ?? "")}
                  onBlur={(e) => save({ business_names: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_names: e.target.value })}
                />
              </div>
              <div>
                <Label>Business Addresses (semicolon-separated)</Label>
                <Input
                  value={Array.isArray(settings.business_addresses) ? settings.business_addresses.join("; ") : (settings.business_addresses ?? "")}
                  onBlur={(e) => save({ business_addresses: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_addresses: e.target.value })}
                />
              </div>
              <div>
                <Label>Business Tax IDs (semicolon-separated)</Label>
                <Input
                  value={Array.isArray(settings.business_tax_ids) ? settings.business_tax_ids.join("; ") : (settings.business_tax_ids ?? "")}
                  onBlur={(e) => save({ business_tax_ids: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, business_tax_ids: e.target.value })}
                />
              </div>
              <div>
                <Label>Reference Currency</Label>
                <Input value={settings.reference_currency || ""} onBlur={(e) => save({ reference_currency: e.target.value })} onChange={(e) => setSettings({ ...settings, reference_currency: e.target.value })} />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle>Authentication</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Username</Label><Input value={settings.auth_username || ""} onBlur={(e) => save({ auth_username: e.target.value })} onChange={(e) => setSettings({ ...settings, auth_username: e.target.value })} /></div>
              <div><Label>New Password</Label><Input type="password" placeholder="Enter new password" onBlur={(e) => { if (e.target.value) save({ auth_password_hash: e.target.value }); }} /></div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="llm" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>LLM Configuration</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Model</Label><Input value={settings.llm_model || ""} onBlur={(e) => save({ llm_model: e.target.value })} onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })} /></div>
              <div><Label>API Key</Label><Input type="password" value={settings.llm_api_key || ""} onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ llm_api_key: e.target.value }); }} onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })} /></div>
              <div><Label>Sleep Interval (seconds)</Label><Input type="number" step="0.1" value={settings.llm_sleep_interval ?? ""} onBlur={(e) => save({ llm_sleep_interval: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_sleep_interval: e.target.value })} /></div>
              <div><Label>Confidence Threshold</Label><Input type="number" step="0.05" min="0" max="1" value={settings.confidence_threshold ?? ""} onBlur={(e) => save({ confidence_threshold: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, confidence_threshold: e.target.value })} /></div>
              <Button onClick={testLlm}>Test LLM Connection</Button>
              {testResult && <p className="text-sm">{testResult}</p>}
            </CardContent>
          </Card>
          {costs && (
            <Card>
              <CardHeader><CardTitle>Processing Costs</CardTitle></CardHeader>
              <CardContent>
                <p>Total cost: ${costs.total_cost_usd?.toFixed(4)}</p>
                <p>Total tokens: {costs.total_tokens_in?.toLocaleString()} in / {costs.total_tokens_out?.toLocaleString()} out</p>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="telegram" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Telegram Bot</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div>
                <Label>Bot Token</Label>
                <Input
                  type="password"
                  value={settings.telegram_bot_token || ""}
                  onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ telegram_bot_token: e.target.value }); }}
                  onChange={(e) => setSettings({ ...settings, telegram_bot_token: e.target.value })}
                  placeholder="Get from @BotFather on Telegram"
                />
              </div>
              <div>
                <Label>Authorized User IDs (semicolon-separated, leave empty to allow all)</Label>
                <Input
                  value={Array.isArray(settings.telegram_authorized_users) ? settings.telegram_authorized_users.join("; ") : (settings.telegram_authorized_users ?? "")}
                  onBlur={(e) => save({ telegram_authorized_users: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                  onChange={(e) => setSettings({ ...settings, telegram_authorized_users: e.target.value })}
                />
                <p className="text-xs text-muted-foreground mt-1">
                  To find your user ID, message @userinfobot on Telegram. Restart the server after changing the token.
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="outline" onClick={checkTelegram}>Check Status</Button>
                {telegramStatus && (
                  <span className="flex items-center gap-2 text-sm">
                    <Badge variant={
                      telegramStatus.status === "running" ? "default" :
                      telegramStatus.status === "checking" ? "secondary" :
                      "destructive"
                    }>
                      {telegramStatus.status}
                    </Badge>
                    {telegramStatus.bot_username && <span>{telegramStatus.bot_username} ({telegramStatus.bot_name})</span>}
                    {telegramStatus.message && <span className="text-muted-foreground">{telegramStatus.message}</span>}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="categories"><CategoryManager /></TabsContent>
        <TabsContent value="backup">
          <Card>
            <CardHeader><CardTitle>Backup Configuration</CardTitle></CardHeader>
            <CardContent className="space-y-3">
              <div><Label>Rclone Destination</Label><Input value={settings.backup_destination || ""} onBlur={(e) => save({ backup_destination: e.target.value })} onChange={(e) => setSettings({ ...settings, backup_destination: e.target.value })} /></div>
              <div><Label>Schedule (cron)</Label><Input value={settings.backup_schedule || ""} onBlur={(e) => save({ backup_schedule: e.target.value })} onChange={(e) => setSettings({ ...settings, backup_schedule: e.target.value })} /></div>
            </CardContent>
          </Card>
          <BackupPanel />
        </TabsContent>

        <TabsContent value="logs"><LogViewer /></TabsContent>
      </Tabs>
    </div>
  );
}
