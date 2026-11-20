import type { Me } from "./api";

export type Session = { token: string; me: Me };
const KEY = "cab-desk-session";

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function saveSession(s: Session) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* private mode: session lasts for this tab only */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}
