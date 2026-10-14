const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function requestHeaders(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers || {});
  headers.set("Content-Type", "application/json");
  return headers;
}

export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = requestHeaders(path, init);
  const res = await fetch(`${API}${path}`, { ...init, headers, credentials: "include" });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw Object.assign(new Error(typeof data.detail === "string" ? data.detail : "Please check the form and try again."), { status: res.status });
  }
  return res.json();
}

export async function login(username: string, password: string, code = "") {
  const data = await apiFetch("/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password, code }),
  });
  window.localStorage.removeItem("logiq_token");
  return data;
}

export const apiUrl = API;
export const money = (paise: number) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(paise / 100);

export async function downloadLogs(path: string) {
  const response = await fetch(API + path, { headers: requestHeaders(path), credentials: "include" });
  if (!response.ok) throw new Error("Log export failed. Please check your session and filters.");
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a"); link.href = url; link.download = "logiq-events.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function displayTimezone() {
  return typeof window === "undefined" ? "UTC" : localStorage.getItem("logiq_timezone") || "UTC";
}
