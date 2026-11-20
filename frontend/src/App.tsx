import { useEffect, useState } from "react";

import { Toaster } from "@/components/ui/sonner";
import { navigate, usePath } from "@/lib/router";
import { clearSession, getSession, type Session } from "@/lib/session";
import { EmployeeView } from "@/pages/EmployeeView";
import { Landing } from "@/pages/Landing";
import { Login } from "@/pages/Login";
import { ManagerView } from "@/pages/ManagerView";

export default function App() {
  const path = usePath();
  const [session, setSession] = useState<Session | null>(() => getSession());
  const inApp = path.startsWith("/app");

  // Signed-out visitors can't open the app; signed-in ones skip the login page.
  useEffect(() => {
    if (inApp && !session) navigate("/login");
    if (path === "/login" && session) navigate("/app");
  }, [inApp, path, session]);

  const signOut = () => {
    clearSession();
    setSession(null);
    navigate("/");
  };

  let page;
  if (path === "/login") page = <Login onSignedIn={setSession} />;
  else if (inApp && session?.me.role === "employee") page = <EmployeeView key={session.me.employee_id} session={session} onSignOut={signOut} />;
  else if (inApp && session?.me.role === "manager") page = <ManagerView key={session.me.employee_id} session={session} onSignOut={signOut} />;
  else if (inApp) page = null;
  else page = <Landing signedIn={!!session} />;

  return (
    <>
      {page}
      <Toaster position="bottom-center" theme="light" />
    </>
  );
}
