import guestOrderIcon from "../assets/guest-order.svg";
import "./order-origin-icon.css";

/** Decorative artwork shared by admin and courier identity indicators. */
export function OrderOriginIcon({ accountOwned }: { accountOwned: boolean }) {
  return accountOwned ? (
    <span className="order-pickup-icon">
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5 21v-1.5a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4V21" />
      </svg>
    </span>
  ) : (
    <img
      className="order-guest-icon"
      src={guestOrderIcon}
      width="28"
      height="28"
      alt=""
      aria-hidden="true"
    />
  );
}

/** Display-only identity: never opens customer data or triggers a parent row. */
export function OrderOriginIndicator({
  accountOwned,
  className = "",
}: {
  accountOwned: boolean;
  className?: string;
}) {
  return (
    <span
      className={`order-origin-indicator ${className}`.trim()}
      role="img"
      aria-label={accountOwned ? "账号订单" : "游客订单"}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
      }}
    >
      <OrderOriginIcon accountOwned={accountOwned} />
    </span>
  );
}
