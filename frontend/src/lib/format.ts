import type { Trip } from "./api";

export function clockTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export const directionLabel = { pickup: "Pickup", drop: "Drop" } as const;
export const directionHint = { pickup: "to office", drop: "home" } as const;

export function tripKey(t: Trip) {
  return `${t.trip_date}-${t.direction}`;
}
