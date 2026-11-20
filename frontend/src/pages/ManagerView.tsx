import { Check, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

import { AppHeader } from "@/components/AppHeader";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type AuditRow, type PendingRequest } from "@/lib/api";
import { clockTime, directionLabel } from "@/lib/format";
import type { Session } from "@/lib/session";

const POLL_MS = 5000;

function change(r: PendingRequest) {
  if (r.change_type === "cancel") return <span>Cancel <span className="font-mono">{r.from}</span></span>;
  return (
    <span className="font-mono tabular-nums">
      {r.from} <span className="text-muted-foreground">→</span> <span className="rounded bg-amber-soft px-1">{r.to}</span>
    </span>
  );
}

export function ManagerView({ session, onSignOut }: { session: Session; onSignOut: () => void }) {
  const [pending, setPending] = useState<PendingRequest[] | null>(null);
  const [audit, setAudit] = useState<AuditRow[]>([]);
  const [rejecting, setRejecting] = useState<PendingRequest | null>(null);
  const [note, setNote] = useState("");
  const [working, setWorking] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [p, a] = await Promise.all([api.pendingRequests(), api.audit()]);
      setPending(p);
      setAudit(a);
    } catch {
      /* next poll retries */
    }
  }, []);

  useEffect(() => {
    void load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  async function decide(r: PendingRequest, decision: "approve" | "reject", reason?: string) {
    setWorking(r.request_id);
    try {
      await api.decide(r.request_id, decision, reason);
      toast(decision === "approve" ? `Approved ${r.request_id}` : `Rejected ${r.request_id}`);
    } catch (e) {
      toast(e instanceof ApiError ? e.message : "Couldn't save the decision. Try again.");
    } finally {
      setWorking(null);
      setRejecting(null);
      setNote("");
      void load();
    }
  }

  return (
    <div className="min-h-dvh">
      <AppHeader me={session.me} onSignOut={onSignOut} onReset={load} />
      <main className="mx-auto max-w-6xl px-4 py-6">
        <h1 className="text-xl font-semibold tracking-tight">Shift-change approvals</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          These requests broke a {session.me.company_name} rule and the employee still wants them. Approving overrides the cut-off; it can't create a seat.
        </p>
        <Tabs defaultValue="pending" className="mt-5">
          <TabsList>
            <TabsTrigger value="pending">Waiting{pending?.length ? ` (${pending.length})` : ""}</TabsTrigger>
            <TabsTrigger value="audit">Audit log</TabsTrigger>
          </TabsList>
          <TabsContent value="pending">
            <Card className="py-0">
              {pending?.length === 0 ? (
                <p className="p-6 text-sm text-muted-foreground">Nothing waiting for you. New requests show up here automatically.</p>
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="pl-5">Employee</TableHead>
                      <TableHead>Trip</TableHead>
                      <TableHead>Change</TableHead>
                      <TableHead className="hidden lg:table-cell">Rule</TableHead>
                      <TableHead>Sent</TableHead>
                      <TableHead className="pr-5 text-right">Decision</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(pending ?? []).map((r) => (
                      <TableRow key={r.request_id}>
                        <TableCell className="pl-5">
                          <div className="font-medium">{r.employee_name}</div>
                          <div className="font-mono text-xs text-muted-foreground">{r.request_id}</div>
                        </TableCell>
                        <TableCell>{directionLabel[r.direction]} {r.day}</TableCell>
                        <TableCell>{change(r)}</TableCell>
                        <TableCell className="hidden min-w-56 max-w-xs text-xs whitespace-normal text-muted-foreground lg:table-cell">{r.reason}</TableCell>
                        <TableCell className="font-mono text-xs">{clockTime(r.created_at)}</TableCell>
                        <TableCell className="pr-5 text-right">
                          <div className="inline-flex gap-2">
                            <Button size="sm" variant="outline" disabled={working === r.request_id} onClick={() => setRejecting(r)}>
                              <X /> Reject
                            </Button>
                            <Button size="sm" disabled={working === r.request_id} onClick={() => void decide(r, "approve")}>
                              <Check /> Approve
                            </Button>
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </Card>
          </TabsContent>
          <TabsContent value="audit">
            <Card className="py-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-5">Time</TableHead>
                    <TableHead>Who</TableHead>
                    <TableHead>What</TableHead>
                    <TableHead className="pr-5">Request</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {audit.map((a) => (
                    <TableRow key={a.id}>
                      <TableCell className="pl-5 font-mono text-xs">{clockTime(a.created_at)}</TableCell>
                      <TableCell className="text-sm">{a.actor_id} <span className="text-muted-foreground">({a.actor_type})</span></TableCell>
                      <TableCell className="text-sm">{a.action.replace(/_/g, " ")}</TableCell>
                      <TableCell className="pr-5 font-mono text-xs">{a.request_id ?? "—"}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          </TabsContent>
        </Tabs>
      </main>

      <Dialog open={rejecting !== null} onOpenChange={(o) => !o && setRejecting(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject {rejecting?.request_id}?</DialogTitle>
            <DialogDescription>
              {rejecting?.employee_name} keeps their {rejecting?.from} {rejecting?.direction}. Your note is shown to them.
            </DialogDescription>
          </DialogHeader>
          <Textarea value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} placeholder="Optional note, e.g. Not this week" />
          <DialogFooter>
            <Button variant="outline" onClick={() => setRejecting(null)}>Keep waiting</Button>
            <Button variant="destructive" disabled={working !== null} onClick={() => rejecting && void decide(rejecting, "reject", note.trim() || undefined)}>
              Reject request
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
