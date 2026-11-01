import { useEffect, useState, type InputHTMLAttributes } from "react";
type NumberInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "type" | "value" | "onChange" | "defaultValue"
> & {
  value: number | null | undefined;
  onValueChange: (value: number | null) => void;
  /** Multiply the display value by this factor, e.g. 100 for yuan/cents. */
  scale?: number;
  step?: number | string;
};
/**
 * A controlled numeric input that lets the user clear the field and type a
 * replacement without the page forcing a fallback number. The visible draft is
 * kept as text while focused, so an empty or in-progress edit is preserved;
 * the owner only receives a number, or null when the field is empty.
 */
export function NumberInput({
  value,
  onValueChange,
  scale = 1,
  step,
  onFocus,
  onBlur,
  ...rest
}: NumberInputProps) {
  const format = (next: number | null | undefined) =>
    next === null || next === undefined ? "" : String(next / scale);
  const [focused, setFocused] = useState(false);
  const [draft, setDraft] = useState(() => format(value));
  useEffect(() => {
    if (!focused) setDraft(format(value));
  }, [value, scale, focused]);
  return (
    <input
      {...rest}
      type="number"
      step={step}
      value={draft}
      onFocus={(event) => {
        setFocused(true);
        onFocus?.(event);
      }}
      onBlur={(event) => {
        setFocused(false);
        onBlur?.(event);
      }}
      onChange={(event) => {
        const raw = event.target.value;
        setDraft(raw);
        if (raw.trim() === "") {
          onValueChange(null);
          return;
        }
        const parsed = Number(raw);
        if (!Number.isFinite(parsed)) {
          onValueChange(null);
          return;
        }
        onValueChange(scale === 1 ? parsed : Math.round(parsed * scale));
      }}
    />
  );
}
