import { useState } from "react";
import { Button } from "@/components/ui/button";

const API_BASE = import.meta.env.DEV ? "http://localhost:8080/api" : "/api";

interface Props {
  docId: number;
  pageCount: number;
}

export default function PageViewer({ docId, pageCount }: Props) {
  const [currentPage, setCurrentPage] = useState(0);

  return (
    <div className="space-y-2">
      <img
        src={`${API_BASE}/documents/${docId}/pages/${currentPage}`}
        alt={`Page ${currentPage + 1}`}
        className="w-full border rounded"
      />
      {pageCount > 1 && (
        <div className="flex justify-center gap-2">
          <Button variant="outline" size="sm" disabled={currentPage === 0} onClick={() => setCurrentPage(currentPage - 1)}>
            Prev
          </Button>
          <span className="text-sm py-1">Page {currentPage + 1} of {pageCount}</span>
          <Button variant="outline" size="sm" disabled={currentPage >= pageCount - 1} onClick={() => setCurrentPage(currentPage + 1)}>
            Next
          </Button>
        </div>
      )}
    </div>
  );
}
