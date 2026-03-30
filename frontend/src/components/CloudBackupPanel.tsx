import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "@/lib/api";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

interface ProviderStatus {
  connected: boolean;
  email: string | null;
  folder: string | null;
  client_id_set: boolean;
}

interface ProvidersStatus {
  gdrive: ProviderStatus;
  onedrive: ProviderStatus;
}

const PROVIDER_CONFIG = {
  gdrive: {
    name: "Google Drive",
    icon: "cloud",
    color: "bg-blue-50 text-blue-600",
    helpUrl: "https://console.cloud.google.com/apis/credentials",
    callbackPath: "/api/cloud-auth/callback/gdrive",
    steps: [
      'Go to <a href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noopener noreferrer">Google Cloud Console &rarr; APIs & Services &rarr; Credentials</a>',
      'If you don\'t have a project yet, click <strong>Create Project</strong> and give it any name (e.g. "Receiptory")',
      'Click <strong>+ Create Credentials</strong> &rarr; <strong>OAuth client ID</strong>',
      'If prompted to configure the consent screen: choose <strong>External</strong>, fill in the app name (e.g. "Receiptory"), your email, and save. You can skip scopes and test users for now',
      'For Application type, select <strong>Web application</strong>',
      'Under <strong>Authorized redirect URIs</strong>, click <strong>+ Add URI</strong> and paste the <em>Callback URL</em> shown below',
      'Click <strong>Create</strong>. Copy the <strong>Client ID</strong> and <strong>Client Secret</strong> into the fields below',
      'Go to <a href="https://console.cloud.google.com/apis/library/drive.googleapis.com" target="_blank" rel="noopener noreferrer">APIs & Services &rarr; Library</a>, search for <strong>Google Drive API</strong>, and click <strong>Enable</strong>',
    ],
  },
  onedrive: {
    name: "OneDrive",
    icon: "cloud",
    color: "bg-sky-50 text-sky-600",
    helpUrl: "https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps",
    callbackPath: "/api/cloud-auth/callback/onedrive",
    steps: [
      'Go to <a href="https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps" target="_blank" rel="noopener noreferrer">Azure Portal &rarr; App registrations</a>',
      'Click <strong>+ New registration</strong>. Name it (e.g. "Receiptory"). Under Supported account types, select <strong>Accounts in any organizational directory and personal Microsoft accounts</strong>',
      'Under <strong>Redirect URI</strong>, select <strong>Web</strong> and paste the <em>Callback URL</em> shown below. Click <strong>Register</strong>',
      'On the app overview page, copy the <strong>Application (client) ID</strong> into the Client ID field below',
      'Go to <strong>Certificates & secrets</strong> &rarr; <strong>+ New client secret</strong>. Set any description and expiry. Copy the <strong>Value</strong> (not the Secret ID) into the Client Secret field below',
      'Go to <strong>API permissions</strong> &rarr; <strong>+ Add a permission</strong> &rarr; <strong>Microsoft Graph</strong> &rarr; <strong>Delegated permissions</strong>. Add: <code>Files.ReadWrite.All</code>, <code>User.Read</code>, <code>offline_access</code>',
      'Click <strong>Grant admin consent</strong> if available (optional for personal accounts)',
    ],
  },
};

const inputCls = "bg-[#eceef0] border-none rounded-lg text-sm focus-visible:ring-primary/20 h-10";

export default function CloudBackupPanel({
  settings,
  save,
}: {
  settings: any;
  save: (updates: Record<string, any>) => Promise<void>;
}) {
  const [providers, setProviders] = useState<ProvidersStatus | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<Record<string, string>>({});
  const [searchParams, setSearchParams] = useSearchParams();
  const [toast, setToast] = useState<string | null>(null);
  const [localCreds, setLocalCreds] = useState<Record<string, string>>({});

  const fetchProviders = useCallback(() => {
    api.get<ProvidersStatus>("/cloud-auth/providers").then(setProviders).catch(() => {});
  }, []);

  useEffect(() => {
    fetchProviders();
  }, [fetchProviders]);

  // Handle OAuth callback result from URL params
  useEffect(() => {
    const authResult = searchParams.get("cloud_auth");
    if (authResult === "success") {
      setToast("Cloud storage connected successfully!");
      fetchProviders();
      // Clean URL params
      searchParams.delete("cloud_auth");
      setSearchParams(searchParams, { replace: true });
      setTimeout(() => setToast(null), 5000);
    } else if (authResult === "error") {
      const msg = searchParams.get("message") || "Unknown error";
      setToast(`Connection failed: ${msg}`);
      searchParams.delete("cloud_auth");
      searchParams.delete("message");
      setSearchParams(searchParams, { replace: true });
      setTimeout(() => setToast(null), 8000);
    }
  }, [searchParams, setSearchParams, fetchProviders]);

  const startAuth = async (provider: string) => {
    setLoading(provider);
    try {
      const res: any = await api.post(`/cloud-auth/${provider}/start`);
      window.location.href = res.authorize_url;
    } catch (e: any) {
      setTestResult({ ...testResult, [provider]: `Error: ${e.message}` });
      setLoading(null);
    }
  };

  const disconnect = async (provider: string) => {
    if (!confirm(`Disconnect ${PROVIDER_CONFIG[provider as keyof typeof PROVIDER_CONFIG].name}?`)) return;
    await api.post(`/cloud-auth/${provider}/disconnect`);
    fetchProviders();
  };

  const testConnection = async (provider: string) => {
    setTestResult({ ...testResult, [provider]: "Testing..." });
    try {
      await api.post(`/cloud-auth/${provider}/test`);
      setTestResult({ ...testResult, [provider]: "Connected!" });
    } catch (e: any) {
      setTestResult({ ...testResult, [provider]: `Failed: ${e.message}` });
    }
  };

  const updateFolder = async (provider: string, folder: string) => {
    try {
      await api.post(`/cloud-auth/${provider}/set-folder`, { folder });
      fetchProviders();
      // Refresh parent settings so backup_destination updates
      const fresh = await api.get("/settings");
      Object.assign(settings, fresh);
    } catch (e: any) {
      setTestResult({ ...testResult, [provider]: `Error: ${e.message}` });
    }
  };

  if (!providers) return null;

  const baseUrl = settings.base_url || window.location.origin;

  return (
    <div className="space-y-4">
      {toast && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-lg text-sm font-medium ${
          toast.startsWith("Cloud storage connected") ? "bg-[#7bf8a1]/20 text-[#007239]" : "bg-[#ffdad6] text-[#93000a]"
        }`}>
          <span className="material-symbols-outlined text-sm">
            {toast.startsWith("Cloud storage connected") ? "check_circle" : "error"}
          </span>
          {toast}
        </div>
      )}

      {!settings.base_url && (
        <div className="flex items-center gap-2 px-4 py-3 rounded-lg bg-[#e2dfff]/30 text-[#3323cc] text-xs font-medium">
          <span className="material-symbols-outlined text-sm">info</span>
          Base URL will be auto-detected from your browser. To override, set it in the Notifications tab.
        </div>
      )}

      {(["gdrive", "onedrive"] as const).map((provider) => {
        const cfg = PROVIDER_CONFIG[provider];
        const status = providers[provider];
        const prefix = provider === "gdrive" ? "gdrive" : "onedrive";

        return (
          <div key={provider} className="border border-[rgba(116,119,125,0.15)] rounded-xl p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-lg ${cfg.color}`}>
                  <span className="material-symbols-outlined text-lg">{cfg.icon}</span>
                </div>
                <div>
                  <h4 className="font-bold font-headline text-primary">{cfg.name}</h4>
                  {status.connected && status.email && (
                    <p className="text-xs text-[#43474c]">{status.email}</p>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2">
                {status.connected ? (
                  <span className="chip-processed">Connected</span>
                ) : (
                  <span className="text-xs font-bold text-[#74777d] uppercase">Not connected</span>
                )}
              </div>
            </div>

            {!status.connected && (
              <>
                <details className="group">
                  <summary className="cursor-pointer text-sm font-semibold text-primary flex items-center gap-1.5 hover:underline">
                    <span className="material-symbols-outlined text-sm transition-transform group-open:rotate-90">chevron_right</span>
                    Setup instructions
                  </summary>
                  <div className="mt-3 bg-[#f7f9fb] rounded-lg p-4 space-y-3">
                    <ol className="list-decimal list-outside ml-4 space-y-2 text-xs text-[#43474c] leading-relaxed">
                      {cfg.steps.map((step, i) => (
                        <li key={i} dangerouslySetInnerHTML={{ __html: step }} />
                      ))}
                    </ol>
                  </div>
                </details>

                <div className="bg-[#f7f9fb] rounded-lg p-4 space-y-3">
                  <div className="space-y-1.5">
                    <Label className="text-[10px] font-bold text-[#74777d] uppercase tracking-wider">
                      Callback URL
                      <span className="normal-case font-normal text-[#43474c] ml-1">(paste this into your OAuth app's redirect URIs)</span>
                    </Label>
                    <div className="flex gap-2 items-center">
                      <code className="flex-1 bg-[#eceef0] px-3 py-2 rounded-lg text-xs break-all select-all">
                        {baseUrl}{cfg.callbackPath}
                      </code>
                      <Button
                        variant="outline"
                        size="sm"
                        className="shrink-0 h-8"
                        onClick={() => {
                          navigator.clipboard.writeText(`${baseUrl}${cfg.callbackPath}`);
                          setToast("Copied to clipboard!");
                          setTimeout(() => setToast(null), 2000);
                        }}
                      >
                        <span className="material-symbols-outlined text-sm">content_copy</span>
                      </Button>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-[10px] font-bold text-[#74777d] uppercase tracking-wider">Client ID</Label>
                    <Input
                      className={inputCls}
                      value={localCreds[`${prefix}_client_id`] ?? settings[`${prefix}_client_id`] ?? ""}
                      onChange={(e) => setLocalCreds({ ...localCreds, [`${prefix}_client_id`]: e.target.value })}
                      onBlur={(e) => { if (e.target.value) save({ [`${prefix}_client_id`]: e.target.value }).then(fetchProviders); }}
                      placeholder="Your OAuth client ID"
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-[10px] font-bold text-[#74777d] uppercase tracking-wider">Client Secret</Label>
                    <Input
                      className={inputCls}
                      value={localCreds[`${prefix}_client_secret`] ?? settings[`${prefix}_client_secret`] ?? ""}
                      onChange={(e) => setLocalCreds({ ...localCreds, [`${prefix}_client_secret`]: e.target.value })}
                      onBlur={(e) => { if (e.target.value) save({ [`${prefix}_client_secret`]: e.target.value }).then(fetchProviders); }}
                      placeholder="Your OAuth client secret"
                    />
                  </div>
                </div>
                <Button
                  onClick={() => startAuth(provider)}
                  disabled={!status.client_id_set || loading === provider}
                  className="w-full bg-primary text-white hover:bg-primary/90"
                >
                  {loading === provider ? (
                    <span className="material-symbols-outlined animate-spin text-sm mr-2">progress_activity</span>
                  ) : (
                    <span className="material-symbols-outlined text-sm mr-2">login</span>
                  )}
                  Connect {cfg.name}
                </Button>
              </>
            )}

            {status.connected && (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label className="text-[10px] font-bold text-[#74777d] uppercase tracking-wider">Backup Folder</Label>
                  <div className="flex gap-2">
                    <Input
                      className={inputCls + " flex-1"}
                      defaultValue={status.folder || "Receiptory"}
                      onBlur={(e) => updateFolder(provider, e.target.value)}
                      placeholder="Receiptory"
                    />
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => testConnection(provider)}
                  >
                    <span className="material-symbols-outlined text-sm mr-1">wifi_tethering</span>
                    Test
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="text-[#93000a] hover:bg-[#ffdad6] hover:text-[#93000a]"
                    onClick={() => disconnect(provider)}
                  >
                    <span className="material-symbols-outlined text-sm mr-1">link_off</span>
                    Disconnect
                  </Button>
                </div>
                {testResult[provider] && (
                  <p className={`text-xs font-medium ${testResult[provider].startsWith("Connected") ? "text-[#007239]" : "text-[#93000a]"}`}>
                    {testResult[provider]}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
