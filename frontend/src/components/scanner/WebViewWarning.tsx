import { useNavigate } from "react-router-dom";

interface WebViewWarningProps {
  reason: "webview" | "insecure";
}

export default function WebViewWarning({ reason }: WebViewWarningProps) {
  const navigate = useNavigate();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#f2f4f6] p-6">
      <div className="bg-white rounded-2xl shadow-xl p-8 max-w-sm text-center space-y-4">
        <span className="material-symbols-outlined text-5xl text-[#93000a]">
          {reason === "insecure" ? "lock_open" : "error"}
        </span>
        <h2 className="text-xl font-headline font-bold text-primary">
          {reason === "insecure" ? "HTTPS Required" : "Camera Not Available"}
        </h2>
        {reason === "insecure" ? (
          <div className="text-sm text-[#43474c] leading-relaxed space-y-2">
            <p>
              The camera requires a <strong>secure connection (HTTPS)</strong>.
              You are currently accessing the app over plain HTTP.
            </p>
            <p>To fix this, either:</p>
            <ul className="text-left list-disc list-inside space-y-1 text-xs">
              <li>Set up a reverse proxy with HTTPS (e.g. Nginx Proxy Manager)</li>
              <li>
                In Chrome, go to <code className="bg-[#eceef0] px-1 rounded">chrome://flags/#unsafely-treat-insecure-origin-as-secure</code>, add your NAS URL, and restart Chrome
              </li>
            </ul>
          </div>
        ) : (
          <p className="text-sm text-[#43474c] leading-relaxed">
            This in-app browser doesn't support camera access.
            Please open this page in <strong>Chrome</strong> or <strong>Edge</strong>.
          </p>
        )}
        <button
          onClick={() => navigate("/")}
          className="w-full py-3 bg-primary text-primary-foreground rounded-xl font-bold font-headline active:scale-95 transition-transform"
        >
          Go to Dashboard
        </button>
      </div>
    </div>
  );
}
