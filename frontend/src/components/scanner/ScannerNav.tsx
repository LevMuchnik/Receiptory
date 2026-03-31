interface ScannerNavProps {
  pageCount: number;
  onClose: () => void;
}

export default function ScannerNav({ pageCount, onClose }: ScannerNavProps) {
  return (
    <div className="shrink-0 flex items-center justify-between px-4 h-14 bg-black/60 text-white backdrop-blur-sm z-40">
      <button onClick={onClose} className="p-2 rounded-full hover:bg-white/10 transition-colors">
        <span className="material-symbols-outlined">close</span>
      </button>
      <h1 className="font-headline font-bold text-sm">Document Scanner</h1>
      <div className="flex items-center gap-1.5 bg-white/10 px-3 py-1 rounded-full">
        <span className="material-symbols-outlined text-sm">description</span>
        <span className="text-sm font-bold">{pageCount}</span>
      </div>
    </div>
  );
}
