import {
  useEffect,
  useId,
  useState,
  type ComponentPropsWithoutRef,
  type ReactNode,
} from "react";
import "./search-suggestions.css";

export type SearchOption = {
  value: string;
  label?: string;
  detail?: string;
  keywords?: string;
  key?: string;
};

export function useSearchTerm(value: string, minimum = 2) {
  const term = value.trim();
  const [settled, setSettled] = useState("");
  useEffect(() => {
    if (term.length < minimum) return;
    const timer = window.setTimeout(() => setSettled(term), 250);
    return () => window.clearTimeout(timer);
  }, [term, minimum]);
  return term.length >= minimum && settled === term ? settled : "";
}

export function matchingSearchOptions(
  options: readonly (SearchOption | string)[],
  value: string,
) {
  const query = value.trim().toLowerCase();
  const starts: SearchOption[] = [];
  const contains: SearchOption[] = [];
  const seen = new Set<string>();
  for (const item of options) {
    const option = typeof item === "string" ? { value: item } : item;
    const key = option.key ?? option.value;
    if (!option.value || seen.has(key)) continue;
    const texts = [option.label, option.value, option.detail, option.keywords]
      .filter(Boolean)
      .map((text) => text!.toLowerCase());
    if (!texts.some((text) => text.includes(query))) continue;
    seen.add(key);
    (texts.some((text) => text.startsWith(query)) ? starts : contains).push(
      option,
    );
  }
  return [...starts, ...contains].slice(0, 6);
}

type Props = Omit<
  ComponentPropsWithoutRef<"input">,
  "value" | "onChange" | "onSelect"
> & {
  value: string;
  onValueChange: (value: string) => void;
  options: readonly (SearchOption | string)[];
  onPick?: (option: SearchOption) => void;
  label?: string;
  hint?: string;
  wrapperClassName?: string;
  icon?: ReactNode;
  minimum?: number;
  pending?: boolean;
  status?: string;
  inline?: boolean;
};

export function SearchSuggestions({
  value,
  onValueChange,
  options,
  onPick,
  label,
  hint,
  wrapperClassName,
  icon,
  minimum = 1,
  pending,
  status,
  inline,
  id,
  type = "search",
  ...input
}: Props) {
  const uid = useId();
  const inputId = id ?? uid;
  const listId = `${uid}-options`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const matches =
    value.trim().length >= minimum ? matchingSearchOptions(options, value) : [];
  const expanded =
    open &&
    !input.disabled &&
    value.trim().length >= minimum &&
    (matches.length > 0 || !!status || !!pending);
  const activeOption = matches.findIndex(
    (option) => (option.key ?? option.value) === active,
  );
  const choose = (option: SearchOption) => {
    onValueChange(option.value);
    onPick?.(option);
    setOpen(false);
    setActive(null);
  };

  return (
    <div
      className={[
        "search-combobox",
        label && "field",
        inline && "search-combobox-inline",
        wrapperClassName,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {label && <label htmlFor={inputId}>{label}</label>}
      {icon}
      <input
        {...input}
        id={inputId}
        type={type}
        role="combobox"
        autoComplete="off"
        aria-autocomplete="list"
        aria-describedby={hint ? `${uid}-hint` : input["aria-describedby"]}
        aria-expanded={expanded}
        aria-controls={expanded && matches.length ? listId : undefined}
        aria-activedescendant={
          expanded && activeOption >= 0
            ? `${listId}-${activeOption}`
            : undefined
        }
        value={value}
        onChange={(event) => {
          setOpen(true);
          setActive(null);
          onValueChange(event.target.value);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => {
          setOpen(false);
          setActive(null);
        }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing || event.keyCode === 229) return;
          if (event.key === "Escape" && expanded) {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            setActive(null);
          } else if (
            ["ArrowDown", "ArrowUp"].includes(event.key) &&
            matches.length
          ) {
            event.preventDefault();
            setOpen(true);
            const next =
              event.key === "ArrowDown"
                ? Math.min(activeOption + 1, matches.length - 1)
                : activeOption < 0
                  ? matches.length - 1
                  : Math.max(0, activeOption - 1);
            setActive(matches[next].key ?? matches[next].value);
            requestAnimationFrame(() => {
              document
                .getElementById(`${listId}-${next}`)
                ?.scrollIntoView({ block: "nearest" });
            });
          } else if (event.key === "Enter" && expanded && activeOption >= 0) {
            event.preventDefault();
            choose(matches[activeOption]);
          }
        }}
      />
      {hint && <small id={`${uid}-hint`}>{hint}</small>}
      {expanded && (
        <div className="search-suggestion-popover">
          {matches.length ? (
            <div id={listId} role="listbox" aria-label="搜索建议">
              {matches.map((option, index) => (
                <div
                  key={option.key ?? option.value}
                  id={`${listId}-${index}`}
                  role="option"
                  aria-selected={activeOption === index}
                  className="search-suggestion-option"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => choose(option)}
                >
                  <span>{option.label || option.value}</span>
                  {option.detail && <small>{option.detail}</small>}
                </div>
              ))}
            </div>
          ) : (
            <div className="search-suggestion-status" role="status">
              {pending ? "正在查找…" : status}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
