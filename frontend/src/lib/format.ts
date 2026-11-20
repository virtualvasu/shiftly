import type { Trip } from "./api";

export function dayLabel(isoDate: string, today: string): string {
  if (isoDate === today) return "Today";
  const d = new Date(isoDate + "T00:00:00");
  const t = new Date(today + "T00:00:00");
  if ((d.getTime() - t.getTime()) / 86_400_000 === 1) return "Tomorrow";
  return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
}

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export const directionLabel = { pickup: "Pickup", drop: "Drop" } as const;
export const directionHint = { pickup: "to office", drop: "home" } as const;

export function tripKey(t: Trip) {
  return `${t.trip_date}-${t.direction}`;
}
