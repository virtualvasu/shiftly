import { ArrowDown, ArrowRight, Check, CircleSlash, Clock3, UsersRound } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { Plate } from "@/components/Plate";
import { Button } from "@/components/ui/button";
import { navigate } from "@/lib/router";

const STEPS = [
  {
    title: "Say it the way you'd say it to the desk",
    body: "Running late, leaving early, on leave tomorrow. No forms and no menus. If something's unclear, it asks instead of guessing.",
  },
  {
    title: "It finds your cab",
    body: "It looks only at your own trips, from your company's roster.",
  },
  {
    title: "It checks your company's rule",
    body: "Every company sets its own cut-off. It's applied exactly the same way every time, whoever asks and however they ask.",
  },
  {
    title: "It looks for a free seat",
    body: "Cabs are shared and seats run out. If the cab you want is full, you hear about the nearest one that isn't.",
  },
  {
    title: "You get a straight answer",
    body: "Booked, the nearest free cab, or sent to your manager when a rule needs bending. You keep your current seat while they decide.",
  },
];

/** Which step's text is in the middle of the screen. */
function useActiveStep(count: number) {
  const refs = useRef<(HTMLElement | null)[]>([]);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(Number((e.target as HTMLElement).dataset.step));
        }
      },
      { rootMargin: "-45% 0px -45% 0px" },
    );
    refs.current.slice(0, count).forEach((el) => el && io.observe(el));
    return () => io.disconnect();
  }, [count]);
  return { active, refs };
}

/** A layer of the story card that slides in once its step is reached. */
function Reveal({ on, children, className = "" }: { on: boolean; children: ReactNode; className?: string }) {
  return (
    <div
      className={
        "transition-all duration-500 ease-out motion-reduce:transition-none " +
        (on ? "translate-y-0 opacity-100" : "pointer-events-none translate-y-3 opacity-0") +
        " " +
        className
      }
    >
      {children}
    </div>
  );
}

function StoryCard({ step }: { step: number }) {
  return (
    <div aria-hidden="true" className="rounded-2xl border bg-card p-5 shadow-sm sm:p-6">
      <div className="flex items-center justify-between border-b pb-3 text-xs text-muted-foreground">
        <span className="font-mono font-semibold text-foreground">cab desk</span>
        <span>Today · 17:30</span>
      </div>

      <div className="space-y-4 pt-4">
        <Reveal on={step >= 0} className="flex justify-end">
          <p className="rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
            Can I leave at 8 tonight?
          </p>
        </Reveal>

        <Reveal on={step >= 1}>
          <div className="flex items-center gap-4 rounded-xl border px-4 py-3">
            <span className="rounded-md bg-amber-soft px-2 py-1 font-mono text-lg font-semibold tabular-nums">19:00</span>
            <div className="flex-1 text-sm">
              <p className="font-medium">Your drop today</p>
              <p className="text-xs text-muted-foreground">home · Whitefield</p>
            </div>
            <Plate number="KA01-0016" />
          </div>
        </Reveal>

        <Reveal on={step >= 2}>
          <div className="flex items-start gap-3 rounded-xl bg-muted px-4 py-3 text-sm">
            <Clock3 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
            <div className="flex-1">
              <p className="font-medium">Your company: changes up to 1 h before the cab</p>
              <p className="text-xs text-muted-foreground">The 19:00 is 1 h 30 min away and the 20:00 is 2 h 30 min away. Both fine.</p>
            </div>
            <Check className={"size-4 shrink-0 text-emerald-600 transition-transform duration-500 motion-reduce:transition-none " + (step >= 2 ? "scale-100" : "scale-0")} />
          </div>
        </Reveal>

        <Reveal on={step >= 3}>
          <div className="flex items-center gap-3 rounded-xl bg-muted px-4 py-3 text-sm">
            <UsersRound className="size-4 shrink-0 text-muted-foreground" />
            <p className="flex-1 font-medium">20:00 cab</p>
            <div className="flex gap-1.5">
              {[0, 1, 2, 3].map((i) => (
                <span
                  key={i}
                  style={{ transitionDelay: `${i * 90}ms` }}
                  className={
                    "size-4 rounded-[4px] border transition-colors duration-500 motion-reduce:transition-none " +
                    (i < 3 ? "border-foreground/20 bg-foreground/20" : step >= 3 ? "border-amber bg-amber" : "bg-card")
                  }
                />
              ))}
            </div>
            <span className="text-xs text-muted-foreground">1 free</span>
          </div>
        </Reveal>

        <Reveal on={step >= 4}>
          <p className="max-w-[90%] rounded-2xl rounded-bl-md border bg-card px-4 py-2.5 text-sm leading-relaxed">
            Done. Your drop today is now at <span className="font-mono font-semibold">20:00</span>, cab KA01-0021.
          </p>
        </Reveal>
      </div>
    </div>
  );
}

const WHEN_NO = [
  { icon: UsersRound, title: "The cab is full", body: "You get the nearest cabs that still have a seat." },
  { icon: Clock3, title: "It's past your company's cut-off", body: "It explains the rule and offers to ask your manager. Your seat stays yours while they decide." },
  { icon: CircleSlash, title: "Something is down", body: "It tells you nothing was changed and what to do next. It never pretends." },
];

export function Landing({ signedIn }: { signedIn: boolean }) {
  const { active, refs } = useActiveStep(STEPS.length);
  const enter = () => navigate(signedIn ? "/app" : "/login");

  return (
    <div className="min-h-dvh bg-background">
      <header className="sticky top-0 z-20 border-b bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4">
          <span className="font-mono text-sm font-semibold">cab desk</span>
          <Button size="sm" onClick={enter}>{signedIn ? "Open cab desk" : "Sign in"}</Button>
        </div>
      </header>

      {/* Hero */}
      <section className="mx-auto grid max-w-6xl items-center gap-12 px-4 pb-20 pt-16 md:grid-cols-[1.1fr_1fr] md:pt-24">
        <div>
          <p className="text-sm font-medium text-muted-foreground">For everyone who rides the office cab</p>
          <h1 className="mt-3 text-4xl font-semibold leading-[1.1] tracking-tight sm:text-5xl">
            Change your office cab in <span className="whitespace-nowrap rounded-md bg-amber-soft px-1.5">one message.</span>
          </h1>
          <p className="mt-5 max-w-lg text-lg text-muted-foreground">
            Meeting running late? Leaving early? Working from home tomorrow? Tell the cab desk in plain words. It checks your
            company's rules and the free seats, then books it, finds the nearest cab, or asks your manager.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <Button size="lg" onClick={enter}>
              {signedIn ? "Open cab desk" : "Sign in"} <ArrowRight />
            </Button>
            <Button size="lg" variant="ghost" onClick={() => document.getElementById("how")?.scrollIntoView({ behavior: "smooth" })}>
              See how it works <ArrowDown />
            </Button>
          </div>
        </div>

        <div aria-hidden="true" className="space-y-3 rounded-2xl border bg-card p-5 shadow-sm">
          <p className="ml-auto w-fit rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
            I'm on leave tomorrow
          </p>
          <p className="w-fit max-w-[90%] rounded-2xl rounded-bl-md border px-4 py-2.5 text-sm">
            Done. Your 09:00 pickup tomorrow is cancelled. Done. Your 19:00 drop tomorrow is cancelled.
          </p>
          <p className="ml-auto w-fit rounded-2xl rounded-br-md bg-primary px-4 py-2.5 text-sm text-primary-foreground">
            Actually, book the drop back
          </p>
          <p className="w-fit max-w-[90%] rounded-2xl rounded-bl-md border px-4 py-2.5 text-sm">
            Done. Your 19:00 drop tomorrow is booked again, cab KA01-0039.
          </p>
        </div>
      </section>

      {/* How it works: the card on the left follows the step you're reading */}
      <section id="how" className="border-t bg-card/40">
        <div className="mx-auto max-w-6xl px-4 py-20">
          <h2 className="text-3xl font-semibold tracking-tight">What happens when you send a message</h2>
          <p className="mt-2 max-w-xl text-muted-foreground">Five checks, in about a second.</p>

          <div className="mt-12 grid gap-10 md:grid-cols-2 md:gap-16">
            <div className="sticky top-16 z-10 self-start md:top-28">
              <StoryCard step={active} />
            </div>
            <ol className="space-y-6 md:space-y-0">
              {STEPS.map((s, i) => (
                <li
                  key={s.title}
                  data-step={i}
                  ref={(el) => {
                    refs.current[i] = el;
                  }}
                  className={
                    "flex min-h-[40vh] flex-col justify-center transition-opacity duration-300 motion-reduce:transition-none md:min-h-[70vh] " +
                    (active === i ? "opacity-100" : "opacity-35")
                  }
                >
                  <h3 className="text-xl font-semibold tracking-tight">{s.title}</h3>
                  <p className="mt-2 max-w-md text-muted-foreground">{s.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </section>

      {/* When the answer is no */}
      <section className="mx-auto max-w-6xl px-4 py-20">
        <h2 className="text-3xl font-semibold tracking-tight">When the answer is no, you still know what to do</h2>
        <div className="mt-8 grid gap-4 md:grid-cols-3">
          {WHEN_NO.map(({ icon: Icon, title, body }) => (
            <div key={title} className="rounded-xl border bg-card p-5">
              <Icon className="size-5 text-muted-foreground" />
              <p className="mt-3 font-medium">{title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{body}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="border-t">
        <div className="mx-auto flex max-w-6xl flex-col items-start justify-between gap-6 px-4 py-14 sm:flex-row sm:items-center">
          <p className="text-2xl font-semibold tracking-tight">Your cab, sorted in one message.</p>
          <Button size="lg" onClick={enter}>
            {signedIn ? "Open cab desk" : "Sign in"} <ArrowRight />
          </Button>
        </div>
      </section>
    </div>
  );
}
