import { useCallback, useEffect, useRef, useState } from "react";

import { AppHeader } from "@/components/AppHeader";
import { ChatPanel } from "@/components/ChatPanel";
import { TripsPanel } from "@/components/TripsPanel";
import { api, type Trip } from "@/lib/api";
import { tripKey } from "@/lib/format";
import type { Session } from "@/lib/session";

export function EmployeeView({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [trips, setTrips] = useState<Trip[] | null>(null);
  const [changed, setChanged] = useState<Set<string>>(new Set());
  const previous = useRef<Map<string, string>>(new Map());

  const refresh = useCallback(async () => {
    try {
      const next = await api.myRoster();
      const moved = new Set(next.filter((t) => previous.current.has(tripKey(t)) && previous.current.get(tripKey(t)) !== t.time).map(tripKey));
      previous.current = new Map(next.map((t) => [tripKey(t), t.time]));
      setChanged(moved);
      setTrips(next);
    } catch {
      /* keep showing the last known trips */
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="min-h-dvh">
      <AppHeader me={session.me} onSignOut={onSignOut} onReset={() => window.location.reload()} />
      <main className="mx-auto grid max-w-6xl gap-4 px-4 py-4 md:grid-cols-[1fr_20rem]">
        <ChatPanel name={session.me.name} onReply={refresh} />
        <aside className="order-first md:order-none">
          <TripsPanel trips={trips} changed={changed} />
        </aside>
      </main>
    </div>
  );
}
