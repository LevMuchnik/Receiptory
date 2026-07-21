import { useEffect, useMemo, useState } from "react";
import { Autocomplete } from "@base-ui/react/autocomplete";

export type ModelOption = {
  id: string;
  provider: string | null;
  input_price_per_1m: number | null;
  output_price_per_1m: number | null;
  supports_reasoning: boolean;
  max_output_tokens?: number | null;
};

const MAX_RESULTS = 50;

// Searchable model picker over litellm's vision-capable chat registry (#13, PR3).
// Free-text friendly: the committed value is exactly the model id (or whatever
// the user typed), so a self-hosted / brand-new id that isn't in the registry
// still saves. Filtering is done here (not by base-ui) so it can match the id
// AND the provider field without polluting the committed value — typing "flash",
// "gemini", or "bedrock" all narrow the list. mode="none" tells base-ui the
// item list is already filtered.
export default function ModelCombobox({
  value,
  models,
  onCommit,
  className,
  placeholder,
}: {
  value: string;
  models: ModelOption[];
  onCommit: (id: string) => void;
  className?: string;
  placeholder?: string;
}) {
  const [text, setText] = useState(value ?? "");
  // Keep local input text in sync when the saved value changes elsewhere.
  useEffect(() => setText(value ?? ""), [value]);

  const commit = (v: string) => {
    if (v !== value) onCommit(v);
  };

  const { filtered, total } = useMemo(() => {
    const q = text.trim().toLowerCase();
    // When the input exactly equals a saved id, don't filter to just that one —
    // show the full list so the user can browse after opening.
    const matches = q && q !== value.toLowerCase()
      ? models.filter(
          (m) => m.id.toLowerCase().includes(q) || (m.provider ?? "").toLowerCase().includes(q)
        )
      : models;
    return { filtered: matches.slice(0, MAX_RESULTS), total: matches.length };
  }, [text, value, models]);

  const fmtPrice = (m: ModelOption) =>
    m.input_price_per_1m != null && m.output_price_per_1m != null
      ? `$${m.input_price_per_1m} / $${m.output_price_per_1m}`
      : "";

  return (
    <Autocomplete.Root
      items={filtered}
      mode="none"
      value={text}
      onValueChange={(v, details) => {
        setText(v);
        // Selecting a suggestion fills the input with the model id and saves.
        if (details?.reason === "item-press") commit(v);
      }}
      itemToStringValue={(item: ModelOption) => item.id}
    >
      <Autocomplete.Input
        placeholder={placeholder ?? "gpt-4o"}
        className={className}
        onBlur={() => commit(text)}
      />
      <Autocomplete.Portal>
        <Autocomplete.Positioner sideOffset={4} className="isolate z-50">
          <Autocomplete.Popup className="max-h-72 w-(--anchor-width) min-w-64 overflow-y-auto rounded-lg bg-popover text-popover-foreground shadow-md ring-1 ring-foreground/10 p-1">
            <Autocomplete.Empty className="px-2 py-3 text-xs text-muted-foreground">
              No registry match — the typed value will be used as-is.
            </Autocomplete.Empty>
            <Autocomplete.List>
              {(item: ModelOption) => (
                <Autocomplete.Item
                  key={item.id}
                  value={item}
                  className="flex items-center justify-between gap-3 rounded-md px-2 py-1.5 text-sm cursor-default select-none outline-none data-highlighted:bg-accent data-highlighted:text-accent-foreground"
                >
                  <span className="truncate font-mono text-[12px]">{item.id}</span>
                  <span className="flex items-center gap-1.5 shrink-0">
                    {item.supports_reasoning && (
                      <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-[#7bf8a1]/30 text-[#007239]">
                        reasoning
                      </span>
                    )}
                    <span className="text-[11px] text-muted-foreground font-mono">{fmtPrice(item)}</span>
                  </span>
                </Autocomplete.Item>
              )}
            </Autocomplete.List>
            {total > MAX_RESULTS && (
              <div className="px-2 py-1.5 text-[11px] text-muted-foreground border-t border-foreground/10">
                Showing {MAX_RESULTS} of {total} — keep typing to narrow.
              </div>
            )}
          </Autocomplete.Popup>
        </Autocomplete.Positioner>
      </Autocomplete.Portal>
    </Autocomplete.Root>
  );
}
