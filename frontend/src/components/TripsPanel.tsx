import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type { Trip } from "@/lib/api";
import { dayLabel, directionHint, directionLabel, tripKey } from "@/lib/format";
import { Plate } from "./Plate";

type Props = { trips: Trip[] | null; changed: Set<string> };

export function TripsPanel({ trips, changed }: Props) {
  const today = trips?.[0]?.trip_date ?? "";
  const days = [...new Set((trips ?? []).map((t) => t.trip_date))];

  return (
    <Card className="gap-0 py-0">
      <CardHeader className="border-b px-5 py-4">
        <CardTitle className="text-sm font-medium">Your cabs</CardTitle>
      </CardHeader>
      <CardContent className="px-0">
        {trips === null && (
          <div className="space-y-3 p-5">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-12 w-full" />
          </div>
        )}
        {trips?.length === 0 && (
          <p className="p-5 text-sm text-muted-foreground">No cabs booked for today or tomorrow.</p>
        )}
        {days.map((day) => (
          <section key={day} className="border-b last:border-b-0">
            <h3 className="px-5 pt-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
              {dayLabel(day, today)}
            </h3>
            <ul>
              {(trips ?? [])
                .filter((t) => t.trip_date === day)
                .map((t) => (
                  <li key={tripKey(t)} className="flex items-center gap-4 px-5 py-3">
                    <time
                      key={t.time}
                      className={
                        "w-[4.5rem] rounded-md bg-amber-soft px-2 py-1 text-center font-mono text-xl font-semibold tabular-nums text-foreground" +
                        (changed.has(tripKey(t)) ? " animate-in fade-in slide-in-from-top-2 duration-500" : "")
                      }
                    >
                      {t.time}
                    </time>
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium">{directionLabel[t.direction]}</p>
                      <p className="truncate text-xs text-muted-foreground">
                        {directionHint[t.direction]} · {t.route}
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <Plate number={t.vehicle_no} />
                      {t.pending_request && (
                        <Badge variant="outline" className="border-amber text-[10px] text-foreground">
                          With manager
                        </Badge>
                      )}
                    </div>
                  </li>
                ))}
            </ul>
          </section>
        ))}
      </CardContent>
    </Card>
  );
}
