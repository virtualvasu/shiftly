import { LogOut } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Me } from "@/lib/api";
import { navigate } from "@/lib/router";
import { Brand } from "./Brand";
import { DevFaults } from "./DevFaults";

type Props = { me: Me; onSignOut: () => void; onReset?: () => void };

export function AppHeader({ me, onSignOut, onReset }: Props) {
  return (
    <header className="border-b bg-card">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-3 px-4">
        <Brand onClick={() => navigate("/")} />
        <span className="text-sm text-muted-foreground">·</span>
        <span className="truncate text-sm text-muted-foreground">{me.company_name}</span>
        <div className="ml-auto flex items-center gap-1">
          <DevFaults onReset={onReset} />
          <span className="hidden items-center gap-2 pl-2 text-sm sm:flex">
            {me.name}
            {me.role === "manager" && <Badge variant="secondary">Manager</Badge>}
          </span>
          <Button variant="ghost" size="icon" onClick={onSignOut} aria-label="Sign out">
            <LogOut />
          </Button>
        </div>
      </div>
    </header>
  );
}
