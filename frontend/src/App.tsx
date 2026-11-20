import { useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { clearSession, getSession, type Session } from "@/lib/session";
import { EmployeeView } from "@/pages/EmployeeView";
import { ManagerView } from "@/pages/ManagerView";
import { SignIn } from "@/pages/SignIn";

export default function App() {
  const [session, setSession] = useState<Session | null>(() => getSession());
  const signOut = () => {
    clearSession();
    setSession(null);
  };

  return (
    <>
      {!session && <SignIn onSignedIn={setSession} />}
      {session?.me.role === "employee" && <EmployeeView key={session.me.employee_id} session={session} onSignOut={signOut} />}
      {session?.me.role === "manager" && <ManagerView key={session.me.employee_id} session={session} onSignOut={signOut} />}
      <Toaster position="bottom-center" theme="light" />
    </>
  );
}
