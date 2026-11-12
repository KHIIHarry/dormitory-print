import { Field } from "./ui";

export function DeliveryDateField({
  label,
  value,
  onChange,
  error,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
}) {
  return (
    <Field label={label} error={error}>
      <input
        type="date"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  );
}
