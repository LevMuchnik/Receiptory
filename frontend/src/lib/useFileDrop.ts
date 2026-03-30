import { useCallback, useState, type DragEvent } from "react";

const ACCEPTED = new Set([
  "application/pdf",
  "image/jpeg",
  "image/png",
  "text/html",
]);

const ACCEPTED_EXT = /\.(pdf|jpe?g|png|html?)$/i;

function isAccepted(file: File) {
  return ACCEPTED.has(file.type) || ACCEPTED_EXT.test(file.name);
}

export function useFileDrop(onFiles: (files: File[]) => void) {
  const [dragging, setDragging] = useState(false);
  const counterRef = { current: 0 };

  const onDragEnter = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    counterRef.current += 1;
    if (counterRef.current === 1) setDragging(true);
  }, []);

  const onDragLeave = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    counterRef.current -= 1;
    if (counterRef.current === 0) setDragging(false);
  }, []);

  const onDragOver = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const onDrop = useCallback((e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    counterRef.current = 0;
    const files = Array.from(e.dataTransfer.files).filter(isAccepted);
    if (files.length > 0) onFiles(files);
  }, [onFiles]);

  return { dragging, onDragEnter, onDragLeave, onDragOver, onDrop };
}
