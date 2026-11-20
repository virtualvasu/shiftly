import { getSession } from "./session";

export type Role = "employee" | "manager";
export type DemoGroup = {
  company_id: string;
  company_name: string;
  people: { employee_id: string; name: string; role: Role }[];
};
export type Me = { employee_id: string; name: string; role: Role; company_id: string; company_name: string };
export type Trip = {
  trip_date: string;
  direction: "pickup" | "drop";
  time: string;
  vehicle_no: string;
  route: string;
  pending_request: string | null;
};
export type Message = { id: number; role: "user" | "assistant"; text: string; created_at: string };
export type ChatResult = { conversation_id: string; reply: string; llm_calls: number; tools: string[] };
export type PendingRequest = {
  request_id: string;
  employee_name: string;
  direction: "pickup" | "drop";
  day: string;
  change_type: "reschedule" | "cancel";
  from: string;
  to: string | null;
  reason: string | null;
  status: string;
  created_at: string;
};
export type AuditRow = {
  id: number;
  actor_type: string;
  actor_id: string;
  action: string;
  request_id: string | null;
  created_at: string;
};
export type FaultSystem = "roster" | "cabs" | "booking";
export type Faults = Partial<Record<FaultSystem, { latency_s: number; fail: boolean }>>;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  const token = getSession()?.token;
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      method: init.method ?? "GET",
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    throw new ApiError(0, "Can't reach the cab desk right now. Check your connection and try again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.error ?? "Something went wrong. Please try again.");
  return data as T;
}

export const api = {
  demoUsers: () => request<DemoGroup[]>("/auth/demo-users"),
  signIn: (employee_id: string) =>
    request<{ token: string }>("/auth/token", { method: "POST", body: { employee_id } }),
  me: () => request<Me>("/me"),
  myRoster: () => request<Trip[]>("/me/roster"),
  sendChat: (conversation_id: string | null, message: string) =>
    request<ChatResult>("/chat", {
      method: "POST",
      body: conversation_id ? { conversation_id, message } : { message },
    }),
  messages: (conversation_id: string, after = 0) =>
    request<Message[]>(`/conversations/${conversation_id}/messages?after=${after}`),
  latestConversation: () => request<{ conversation_id: string | null }>("/conversations/latest"),
  pendingRequests: () => request<PendingRequest[]>("/manager/requests"),
  decide: (id: string, decision: "approve" | "reject", note?: string) =>
    request<{ status: string }>(`/manager/requests/${id}/decision`, {
      method: "POST",
      body: note ? { decision, note } : { decision },
    }),
  audit: () => request<AuditRow[]>("/manager/audit"),
  faults: () => request<Faults>("/dev/faults"),
  setFault: (system: FaultSystem, fault: { latency_s?: number; fail?: boolean }) =>
    request<Faults>("/dev/faults", { method: "POST", body: { system, ...fault } }),
  clearFaults: () => request<Faults>("/dev/faults", { method: "DELETE" }),
  resetData: () => request<{ status: string }>("/dev/reset", { method: "POST" }),
};
