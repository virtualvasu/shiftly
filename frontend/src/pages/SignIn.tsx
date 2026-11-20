import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { api, ApiError, type DemoGroup } from "@/lib/api";
import { saveSession, type Session } from "@/lib/session";

export function SignIn({ onSignedIn }: { onSignedIn: (s: Session) => void }) {
  const [groups, setGroups] = useState<DemoGroup[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    api.demoUsers().then(setGroups).catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  async function signIn(id: string) {
    setBusy(id);
    try {
      const { token } = await api.signIn(id);
      saveSession({ token, me: { employee_id: id, name: "", role: "employee", company_id: "", company_name: "" } });
      const me = await api.me();
      const s = { token, me };
      saveSession(s);
      onSignedIn(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-3xl flex-col justify-center px-4 py-10">
      <p className="font-mono text-sm font-semibold">cab desk</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">Change your office cab in one message.</h1>
      <p className="mt-2 max-w-xl text-muted-foreground">
        Demo sign-in. Pick a person; their company and role come from the signed token, not from this page.
      </p>
      {error && <p className="mt-6 text-sm text-destructive">{error}</p>}
      <div className="mt-8 grid gap-4 sm:grid-cols-2">
        {(groups ?? [null, null]).map((g, i) => (
          <Card key={g?.company_id ?? i} className="gap-3">
            <CardHeader>
              <CardTitle>{g ? g.company_name : <Skeleton className="h-5 w-32" />}</CardTitle>
              <CardDescription>
                {g?.company_id === "acme" && "Changes 2 h before a drop · 4 seats per cab"}
                {g?.company_id === "globex" && "Changes 1 h before a drop · 6 seats per cab"}
              </CardDescription>
            </CardHeader>
            <CardContent className="px-2">
              <ul>
                {g?.people.map((p) => (
                  <li key={p.employee_id}>
                    <button
                      onClick={() => void signIn(p.employee_id)}
                      disabled={busy !== null}
                      className="flex w-full items-center justify-between rounded-md px-4 py-2.5 text-left text-sm hover:bg-muted focus-visible:outline-2 focus-visible:outline-ring disabled:opacity-60"
                    >
                      <span>
                        {p.name} <span className="font-mono text-xs text-muted-foreground">{p.employee_id}</span>
                      </span>
                      {p.role === "manager" ? <Badge variant="secondary">Manager</Badge> : <span className="text-xs text-muted-foreground">Employee</span>}
                    </button>
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        ))}
      </div>
    </main>
  );
}
