import { useEffect, useState } from "react";
import { shanghaiToday } from "./slot-date-range";

export function useShanghaiToday() {
  const [today, setToday] = useState(() => shanghaiToday());
  useEffect(() => {
    const refreshToday = () => setToday(shanghaiToday());
    const timer = window.setInterval(refreshToday, 60_000);
    document.addEventListener("visibilitychange", refreshToday);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshToday);
    };
  }, []);
  return today;
}
