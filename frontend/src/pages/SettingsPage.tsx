import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { useTheme } from "@/contexts/ThemeContext";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import CategoryManager from "@/components/CategoryManager";
import BackupPanel from "@/components/BackupPanel";
import CloudBackupPanel from "@/components/CloudBackupPanel";
import LogViewer from "@/components/LogViewer";

type SettingsTab = "general" | "llm" | "telegram" | "email" | "categories" | "backup" | "notifications" | "logs";

const TABS: { key: SettingsTab; label: string; icon: string }[] = [
  { key: "general",    label: "General",     icon: "tune" },
  { key: "llm",        label: "LLM Engine",  icon: "psychology" },
  { key: "telegram",   label: "Telegram",    icon: "send" },
  { key: "email",      label: "Email",       icon: "mail" },
  { key: "categories", label: "Taxonomies",  icon: "label" },
  { key: "backup",        label: "Resilience",     icon: "backup" },
  { key: "notifications", label: "Notifications",  icon: "notifications" },
  { key: "logs",          label: "Logs",           icon: "terminal" },
];

function SectionCard({ title, icon, children, badge }: { title: string; icon: string; children: React.ReactNode; badge?: string }) {
  return (
    <div className="bg-card rounded-xl shadow-[0_8px_32px_rgba(25,28,30,0.06)] p-6">
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-muted flex items-center justify-center">
            <span className="material-symbols-outlined text-primary">{icon}</span>
          </div>
          <h2 className="text-lg font-headline font-bold text-primary">{title}</h2>
        </div>
        {badge && <span className="chip-processed">{badge}</span>}
      </div>
      {children}
    </div>
  );
}

function FieldGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">{label}</Label>
      {children}
    </div>
  );
}

const inputCls = "bg-muted dark:bg-[#272a2d] border-none rounded-lg text-sm focus-visible:ring-primary/20 h-10";

const THEME_OPTIONS: { value: "light" | "dark" | "system"; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "light_mode" },
  { value: "dark", label: "Dark", icon: "dark_mode" },
  { value: "system", label: "System", icon: "desktop_windows" },
];

function ThemeSelector() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="flex gap-2">
      {THEME_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          onClick={() => setTheme(opt.value)}
          className={`flex-1 flex flex-col items-center gap-1.5 py-3 rounded-lg text-xs font-semibold transition-colors ${
            theme === opt.value
              ? "bg-primary text-primary-foreground"
              : "bg-muted text-muted-foreground hover:text-foreground"
          }`}
        >
          <span className="material-symbols-outlined text-lg">{opt.icon}</span>
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const [searchParams] = useSearchParams();
  const tabParam = searchParams.get("tab");
  const [activeTab, setActiveTab] = useState<SettingsTab>(
    tabParam && TABS.some((t) => t.key === tabParam) ? (tabParam as SettingsTab) : "general"
  );
  const [settings, setSettings] = useState<any>({});
  const [costs, setCosts] = useState<any>(null);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [telegramStatus, setTelegramStatus] = useState<any>(null);
  const [gmailStatus, setGmailStatus] = useState<any>(null);
  const [gmailPollResult, setGmailPollResult] = useState<string | null>(null);
  const [notifyTestResult, setNotifyTestResult] = useState<string | null>(null);

  useEffect(() => {
    api.get("/settings").then(setSettings);
    api.get("/stats/processing-costs").then(setCosts);
    api.get("/settings/telegram-status").then(setTelegramStatus).catch(() => {});
    api.get("/settings/gmail-status").then(setGmailStatus).catch(() => {});
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

  const checkGmail = async () => {
    setGmailStatus({ status: "checking" });
    try {
      const res = await api.get("/settings/gmail-status");
      setGmailStatus(res);
    } catch (e: any) {
      setGmailStatus({ status: "error", message: e.message });
    }
  };

  const pollGmailNow = async () => {
    setGmailPollResult("Polling...");
    try {
      const res: any = await api.post("/settings/gmail-poll-now");
      setGmailPollResult(`Polled: ${res.polled} message(s) processed`);
    } catch (e: any) {
      setGmailPollResult(`Failed: ${e.message}`);
    }
  };

  const sendTestNotification = async () => {
    setNotifyTestResult("Sending...");
    try {
      await api.post("/settings/test-notification");
      setNotifyTestResult("Test notification sent via enabled channels.");
    } catch (e: any) {
      setNotifyTestResult(`Failed: ${e.message}`);
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
    <div className="space-y-6">
      {/* ── Header ─────────────────────────────────────────────────── */}
      <div>
        <h1 className="text-3xl font-headline font-extrabold text-primary tracking-tight">System Administration</h1>
        <p className="text-muted-foreground font-medium mt-1">Configure your precision extraction engine and global connectivity.</p>
      </div>

      {/* ── Tab nav ─────────────────────────────────────────────────── */}
      <div className="flex gap-1 flex-wrap bg-card rounded-xl shadow-[0_2px_8px_rgba(25,28,30,0.04)] p-1.5">
        {TABS.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-semibold transition-colors ${
              activeTab === tab.key
                ? "bg-primary text-white"
                : "text-muted-foreground hover:bg-muted hover:text-primary"
            }`}
          >
            <span className="material-symbols-outlined text-base">{tab.icon}</span>
            <span className="hidden sm:inline">{tab.label}</span>
          </button>
        ))}
      </div>

      {/* ── Content ─────────────────────────────────────────────────── */}

      {/* General */}
      {activeTab === "general" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8 space-y-6">
            <SectionCard title="Business Information" icon="business">
              <div className="space-y-4">
                <FieldGroup label="Business Names (semicolon-separated, multi-language)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.business_names) ? settings.business_names.join("; ") : (settings.business_names ?? "")}
                    onBlur={(e) => save({ business_names: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_names: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Business Addresses (semicolon-separated)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.business_addresses) ? settings.business_addresses.join("; ") : (settings.business_addresses ?? "")}
                    onBlur={(e) => save({ business_addresses: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_addresses: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Business Tax IDs (semicolon-separated)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.business_tax_ids) ? settings.business_tax_ids.join("; ") : (settings.business_tax_ids ?? "")}
                    onBlur={(e) => save({ business_tax_ids: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_tax_ids: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Reference Currency">
                  <Input className={inputCls} value={settings.reference_currency || ""} onBlur={(e) => save({ reference_currency: e.target.value })} onChange={(e) => setSettings({ ...settings, reference_currency: e.target.value })} />
                </FieldGroup>
              </div>
            </SectionCard>

            <SectionCard title="Watched Folder" icon="folder_shared">
              <div className="space-y-4">
                <FieldGroup label="Folder Path (leave empty to disable)">
                  <Input
                    className={inputCls}
                    value={settings.watched_folder_path || ""}
                    onBlur={(e) => save({ watched_folder_path: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, watched_folder_path: e.target.value })}
                    placeholder="/path/to/watched/folder"
                  />
                  <p className="text-xs text-muted-foreground">Files dropped here are auto-ingested and moved to a "processed" subfolder.</p>
                </FieldGroup>
                <FieldGroup label="Poll Interval (seconds)">
                  <Input
                    className={inputCls}
                    type="number"
                    value={settings.watched_folder_poll_interval ?? 10}
                    onBlur={(e) => save({ watched_folder_poll_interval: parseInt(e.target.value) || 10 })}
                    onChange={(e) => setSettings({ ...settings, watched_folder_poll_interval: e.target.value })}
                  />
                </FieldGroup>
              </div>
            </SectionCard>
          </div>

          <div className="lg:col-span-4 space-y-6">
            <SectionCard title="Appearance" icon="palette">
              <ThemeSelector />
            </SectionCard>

            <SectionCard title="Master Authentication" icon="lock">
              <div className="space-y-4">
                <FieldGroup label="Admin Username">
                  <Input className={inputCls} value={settings.auth_username || ""} onBlur={(e) => save({ auth_username: e.target.value })} onChange={(e) => setSettings({ ...settings, auth_username: e.target.value })} />
                </FieldGroup>
                <FieldGroup label="New Password">
                  <Input className={inputCls} type="password" placeholder="Enter new password" onBlur={(e) => { if (e.target.value) save({ auth_password_hash: e.target.value }); }} />
                </FieldGroup>
                <button className="w-full py-2 bg-primary text-white text-sm font-bold rounded-lg shadow-md hover:opacity-90 transition-opacity">Update Credentials</button>
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {/* LLM */}
      {activeTab === "llm" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8 space-y-6">
            <SectionCard title="LLM Intelligence Engine" icon="psychology" badge="Active">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                <div className="space-y-4">
                  <FieldGroup label="Extraction Model">
                    <Input className={inputCls} value={settings.llm_model || ""} onBlur={(e) => save({ llm_model: e.target.value })} onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })} placeholder="gpt-4o" />
                  </FieldGroup>
                  <FieldGroup label="API Key">
                    <Input className={inputCls} type="password" value={settings.llm_api_key || ""} onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ llm_api_key: e.target.value }); }} onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="Temperature (0 = deterministic)">
                    <Input className={inputCls} type="number" step="0.1" min="0" max="2" value={settings.llm_temperature ?? 0} onBlur={(e) => save({ llm_temperature: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_temperature: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="Max Output Tokens">
                    <Input className={inputCls} type="number" step="1024" min="1024" max="32768" value={settings.llm_max_tokens ?? 8192} onBlur={(e) => save({ llm_max_tokens: parseInt(e.target.value) || 8192 })} onChange={(e) => setSettings({ ...settings, llm_max_tokens: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="Sleep Between Calls (seconds)">
                    <Input className={inputCls} type="number" step="0.1" value={settings.llm_sleep_interval ?? ""} onBlur={(e) => save({ llm_sleep_interval: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_sleep_interval: e.target.value })} />
                  </FieldGroup>
                </div>
                <div className="space-y-4">
                  <FieldGroup label="Confidence Threshold">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-muted-foreground">Documents below this are flagged for review</span>
                      <span className="text-xs font-mono bg-muted px-2 py-0.5 rounded">{Math.round((settings.confidence_threshold ?? 0.8) * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0" max="1" step="0.05"
                      value={settings.confidence_threshold ?? 0.8}
                      className="w-full h-1.5 bg-accent rounded-lg appearance-none cursor-pointer accent-primary"
                      onChange={(e) => setSettings({ ...settings, confidence_threshold: parseFloat(e.target.value) })}
                      onMouseUp={(e) => save({ confidence_threshold: parseFloat((e.target as HTMLInputElement).value) })}
                    />
                  </FieldGroup>
                </div>
              </div>
              <div className="mt-5 flex items-center gap-3">
                <button
                  onClick={testLlm}
                  className="px-5 py-2 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 transition-opacity flex items-center gap-2"
                >
                  <span className="material-symbols-outlined text-sm">cable</span>
                  Test LLM Connection
                </button>
                {testResult && (
                  <p className={`text-sm ${testResult.startsWith("Failed") ? "text-[#ba1a1a]" : "text-[#007239]"}`}>{testResult}</p>
                )}
              </div>
            </SectionCard>
          </div>

          {costs && (
            <div className="lg:col-span-4">
              <SectionCard title="Processing Costs" icon="payments">
                <div className="space-y-3">
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Total cost</span>
                    <span className="font-bold font-headline text-primary">${costs.total_cost_usd?.toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Tokens in</span>
                    <span className="font-medium">{costs.total_tokens_in?.toLocaleString()}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Tokens out</span>
                    <span className="font-medium">{costs.total_tokens_out?.toLocaleString()}</span>
                  </div>
                </div>
              </SectionCard>
            </div>
          )}
        </div>
      )}

      {/* Telegram */}
      {activeTab === "telegram" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-6">
            <SectionCard title="Telegram Bot" icon="send">
              <div className="space-y-4">
                <FieldGroup label="Bot Token">
                  <Input
                    className={inputCls}
                    type="password"
                    value={settings.telegram_bot_token || ""}
                    onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ telegram_bot_token: e.target.value }); }}
                    onChange={(e) => setSettings({ ...settings, telegram_bot_token: e.target.value })}
                    placeholder="Get from @BotFather on Telegram"
                  />
                </FieldGroup>
                <FieldGroup label="Authorized User IDs (semicolon-separated, empty = all)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.telegram_authorized_users) ? settings.telegram_authorized_users.join("; ") : (settings.telegram_authorized_users ?? "")}
                    onBlur={(e) => save({ telegram_authorized_users: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, telegram_authorized_users: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">Message @userinfobot on Telegram to find your user ID.</p>
                </FieldGroup>

                {telegramStatus?.bot_username && (
                  <div className="bg-[#7bf8a1]/20 border border-[#7bf8a1]/40 rounded-lg p-3">
                    <p className="text-sm font-medium text-primary">
                      Send documents to:{" "}
                      <a href={`https://t.me/${telegramStatus.bot_username.replace("@", "")}`} target="_blank" rel="noopener noreferrer" className="font-bold underline">
                        {telegramStatus.bot_username}
                      </a>
                    </p>
                    {telegramStatus.bot_name && <p className="text-xs text-muted-foreground">{telegramStatus.bot_name}</p>}
                  </div>
                )}

                <div className="flex items-center gap-3 flex-wrap">
                  <button
                    onClick={checkTelegram}
                    className="px-4 py-2 border border-border text-primary text-sm font-semibold rounded-lg hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <span className="material-symbols-outlined text-sm">refresh</span>
                    Check Status
                  </button>
                  {telegramStatus && (
                    <span className="flex items-center gap-2 text-sm">
                      <span className={
                        telegramStatus.status === "running" ? "chip-processed" :
                        telegramStatus.status === "checking" ? "chip-pending" :
                        "chip-failed"
                      }>
                        {telegramStatus.status}
                      </span>
                      {!telegramStatus.bot_username && telegramStatus.message && (
                        <span className="text-muted-foreground text-xs">{telegramStatus.message}</span>
                      )}
                    </span>
                  )}
                </div>
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {/* Email */}
      {activeTab === "email" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8">
            <SectionCard title="Email Ingestion (IMAP)" icon="mail">
              <div className="space-y-4">
                <FieldGroup label="Email Address">
                  <Input
                    className={inputCls}
                    value={settings.gmail_address || ""}
                    onBlur={(e) => save({ gmail_address: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, gmail_address: e.target.value })}
                    placeholder="you@gmail.com"
                  />
                </FieldGroup>
                <FieldGroup label="App Password">
                  <Input
                    className={inputCls}
                    type="password"
                    value={settings.gmail_app_password || ""}
                    onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ gmail_app_password: e.target.value }); }}
                    onChange={(e) => setSettings({ ...settings, gmail_app_password: e.target.value })}
                    placeholder="16-character App Password"
                  />
                  <p className="text-xs text-muted-foreground">
                    Gmail:{" "}
                    <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noopener noreferrer" className="underline text-primary">
                      myaccount.google.com/apppasswords
                    </a>{" "}
                    (requires 2FA)
                  </p>
                </FieldGroup>

                <div className="grid grid-cols-2 gap-4">
                  <FieldGroup label="IMAP Host">
                    <Input className={inputCls} value={settings.gmail_imap_host || "imap.gmail.com"} onBlur={(e) => save({ gmail_imap_host: e.target.value })} onChange={(e) => setSettings({ ...settings, gmail_imap_host: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="IMAP Port">
                    <Input className={inputCls} type="number" value={settings.gmail_imap_port ?? 993} onBlur={(e) => save({ gmail_imap_port: parseInt(e.target.value) || 993 })} onChange={(e) => setSettings({ ...settings, gmail_imap_port: e.target.value })} />
                  </FieldGroup>
                </div>

                <FieldGroup label="Labels to Monitor (semicolon-separated)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.gmail_labels) ? settings.gmail_labels.join("; ") : (settings.gmail_labels ?? "")}
                    onBlur={(e) => save({ gmail_labels: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, gmail_labels: e.target.value })}
                    placeholder="Receipts; Invoices"
                  />
                  <p className="text-xs text-muted-foreground">Only emails in these labels are ingested. No labels = email ingestion disabled.</p>
                </FieldGroup>

                <div className="flex items-center gap-3">
                  <Checkbox
                    checked={settings.gmail_unread_only !== false}
                    onCheckedChange={(checked) => save({ gmail_unread_only: !!checked })}
                  />
                  <Label className="text-sm font-medium">Unread only</Label>
                  <span className="text-xs text-muted-foreground">
                    {settings.gmail_unread_only !== false
                      ? "Only unread emails are processed"
                      : "All emails checked — duplicates skipped by hash"}
                  </span>
                </div>

                <FieldGroup label="Poll Interval (seconds)">
                  <Input
                    className={inputCls}
                    type="number"
                    value={settings.gmail_poll_interval ?? 300}
                    onBlur={(e) => save({ gmail_poll_interval: parseInt(e.target.value) || 300 })}
                    onChange={(e) => setSettings({ ...settings, gmail_poll_interval: e.target.value })}
                  />
                </FieldGroup>

                <FieldGroup label="Authorized Senders (semicolon-separated, @domain.com for domain rules)">
                  <Input
                    className={inputCls}
                    value={Array.isArray(settings.gmail_authorized_senders) ? settings.gmail_authorized_senders.join("; ") : (settings.gmail_authorized_senders ?? "")}
                    onBlur={(e) => save({ gmail_authorized_senders: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, gmail_authorized_senders: e.target.value })}
                    placeholder="user@example.com; @company.com"
                  />
                </FieldGroup>

                <div className="flex items-center gap-3 flex-wrap">
                  <button
                    onClick={checkGmail}
                    className="px-4 py-2 border border-border text-primary text-sm font-semibold rounded-lg hover:bg-muted transition-colors flex items-center gap-2"
                  >
                    <span className="material-symbols-outlined text-sm">cable</span>
                    Test Connection
                  </button>
                  {gmailStatus && (
                    <span className="flex items-center gap-2 text-sm">
                      <span className={
                        gmailStatus.status === "connected" ? "chip-processed" :
                        gmailStatus.status === "checking" ? "chip-pending" :
                        "chip-failed"
                      }>
                        {gmailStatus.status}
                      </span>
                      {gmailStatus.email && (
                        <span className="text-muted-foreground text-xs">
                          {gmailStatus.email} ({gmailStatus.matching} {gmailStatus.unread_only ? "unread" : "total"} in {(gmailStatus.labels || []).join(", ")})
                        </span>
                      )}
                      {gmailStatus.message && <span className="text-muted-foreground text-xs">{gmailStatus.message}</span>}
                    </span>
                  )}
                </div>

                {gmailStatus?.status === "connected" && (
                  <div className="flex items-center gap-3">
                    <button
                      onClick={pollGmailNow}
                      className="px-4 py-2 border border-border text-primary text-sm font-semibold rounded-lg hover:bg-muted transition-colors flex items-center gap-2"
                    >
                      <span className="material-symbols-outlined text-sm">sync</span>
                      Poll Now
                    </button>
                    {gmailPollResult && <span className="text-sm text-muted-foreground">{gmailPollResult}</span>}
                  </div>
                )}
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {/* Categories */}
      {activeTab === "categories" && (
        <SectionCard title="Taxonomies" icon="label">
          <CategoryManager />
        </SectionCard>
      )}

      {/* Backup */}
      {activeTab === "backup" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8 space-y-6">
            <SectionCard title="Cloud Storage" icon="cloud">
              <CloudBackupPanel settings={settings} save={save} />
            </SectionCard>

            <SectionCard title="Backup Schedule" icon="schedule">
              <div className="space-y-4">
                <FieldGroup label="Schedule (cron expression)">
                  <Input
                    className={inputCls}
                    value={settings.backup_schedule || ""}
                    onBlur={(e) => save({ backup_schedule: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, backup_schedule: e.target.value })}
                    placeholder="0 2 * * *"
                  />
                  <p className="text-xs text-muted-foreground">Daily at 02:00 AM: <code className="bg-muted px-1 rounded">0 2 * * *</code></p>
                </FieldGroup>
                <FieldGroup label="Current Destination">
                  <p className="text-sm font-mono text-muted-foreground bg-muted px-3 py-2 rounded-lg">
                    {settings.backup_destination || "(not configured)"}
                  </p>
                </FieldGroup>
                <FieldGroup label="Retention Policy">
                  <div className="grid grid-cols-3 gap-3">
                    <div className="space-y-1">
                      <Label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Daily (days)</Label>
                      <Input
                        className={inputCls}
                        type="number"
                        min={1}
                        value={settings.backup_retention_daily ?? 7}
                        onBlur={(e) => save({ backup_retention_daily: parseInt(e.target.value) || 7 })}
                        onChange={(e) => setSettings({ ...settings, backup_retention_daily: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Weekly (weeks)</Label>
                      <Input
                        className={inputCls}
                        type="number"
                        min={1}
                        value={settings.backup_retention_weekly ?? 4}
                        onBlur={(e) => save({ backup_retention_weekly: parseInt(e.target.value) || 4 })}
                        onChange={(e) => setSettings({ ...settings, backup_retention_weekly: e.target.value })}
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Monthly (months)</Label>
                      <Input
                        className={inputCls}
                        type="number"
                        min={1}
                        value={settings.backup_retention_monthly ?? 3}
                        onBlur={(e) => save({ backup_retention_monthly: parseInt(e.target.value) || 3 })}
                        onChange={(e) => setSettings({ ...settings, backup_retention_monthly: e.target.value })}
                      />
                    </div>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1">Quarterly backups (Jan/Apr/Jul/Oct 1st) are never auto-deleted.</p>
                </FieldGroup>
                <details className="text-sm">
                  <summary className="cursor-pointer text-muted-foreground font-medium hover:text-primary transition-colors">
                    Advanced: Custom rclone destination
                  </summary>
                  <div className="mt-3">
                    <FieldGroup label="Rclone Destination (overrides cloud storage)">
                      <Input
                        className={inputCls}
                        value={settings.backup_destination || ""}
                        onBlur={(e) => save({ backup_destination: e.target.value })}
                        onChange={(e) => setSettings({ ...settings, backup_destination: e.target.value })}
                        placeholder="s3:my-bucket/backups"
                      />
                    </FieldGroup>
                  </div>
                </details>
              </div>
            </SectionCard>

            <div className="bg-card rounded-xl shadow-[0_8px_32px_rgba(25,28,30,0.06)] p-6">
              <h3 className="font-headline font-bold text-primary mb-4">Backup History</h3>
              <BackupPanel />
            </div>
          </div>
        </div>
      )}

      {/* Notifications */}
      {activeTab === "notifications" && (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <div className="lg:col-span-8 space-y-6">
            <SectionCard title="Notification Settings" icon="notifications">
              <div className="space-y-4">
                <FieldGroup label="Base URL (for document links in notifications)">
                  <Input
                    className={inputCls}
                    value={settings.base_url || ""}
                    onBlur={(e) => save({ base_url: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, base_url: e.target.value })}
                    placeholder="https://receiptory.example.com"
                  />
                  <p className="text-xs text-muted-foreground">Used to generate clickable links in notifications. Leave empty to omit links.</p>
                </FieldGroup>
                <FieldGroup label="From Name (email sender display name)">
                  <Input
                    className={inputCls}
                    value={settings.notify_from_name || ""}
                    onBlur={(e) => save({ notify_from_name: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, notify_from_name: e.target.value })}
                    placeholder="Receiptory"
                  />
                </FieldGroup>
                <FieldGroup label="Email Recipient (leave empty to use Gmail address)">
                  <Input
                    className={inputCls}
                    value={settings.notify_email_to || ""}
                    onBlur={(e) => save({ notify_email_to: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, notify_email_to: e.target.value })}
                    placeholder="you@example.com"
                  />
                </FieldGroup>
              </div>
            </SectionCard>

            <SectionCard title="Notification Matrix" icon="grid_on">
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border">
                      <th className="text-left py-2 pr-4 font-bold text-muted-foreground text-[10px] uppercase tracking-wider w-full">Event</th>
                      <th className="text-center py-2 px-4 font-bold text-muted-foreground text-[10px] uppercase tracking-wider whitespace-nowrap">
                        <span className="flex items-center gap-1 justify-center">
                          <span className="material-symbols-outlined text-sm">send</span>
                          Telegram
                        </span>
                      </th>
                      <th className="text-center py-2 px-4 font-bold text-muted-foreground text-[10px] uppercase tracking-wider whitespace-nowrap">
                        <span className="flex items-center gap-1 justify-center">
                          <span className="material-symbols-outlined text-sm">mail</span>
                          Email
                        </span>
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#eceef0]">
                    {[
                      { label: "Document Ingested",  tgKey: "notify_telegram_ingested",       emailKey: "notify_email_ingested" },
                      { label: "Document Processed", tgKey: "notify_telegram_processed",      emailKey: "notify_email_processed" },
                      { label: "Processing Failed",  tgKey: "notify_telegram_failed",         emailKey: "notify_email_failed" },
                      { label: "Needs Review",       tgKey: "notify_telegram_needs_review",   emailKey: "notify_email_needs_review" },
                      { label: "Backup Completed",   tgKey: "notify_telegram_backup_ok",      emailKey: "notify_email_backup_ok" },
                      { label: "Backup Failed",      tgKey: "notify_telegram_backup_failed",  emailKey: "notify_email_backup_failed" },
                    ].map(({ label, tgKey, emailKey }) => (
                      <tr key={tgKey} className="hover:bg-[#f8f9fa]">
                        <td className="py-3 pr-4 font-medium text-primary">{label}</td>
                        <td className="py-3 px-4 text-center">
                          <Checkbox
                            checked={settings[tgKey] === true}
                            onCheckedChange={(checked) => save({ [tgKey]: !!checked })}
                          />
                        </td>
                        <td className="py-3 px-4 text-center">
                          <Checkbox
                            checked={settings[emailKey] === true}
                            onCheckedChange={(checked) => save({ [emailKey]: !!checked })}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="mt-5 flex items-center gap-3">
                <button
                  onClick={sendTestNotification}
                  className="px-5 py-2 bg-primary text-white rounded-lg text-sm font-bold hover:opacity-90 transition-opacity flex items-center gap-2"
                >
                  <span className="material-symbols-outlined text-sm">send</span>
                  Send Test Notification
                </button>
                {notifyTestResult && (
                  <p className={`text-sm ${notifyTestResult.startsWith("Failed") ? "text-[#ba1a1a]" : "text-[#007239]"}`}>
                    {notifyTestResult}
                  </p>
                )}
              </div>
            </SectionCard>
          </div>
        </div>
      )}

      {/* Logs */}
      {activeTab === "logs" && (
        <div className="bg-[#191c1e] text-white rounded-xl overflow-hidden shadow-2xl">
          <div className="p-4 bg-slate-800 border-b border-white/10 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded bg-primary flex items-center justify-center">
                <span className="material-symbols-outlined text-sm">terminal</span>
              </div>
              <h2 className="font-bold text-slate-100 font-headline">System Logs</h2>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500 animate-pulse" />
              <span className="text-[10px] text-slate-400 uppercase font-bold tracking-widest">Live</span>
            </div>
          </div>
          <div className="p-4">
            <LogViewer />
          </div>
        </div>
      )}
    </div>
  );
}
