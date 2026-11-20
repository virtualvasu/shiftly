import { ArrowUp } from "lucide-react";
import { useEffect, useRef, useState, type KeyboardEvent } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, type Message } from "@/lib/api";
import { clockTime } from "@/lib/format";

const SUGGESTIONS = ["Can I leave at 8 tonight?", "I'm on leave tomorrow", "Which cab am I on tonight?"];
const POLL_MS = 3000;

type Props = { name: string; onReply: () => void };
type Bubble = Message & { pending?: boolean; failed?: boolean };

export function ChatPanel({ name, onReply }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Bubble[]>([]);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const lastId = messages.reduce((m, x) => (x.id > 0 ? Math.max(m, x.id) : m), 0);

  // Pick up the latest conversation after a reload.
  useEffect(() => {
    api.latestConversation().then(async ({ conversation_id }) => {
      if (!conversation_id) return;
      setConversationId(conversation_id);
      setMessages(await api.messages(conversation_id));
    }).catch(() => {});
  }, []);

  // Manager decisions arrive in the conversation while we wait.
  useEffect(() => {
    if (!conversationId || sending) return;
    const id = setInterval(async () => {
      try {
        const fresh = await api.messages(conversationId, lastId);
        if (fresh.length) {
          setMessages((m) => [...m.filter((x) => x.id > 0), ...fresh]);
          onReply();
        }
      } catch {
        /* next poll will retry */
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [conversationId, lastId, sending, onReply]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, sending]);

  async function send(message: string) {
    const clean = message.trim();
    if (!clean || sending) return;
    setText("");
    setSending(true);
    const temp: Bubble = { id: -Date.now(), role: "user", text: clean, created_at: new Date().toISOString(), pending: true };
    setMessages((m) => [...m, temp]);
    try {
      const res = await api.sendChat(conversationId, clean);
      setConversationId(res.conversation_id);
      const fresh = await api.messages(res.conversation_id, lastId);
      setMessages((m) => [...m.filter((x) => x.id > 0), ...fresh]);
      onReply();
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : "Something went wrong. Please try again.";
      if (e instanceof ApiError && e.status === 409) {
        toast(msg);
        setMessages((m) => m.filter((x) => x.id !== temp.id));
      } else {
        setMessages((m) => [
          ...m.map((x) => (x.id === temp.id ? { ...x, pending: false, failed: true } : x)),
          { id: -Date.now() - 1, role: "assistant", text: msg, created_at: new Date().toISOString() },
        ]);
      }
    } finally {
      setSending(false);
    }
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void send(text);
    }
  }

  return (
    <Card className="flex h-[calc(100dvh-9rem)] min-h-[28rem] flex-col gap-0 overflow-hidden py-0">
      <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-6" aria-live="polite">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-start justify-end gap-3 pb-2">
            <p className="text-lg font-medium">Hi {name}, what needs to change?</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              Tell Shiftly in your own words. It checks your company's rules and the free seats, then makes the change or tells you why not.
            </p>
            <div className="flex flex-wrap gap-2 pt-1">
              {SUGGESTIONS.map((s) => (
                <Button key={s} variant="outline" size="sm" className="rounded-full" onClick={() => void send(s)}>
                  {s}
                </Button>
              ))}
            </div>
          </div>
        )}
        <ol className="space-y-3">
          {messages.map((m) => (
            <li key={m.id} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={
                  "max-w-[85%] whitespace-pre-line rounded-2xl px-4 py-2.5 text-sm leading-relaxed " +
                  (m.role === "user"
                    ? "rounded-br-md bg-primary text-primary-foreground"
                    : "rounded-bl-md border bg-card")
                }
              >
                {m.text}
                <span className={"mt-1 block text-[10px] " + (m.role === "user" ? "text-primary-foreground/60" : "text-muted-foreground")}>
                  {m.failed ? "Not sent" : m.pending ? "Sending…" : clockTime(m.created_at)}
                </span>
              </div>
            </li>
          ))}
          {sending && (
            <li className="flex justify-start">
              <div className="rounded-2xl rounded-bl-md border bg-card px-4 py-3" aria-label="Checking">
                <span className="flex gap-1">
                  {[0, 150, 300].map((d) => (
                    <span key={d} className="size-1.5 animate-pulse rounded-full bg-muted-foreground" style={{ animationDelay: `${d}ms` }} />
                  ))}
                </span>
              </div>
            </li>
          )}
        </ol>
        <div ref={bottom} />
      </div>
      <form
        className="flex items-end gap-2 border-t bg-card p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send(text);
        }}
      >
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKey}
          placeholder="e.g. Move my drop to 8:30 tonight"
          aria-label="Message"
          rows={1}
          maxLength={1000}
          className="max-h-32 min-h-10 resize-none"
        />
        <Button type="submit" size="icon" disabled={sending || !text.trim()} aria-label="Send">
          <ArrowUp />
        </Button>
      </form>
    </Card>
  );
}
