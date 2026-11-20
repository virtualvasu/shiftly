import { FlaskConical } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { api, type FaultSystem, type Faults } from "@/lib/api";

const SYSTEMS: { id: FaultSystem; label: string }[] = [
  { id: "roster", label: "Roster" },
  { id: "cabs", label: "Cab schedule" },
  { id: "booking", label: "Booking system" },
];

/** Demo controls: make a backing system fail or slow, to show how failures reach the employee. */
export function DevFaults({ onReset }: { onReset?: () => void }) {
  const [faults, setFaults] = useState<Faults>({});

  useEffect(() => {
    api.faults().then(setFaults).catch(() => {});
  }, []);

  async function toggle(system: FaultSystem, key: "fail" | "slow", on: boolean) {
    const cur = faults[system] ?? { latency_s: 0, fail: false };
    const next = key === "fail" ? { ...cur, fail: on } : { ...cur, latency_s: on ? 3 : 0 };
    setFaults(await api.setFault(system, next));
  }

  async function reset() {
    await api.resetData();
    setFaults({});
    toast("Sample data reset for today.");
    onReset?.();
  }

  return (
    <Sheet>
      <SheetTrigger asChild>
        <Button variant="ghost" size="sm">
          <FlaskConical />
          <span className="hidden sm:inline">Simulate failures</span>
        </Button>
      </SheetTrigger>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>Simulate failures</SheetTitle>
          <SheetDescription>Demo only. Make a system fail or respond slowly (3 s, over the 1.5 s limit).</SheetDescription>
        </SheetHeader>
        <div className="space-y-5 px-4">
          {SYSTEMS.map((s) => (
            <div key={s.id} className="space-y-2">
              <p className="text-sm font-medium">{s.label}</p>
              <div className="flex items-center justify-between">
                <Label htmlFor={`${s.id}-fail`} className="font-normal text-muted-foreground">Fails</Label>
                <Switch id={`${s.id}-fail`} checked={!!faults[s.id]?.fail} onCheckedChange={(v) => void toggle(s.id, "fail", v)} />
              </div>
              <div className="flex items-center justify-between">
                <Label htmlFor={`${s.id}-slow`} className="font-normal text-muted-foreground">Slow</Label>
                <Switch id={`${s.id}-slow`} checked={(faults[s.id]?.latency_s ?? 0) > 0} onCheckedChange={(v) => void toggle(s.id, "slow", v)} />
              </div>
            </div>
          ))}
          <Separator />
          <Button variant="outline" className="w-full" onClick={() => void reset()}>
            Reset sample data
          </Button>
        </div>
      </SheetContent>
    </Sheet>
  );
}
