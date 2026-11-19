import type { InputHTMLAttributes } from "react";

type AmountInputProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "type" | "inputMode" | "pattern" | "value" | "onChange" | "defaultValue"
> & {
  value: string;
  onValueChange: (value: string) => void;
};

/** Keep invalid edits atomic so a pasted amount never changes meaning. */
export function AmountInput({
  value,
  onValueChange,
  ...props
}: AmountInputProps) {
  return (
    <input
      {...props}
      type="text"
      inputMode="decimal"
      pattern={"[0-9]+(?:\\.[0-9]{1,2})?"}
      value={value}
      onChange={(event) => {
        const input = event.currentTarget;
        const raw = input.value;
        const numeric = raw.replace(/[０-９．]/g, (character) =>
          String.fromCharCode(character.charCodeAt(0) - 0xfee0),
        );
        if (!/^[0-9]*(?:\.[0-9]{0,2})?$/.test(numeric)) {
          const removed = Math.max(0, raw.length - value.length);
          const caret = Math.max(
            0,
            (input.selectionStart ?? raw.length) - removed,
          );
          input.value = value;
          input.setSelectionRange(caret, caret);
          return;
        }
        const next = numeric.startsWith(".") ? `0${numeric}` : numeric;
        if (next !== value) onValueChange(next);
      }}
    />
  );
}
