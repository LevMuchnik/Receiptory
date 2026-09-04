import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api } from "@/lib/api";

interface ScannerNavProps {
  pageCount: number;
  onClose: () => void;
}

interface NativeCameraButtonProps {
  /** Classes for the visible trigger. Callers style it to match their surface. */
  className?: string;
  /** Idle label. Swapped for a progress label while an upload is in flight. */
  label?: string;
  /** Optional Material Symbols glyph rendered before the label. */
  icon?: string;
  /** Render the failure message underneath the button as well as in a toast. */
  inlineError?: boolean;
}

/**
 * Hands the capture off to the OS camera app via
 * `<input type="file" accept="image/*" capture="environment">`.
 *
 * Why this exists:
 *  - It is the only way to scan inside a WebView (Telegram, Slack, ...), where
 *    `getUserMedia` is unavailable and the scanner refuses to start.
 *  - It needs no permissions of its own, so it also works over plain HTTP.
 *  - The stock camera app beats any getUserMedia path on autofocus, HDR and
 *    sensor resolution, so it doubles as an escape hatch when the in-app
 *    scanner misbehaves.
 *
 * The chosen file goes straight to `api.upload`, the same endpoint the scanner
 * submits its generated PDF to.
 */
export function NativeCameraButton({
  className,
  label = "Use camera app",
  icon = "photo_camera",
  inlineError = false,
}: NativeCameraButtonProps) {
  const navigate = useNavigate();
  const inputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const input = e.currentTarget;
    const file = input.files?.[0];
    // Clear synchronously so picking the same file again still fires onChange.
    input.value = "";
    if (!file) return;

    setError(null);
    setUploading(true);
    const toastId = toast.loading("Uploading photo...");
    try {
      await api.upload([file]);
      toast.success("Photo uploaded", { id: toastId });
      navigate("/documents");
    } catch (err) {
      const message = err instanceof Error ? err.message : "Upload failed";
      setError(message);
      toast.error(message, { id: toastId });
    } finally {
      setUploading(false);
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        onChange={handleChange}
      />
      <button
        type="button"
        disabled={uploading}
        onClick={() => inputRef.current?.click()}
        className={className}
      >
        {icon && <span className="material-symbols-outlined text-base">{icon}</span>}
        <span>{uploading ? "Uploading..." : label}</span>
      </button>
      {inlineError && error && <p className="text-xs text-[#93000a]">{error}</p>}
    </>
  );
}

export default function ScannerNav({ pageCount, onClose }: ScannerNavProps) {
  return (
    <div className="shrink-0 flex items-center justify-between px-4 h-14 bg-black/60 text-white backdrop-blur-sm z-40">
      <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10 transition-colors">
        <span className="material-symbols-outlined">close</span>
      </button>
      <h1 className="font-headline font-bold text-sm">Document Scanner</h1>
      <div className="flex items-center gap-2">
        <NativeCameraButton
          label="Camera app"
          className="flex items-center gap-1.5 bg-white/10 px-3 py-1 rounded-full text-xs font-bold hover:bg-white/20 transition-colors disabled:opacity-50"
        />
        <div className="flex items-center gap-1.5 bg-white/10 px-3 py-1 rounded-full">
          <span className="material-symbols-outlined text-sm">description</span>
          <span className="text-sm font-bold">{pageCount}</span>
        </div>
      </div>
    </div>
  );
}
