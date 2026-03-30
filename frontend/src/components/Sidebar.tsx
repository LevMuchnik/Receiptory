import { useCallback, useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { useFileDrop } from "@/lib/useFileDrop";

const NAV_ITEMS = [
  { to: "/", icon: "dashboard",    label: "Dashboard",       exact: true },
  { to: "/documents", icon: "folder_open", label: "Documents",  exact: false },
  { to: "/export",    icon: "ios_share",   label: "Export",     exact: false },
  { to: "/settings",  icon: "settings",    label: "Administration", exact: false },
];

interface UploadDialogProps {
  onClose: () => void;
  onUpload: (files: FileList) => void;
}

function UploadDialog({ onClose, onUpload }: UploadDialogProps) {
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      onUpload(e.target.files);
      onClose();
    }
  };
  const handleDrop = useCallback((files: File[]) => {
    const dt = new DataTransfer();
    files.forEach((f) => dt.items.add(f));
    onUpload(dt.files);
    onClose();
  }, [onUpload, onClose]);
  const { dragging, ...dropHandlers } = useFileDrop(handleDrop);

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/30 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-xl shadow-2xl p-6 w-96 max-w-full mx-4" onClick={(e) => e.stopPropagation()} {...dropHandlers}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-headline font-bold text-primary">Upload Document</h2>
          <button onClick={onClose} className="p-1 hover:bg-[#f2f4f6] rounded-lg transition-colors">
            <span className="material-symbols-outlined text-[#43474c]">close</span>
          </button>
        </div>
        <label className={`flex flex-col items-center justify-center border-2 border-dashed rounded-xl p-8 cursor-pointer transition-all group ${
          dragging ? "border-primary bg-primary/5 scale-[1.02]" : "border-[#c4c6cd] hover:border-primary hover:bg-[#f7f9fb]"
        }`}>
          <span className={`material-symbols-outlined text-4xl mb-2 ${dragging ? "text-primary" : "text-[#43474c] group-hover:text-primary"}`}>
            {dragging ? "file_download" : "upload_file"}
          </span>
          <span className="text-sm font-semibold text-[#191c1e]">{dragging ? "Drop files here" : "Drag & drop or click to upload"}</span>
          <span className="text-xs text-[#43474c] mt-1">PDF, JPG, PNG, HTML supported</span>
          <input type="file" multiple className="hidden" onChange={handleChange} accept=".pdf,.jpg,.jpeg,.png,.html,.htm" />
        </label>
      </div>
    </div>
  );
}

interface SidebarProps {
  mobileOpen: boolean;
  onMobileClose: () => void;
}

export default function Sidebar({ mobileOpen, onMobileClose }: SidebarProps) {
  const { username, logout } = useAuth();
  const navigate = useNavigate();
  const [showUpload, setShowUpload] = useState(false);

  const handleUpload = async (files: FileList) => {
    const { api } = await import("@/lib/api");
    await api.upload(Array.from(files));
    navigate("/documents");
  };

  const handleButtonDrop = useCallback(async (files: File[]) => {
    const { api } = await import("@/lib/api");
    await api.upload(files);
    navigate("/documents");
  }, [navigate]);
  const { dragging: btnDragging, ...btnDropHandlers } = useFileDrop(handleButtonDrop);

  const sidebarContent = (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="flex items-center gap-3 px-2 mb-8">
        <div className="w-10 h-10 bg-primary flex items-center justify-center rounded-xl shadow-lg flex-shrink-0">
          <span className="material-symbols-outlined text-white" style={{ fontVariationSettings: "'FILL' 1" }}>receipt_long</span>
        </div>
        <div>
          <h1 className="text-xl font-bold font-headline text-primary leading-tight tracking-tight">Receiptory</h1>
          <p className="text-[10px] uppercase tracking-widest text-[#74777d] font-bold">Precision Management</p>
        </div>
      </div>

      {/* Nav links */}
      <nav className="flex-1 space-y-1">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.exact}
            onClick={onMobileClose}
            className={({ isActive }) =>
              `flex items-center gap-3 px-4 py-3 rounded-lg font-headline font-semibold tracking-tight transition-colors duration-200 ${
                isActive
                  ? "text-primary bg-[#f2f4f6] font-bold"
                  : "text-slate-500 hover:text-primary hover:bg-[#e0e3e5]"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className="material-symbols-outlined"
                  style={isActive ? { fontVariationSettings: "'FILL' 1" } : undefined}
                >
                  {item.icon}
                </span>
                <span>{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* User info */}
      {username && (
        <div className="mb-3 px-2 py-2 rounded-lg bg-[#f2f4f6] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-[#43474c] text-lg">account_circle</span>
            <span className="text-xs font-semibold text-[#191c1e] truncate max-w-[100px]">{username}</span>
          </div>
          <button
            onClick={logout}
            className="text-xs text-[#74777d] hover:text-primary transition-colors font-medium"
            title="Sign out"
          >
            Sign out
          </button>
        </div>
      )}

      {/* Upload button */}
      <button
        onClick={() => setShowUpload(true)}
        {...btnDropHandlers}
        className={`w-full cta-gradient text-white py-3 rounded-xl font-bold flex items-center justify-center gap-2 shadow-lg transition-all font-headline ${
          btnDragging ? "ring-2 ring-white ring-offset-2 scale-[1.03] opacity-90" : "hover:opacity-90 active:scale-95"
        }`}
      >
        <span className="material-symbols-outlined text-sm">{btnDragging ? "file_download" : "add_circle"}</span>
        <span>{btnDragging ? "Drop to Upload" : "Upload Document"}</span>
      </button>
    </div>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="fixed left-0 top-0 h-full w-64 hidden md:flex flex-col p-4 bg-[#f7f9fb] z-50 border-r-0">
        {sidebarContent}
      </aside>

      {/* Mobile overlay */}
      {mobileOpen && (
        <div className="fixed inset-0 z-50 md:hidden">
          <div className="absolute inset-0 bg-black/40" onClick={onMobileClose} />
          <aside className="absolute left-0 top-0 h-full w-64 flex flex-col p-4 bg-[#f7f9fb] shadow-2xl">
            {sidebarContent}
          </aside>
        </div>
      )}

      {/* Mobile bottom nav */}
      <nav className="fixed bottom-0 left-0 w-full h-16 flex justify-around items-center px-4 md:hidden z-40 bg-white shadow-[0_-4px_12px_rgba(0,0,0,0.05)] border-t border-[rgba(116,119,125,0.15)]">
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.exact}
            className={({ isActive }) =>
              `p-2 flex flex-col items-center gap-0.5 rounded-xl transition-colors ${
                isActive ? "text-primary bg-[#f2f4f6]" : "text-slate-400"
              }`
            }
          >
            {({ isActive }) => (
              <>
                <span
                  className="material-symbols-outlined"
                  style={isActive ? { fontVariationSettings: "'FILL' 1" } : undefined}
                >
                  {item.icon}
                </span>
                <span className="text-[10px] font-medium font-body">{item.label.split(" ")[0]}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Upload dialog */}
      {showUpload && (
        <UploadDialog onClose={() => setShowUpload(false)} onUpload={handleUpload} />
      )}
    </>
  );
}
