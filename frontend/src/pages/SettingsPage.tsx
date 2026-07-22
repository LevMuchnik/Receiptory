import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { useTheme } from "@/contexts/ThemeContext";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import ModelCombobox, { type ModelOption } from "@/components/ModelCombobox";
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

function FieldGroup({ label, children, hint }: { label: string; children: React.ReactNode; hint?: string }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider block">{label}</Label>
      {children}
      {hint && <p className="text-[11px] text-muted-foreground leading-snug">{hint}</p>}
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
  const [modelInfo, setModelInfo] = useState<any>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [envKeys, setEnvKeys] = useState<string[]>([]);
  const [apiKeys, setApiKeys] = useState<{ name: string; last4: string }[]>([]);
  const [apiKeySelected, setApiKeySelected] = useState<string>("");
  const [legacyEnvKeySet, setLegacyEnvKeySet] = useState<boolean>(false);
  const [newKeyName, setNewKeyName] = useState<string>("");
  const [newKeyValue, setNewKeyValue] = useState<string>("");

  // Load the vision-capable chat model registry once for the picker (#13, PR3).
  // Cached server-side; failure just leaves the combobox as a free-text field.
  useEffect(() => {
    api.get("/settings/llm-models").then((r: any) => setModels(r.models || [])).catch(() => setModels([]));
    // Which settings are pinned by an env var (env > db). Edits to these are
    // silently discarded, so the UI shows them read-only instead (#13).
    api.get("/settings/env-overrides").then((r: any) => setEnvKeys(r.keys || [])).catch(() => setEnvKeys([]));
    loadApiKeys();
  }, []);

  // DB-managed LLM API keys (issue #25): name + last4 only, never full secrets.
  const loadApiKeys = () =>
    api.get("/settings/llm-api-keys").then((r: any) => {
      setApiKeys(r.keys || []);
      setApiKeySelected(r.selected || "");
      setLegacyEnvKeySet(!!r.legacy_env_key_set);
    }).catch(() => { setApiKeys([]); setApiKeySelected(""); });

  const addApiKey = async () => {
    if (!newKeyName.trim() || !newKeyValue.trim()) return;
    await api.post("/settings/llm-api-keys", { name: newKeyName.trim(), key: newKeyValue.trim() });
    setNewKeyName(""); setNewKeyValue("");
    await loadApiKeys();
  };
  const deleteApiKey = async (name: string) => {
    await api.delete(`/settings/llm-api-keys/${encodeURIComponent(name)}`);
    await loadApiKeys();
  };
  const selectApiKey = async (name: string) => {
    await api.put("/settings/llm-api-keys/selected", { name });
    await loadApiKeys();
  };

  const envLocked = (k: string) => envKeys.includes(k);
  const envHint = (k: string, base?: string) =>
    envLocked(k)
      ? `Managed by RECEIPTORY_${k.toUpperCase()} in .env — edit the environment to change (UI edits are ignored while set).`
      : base;

  // Look up price + reasoning support for the current model (issue #13).
  // Debounced so typing into the free-text model field doesn't fire a request
  // per keystroke against the ~3k-entry registry.
  useEffect(() => {
    const model = settings.llm_model;
    if (!model) { setModelInfo(null); return; }
    const t = setTimeout(() => {
      api.get(`/settings/model-info?model=${encodeURIComponent(model)}`).then(setModelInfo).catch(() => setModelInfo(null));
    }, 400);
    return () => clearTimeout(t);
  }, [settings.llm_model]);

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
                <FieldGroup label="Business Names (semicolon-separated, multi-language)" hint={envHint("business_names")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("business_names")}
                    value={Array.isArray(settings.business_names) ? settings.business_names.join("; ") : (settings.business_names ?? "")}
                    onBlur={(e) => save({ business_names: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_names: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Export Name (used in export file names; defaults to first business name)" hint={envHint("export_name")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("export_name")}
                    value={settings.export_name ?? ""}
                    placeholder="e.g. Lev_Muchnik"
                    onBlur={(e) => save({ export_name: e.target.value.trim() })}
                    onChange={(e) => setSettings({ ...settings, export_name: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Business Addresses (semicolon-separated)" hint={envHint("business_addresses")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("business_addresses")}
                    value={Array.isArray(settings.business_addresses) ? settings.business_addresses.join("; ") : (settings.business_addresses ?? "")}
                    onBlur={(e) => save({ business_addresses: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_addresses: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Business Tax IDs (semicolon-separated)" hint={envHint("business_tax_ids")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("business_tax_ids")}
                    value={Array.isArray(settings.business_tax_ids) ? settings.business_tax_ids.join("; ") : (settings.business_tax_ids ?? "")}
                    onBlur={(e) => save({ business_tax_ids: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, business_tax_ids: e.target.value })}
                  />
                </FieldGroup>
                <FieldGroup label="Reference Currency" hint={envHint("reference_currency")}>
                  <Input className={inputCls} disabled={envLocked("reference_currency")} value={settings.reference_currency || ""} onBlur={(e) => save({ reference_currency: e.target.value })} onChange={(e) => setSettings({ ...settings, reference_currency: e.target.value })} />
                </FieldGroup>
              </div>
            </SectionCard>

            <SectionCard title="Watched Folder" icon="folder_shared">
              <div className="space-y-4">
                <FieldGroup label="Folder Path (leave empty to disable)" hint={envHint("watched_folder_path")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("watched_folder_path")}
                    value={settings.watched_folder_path || ""}
                    onBlur={(e) => save({ watched_folder_path: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, watched_folder_path: e.target.value })}
                    placeholder="/path/to/watched/folder"
                  />
                  <p className="text-xs text-muted-foreground">Files dropped here are auto-ingested and moved to a "processed" subfolder.</p>
                </FieldGroup>
                <FieldGroup label="Poll Interval (seconds)" hint={envHint("watched_folder_poll_interval")}>
                  <Input
                    className={inputCls}
                    type="number"
                    disabled={envLocked("watched_folder_poll_interval")}
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
                <FieldGroup label="Admin Username" hint={envHint("auth_username")}>
                  <Input className={inputCls} disabled={envLocked("auth_username")} value={settings.auth_username || ""} onBlur={(e) => save({ auth_username: e.target.value })} onChange={(e) => setSettings({ ...settings, auth_username: e.target.value })} />
                </FieldGroup>
                <FieldGroup label="New Password" hint={envHint("auth_password_hash", envLocked("auth_password_hash") ? "Login uses RECEIPTORY_AUTH_PASSWORD from .env — edit the environment to change." : undefined)}>
                  <Input className={inputCls} type="password" disabled={envLocked("auth_password_hash")} placeholder="Enter new password" onBlur={(e) => { if (e.target.value) save({ auth_password_hash: e.target.value }); }} />
                </FieldGroup>
                <button disabled={envLocked("auth_username") && envLocked("auth_password_hash")} className="w-full py-2 bg-primary text-white text-sm font-bold rounded-lg shadow-md hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed">Update Credentials</button>
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
                  <FieldGroup label="Extraction Model" hint={envHint("llm_model")}>
                    <ModelCombobox
                      value={settings.llm_model || ""}
                      models={models}
                      disabled={envLocked("llm_model")}
                      className={`${inputCls} w-full px-3 disabled:opacity-50 disabled:cursor-not-allowed`}
                      onCommit={(id) => { setSettings({ ...settings, llm_model: id }); save({ llm_model: id }); }}
                    />
                    {modelInfo && modelInfo.model === settings.llm_model && (
                      <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                        {modelInfo.in_registry ? (
                          <span className="text-[11px] text-muted-foreground font-mono">
                            {modelInfo.input_price_per_1m != null && modelInfo.output_price_per_1m != null
                              ? `$${modelInfo.input_price_per_1m} / $${modelInfo.output_price_per_1m} per 1M tokens`
                              : "price unavailable"}
                          </span>
                        ) : (
                          <span className="text-[11px] text-muted-foreground">Not in litellm registry — used as-is</span>
                        )}
                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${modelInfo.supports_reasoning ? "bg-[#7bf8a1]/30 text-[#007239]" : "bg-muted text-muted-foreground"}`}>
                          {modelInfo.supports_reasoning ? "reasoning" : "no reasoning"}
                        </span>
                      </div>
                    )}
                  </FieldGroup>
                  <FieldGroup
                    label="API Keys"
                    hint={
                      `Provider keys are stored in the database and editable here. The selected key is sent to the model.` +
                      (modelInfo && modelInfo.model === settings.llm_model && modelInfo.provider
                        ? ` Selected model provider: ${modelInfo.provider} — pick the matching key.`
                        : "")
                    }
                  >
                    <div className="flex flex-col gap-2">
                      {apiKeys.length === 0 && (
                        <p className="text-[12px] text-muted-foreground">No API keys stored yet. Add one below.</p>
                      )}
                      {apiKeys.map((k) => (
                        <div key={k.name} className="flex items-center gap-2 rounded-md border px-3 py-2">
                          <input
                            type="radio"
                            name="apiKeySelected"
                            checked={apiKeySelected.toLowerCase() === k.name.toLowerCase()}
                            onChange={() => selectApiKey(k.name)}
                            title="Use this key"
                          />
                          <span className="font-medium text-[13px]">{k.name}</span>
                          <span className="font-mono text-[12px] text-muted-foreground">••••{k.last4}</span>
                          <button
                            type="button"
                            className="ml-auto text-[12px] text-red-500 hover:underline"
                            onClick={() => deleteApiKey(k.name)}
                          >
                            Delete
                          </button>
                        </div>
                      ))}
                      <div className="flex items-center gap-2 pt-1">
                        <Input
                          className={`${inputCls} w-40`}
                          placeholder="Name (e.g. Gemini)"
                          value={newKeyName}
                          onChange={(e) => setNewKeyName(e.target.value)}
                        />
                        <Input
                          className={`${inputCls} flex-1`}
                          type="password"
                          placeholder="API key"
                          value={newKeyValue}
                          onChange={(e) => setNewKeyValue(e.target.value)}
                        />
                        <button
                          type="button"
                          className="px-4 py-2 bg-primary text-white text-sm font-bold rounded-lg shadow-md hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
                          onClick={addApiKey}
                          disabled={!newKeyName.trim() || !newKeyValue.trim()}
                        >
                          Save
                        </button>
                      </div>
                      {legacyEnvKeySet && !apiKeySelected && (
                        <p className="text-[11px] text-muted-foreground">
                          No key selected — falling back to RECEIPTORY_LLM_API_KEY from .env.
                        </p>
                      )}
                    </div>
                  </FieldGroup>
                  <FieldGroup label="Temperature" hint={envHint("llm_temperature", "Gemini 3 is tuned for 1.0. Lower values can degrade extraction quality.")}>
                    <Input className={inputCls} type="number" step="0.1" min="0" max="2" disabled={envLocked("llm_temperature")} value={settings.llm_temperature ?? 1} onBlur={(e) => save({ llm_temperature: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_temperature: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="Max Output Tokens" hint={envHint("llm_max_tokens")}>
                    <Input className={inputCls} type="number" step="1024" min="1024" max="32768" disabled={envLocked("llm_max_tokens")} value={settings.llm_max_tokens ?? 8192} onBlur={(e) => save({ llm_max_tokens: parseInt(e.target.value) || 8192 })} onChange={(e) => setSettings({ ...settings, llm_max_tokens: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup
                    label="Reasoning Effort"
                    hint={
                      envHint("llm_reasoning_effort",
                        modelInfo && modelInfo.model === settings.llm_model && !modelInfo.supports_reasoning
                          ? "The selected model does not support reasoning. Effort is ignored."
                          : "Higher effort spends more reasoning (output) tokens per document, raising cost. \"None\" sends no reasoning parameter.")
                    }
                  >
                    <select
                      className={`${inputCls} w-full px-3 disabled:opacity-50 disabled:cursor-not-allowed`}
                      value={typeof settings.llm_reasoning_effort === "string" ? settings.llm_reasoning_effort : "none"}
                      disabled={envLocked("llm_reasoning_effort") || !!(modelInfo && modelInfo.model === settings.llm_model && !modelInfo.supports_reasoning)}
                      onChange={(e) => { setSettings({ ...settings, llm_reasoning_effort: e.target.value }); save({ llm_reasoning_effort: e.target.value }); }}
                    >
                      <option value="none">None (default)</option>
                      <option value="minimal">Minimal</option>
                      <option value="low">Low</option>
                      <option value="medium">Medium</option>
                      <option value="high">High</option>
                    </select>
                  </FieldGroup>
                  <FieldGroup label="Sleep Between Calls (seconds)" hint={envHint("llm_sleep_interval")}>
                    <Input className={inputCls} type="number" step="0.1" disabled={envLocked("llm_sleep_interval")} value={settings.llm_sleep_interval ?? ""} onBlur={(e) => save({ llm_sleep_interval: parseFloat(e.target.value) })} onChange={(e) => setSettings({ ...settings, llm_sleep_interval: e.target.value })} />
                  </FieldGroup>
                </div>
                <div className="space-y-4">
                  <FieldGroup label="Confidence Threshold" hint={envHint("confidence_threshold")}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-xs text-muted-foreground">Documents below this are flagged for review</span>
                      <span className="text-xs font-mono bg-muted px-2 py-0.5 rounded">{Math.round((settings.confidence_threshold ?? 0.8) * 100)}%</span>
                    </div>
                    <input
                      type="range"
                      min="0" max="1" step="0.05"
                      disabled={envLocked("confidence_threshold")}
                      value={settings.confidence_threshold ?? 0.8}
                      className="w-full h-1.5 bg-accent rounded-lg appearance-none cursor-pointer accent-primary disabled:opacity-50 disabled:cursor-not-allowed"
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
                <FieldGroup label="Bot Token" hint={envHint("telegram_bot_token")}>
                  <Input
                    className={inputCls}
                    type="password"
                    disabled={envLocked("telegram_bot_token")}
                    value={settings.telegram_bot_token || ""}
                    onBlur={(e) => { if (e.target.value && !e.target.value.includes("***")) save({ telegram_bot_token: e.target.value }); }}
                    onChange={(e) => setSettings({ ...settings, telegram_bot_token: e.target.value })}
                    placeholder="Get from @BotFather on Telegram"
                  />
                </FieldGroup>
                <FieldGroup label="Authorized User IDs (semicolon-separated, empty = all)" hint={envHint("telegram_authorized_users")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("telegram_authorized_users")}
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
                <FieldGroup label="Email Address" hint={envHint("gmail_address")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("gmail_address")}
                    value={settings.gmail_address || ""}
                    onBlur={(e) => save({ gmail_address: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, gmail_address: e.target.value })}
                    placeholder="you@gmail.com"
                  />
                </FieldGroup>
                <FieldGroup label="App Password" hint={envHint("gmail_app_password")}>
                  <Input
                    className={inputCls}
                    type="password"
                    disabled={envLocked("gmail_app_password")}
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
                  <FieldGroup label="IMAP Host" hint={envHint("gmail_imap_host")}>
                    <Input className={inputCls} disabled={envLocked("gmail_imap_host")} value={settings.gmail_imap_host || "imap.gmail.com"} onBlur={(e) => save({ gmail_imap_host: e.target.value })} onChange={(e) => setSettings({ ...settings, gmail_imap_host: e.target.value })} />
                  </FieldGroup>
                  <FieldGroup label="IMAP Port" hint={envHint("gmail_imap_port")}>
                    <Input className={inputCls} type="number" disabled={envLocked("gmail_imap_port")} value={settings.gmail_imap_port ?? 993} onBlur={(e) => save({ gmail_imap_port: parseInt(e.target.value) || 993 })} onChange={(e) => setSettings({ ...settings, gmail_imap_port: e.target.value })} />
                  </FieldGroup>
                </div>

                <FieldGroup label="Labels to Monitor (semicolon-separated)" hint={envHint("gmail_labels")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("gmail_labels")}
                    value={Array.isArray(settings.gmail_labels) ? settings.gmail_labels.join("; ") : (settings.gmail_labels ?? "")}
                    onBlur={(e) => save({ gmail_labels: e.target.value.split(";").map((s: string) => s.trim()).filter(Boolean) })}
                    onChange={(e) => setSettings({ ...settings, gmail_labels: e.target.value })}
                    placeholder="Receipts; Invoices"
                  />
                  <p className="text-xs text-muted-foreground">Only emails in these labels are ingested. No labels = email ingestion disabled.</p>
                </FieldGroup>

                <div>
                  <div className="flex items-center gap-3">
                    <Checkbox
                      disabled={envLocked("gmail_unread_only")}
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
                  {envLocked("gmail_unread_only") && <p className="text-[11px] text-muted-foreground leading-snug mt-1.5">{envHint("gmail_unread_only")}</p>}
                </div>

                <FieldGroup label="Poll Interval (seconds)" hint={envHint("gmail_poll_interval")}>
                  <Input
                    className={inputCls}
                    type="number"
                    disabled={envLocked("gmail_poll_interval")}
                    value={settings.gmail_poll_interval ?? 300}
                    onBlur={(e) => save({ gmail_poll_interval: parseInt(e.target.value) || 300 })}
                    onChange={(e) => setSettings({ ...settings, gmail_poll_interval: e.target.value })}
                  />
                </FieldGroup>

                <FieldGroup label="Authorized Senders (semicolon-separated, @domain.com for domain rules)" hint={envHint("gmail_authorized_senders")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("gmail_authorized_senders")}
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
                <FieldGroup label="Schedule (cron expression)" hint={envHint("backup_schedule")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("backup_schedule")}
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
                        disabled={envLocked("backup_retention_daily")}
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
                        disabled={envLocked("backup_retention_weekly")}
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
                        disabled={envLocked("backup_retention_monthly")}
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
                <FieldGroup label="Base URL (for document links in notifications)" hint={envHint("base_url")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("base_url")}
                    value={settings.base_url || ""}
                    onBlur={(e) => save({ base_url: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, base_url: e.target.value })}
                    placeholder="https://receiptory.example.com"
                  />
                  <p className="text-xs text-muted-foreground">Used to generate clickable links in notifications. Leave empty to omit links.</p>
                </FieldGroup>
                <FieldGroup label="From Name (email sender display name)" hint={envHint("notify_from_name")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("notify_from_name")}
                    value={settings.notify_from_name || ""}
                    onBlur={(e) => save({ notify_from_name: e.target.value })}
                    onChange={(e) => setSettings({ ...settings, notify_from_name: e.target.value })}
                    placeholder="Receiptory"
                  />
                </FieldGroup>
                <FieldGroup label="Email Recipient (leave empty to use Gmail address)" hint={envHint("notify_email_to")}>
                  <Input
                    className={inputCls}
                    disabled={envLocked("notify_email_to")}
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
