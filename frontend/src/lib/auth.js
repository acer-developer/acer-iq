// Supabase sign-in. The URL and publishable (anon) key are public by design -
// they ship in every Supabase web app - and row level security is what keeps
// one BD's pipeline away from another's (supabase_schema.sql).
//
// Without them (local dev, or a deploy missing the env vars) the app runs with
// no login at all, and the backend then serves its shared SQLite pipeline. The
// header says so, so a missing var can never pass for "logged in".
import { createClient } from "@supabase/supabase-js";
import { apiUrl } from "./api.js";

const url = import.meta.env.VITE_SUPABASE_URL ?? "";
const key = import.meta.env.VITE_SUPABASE_ANON_KEY ?? "";

export const authConfigured = Boolean(url && key);
export const supabase = authConfigured ? createClient(url, key) : null;

export async function authHeaders() {
  if (!supabase) return {};
  const { data } = await supabase.auth.getSession();
  const token = data.session?.access_token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// fetch() against our API with the signed-in user's token attached.
export async function apiFetch(path, opts = {}) {
  const headers = { ...(opts.headers ?? {}), ...(await authHeaders()) };
  return fetch(apiUrl(path), { ...opts, headers });
}

// Printable briefs are HTML pages behind auth. A plain <a href> cannot carry
// the Authorization header, so fetch the page and open it from a blob URL.
export async function openAuthed(path) {
  const win = window.open("", "_blank");
  try {
    const res = await apiFetch(path);
    const html = await res.text();
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const blob = new Blob([html], { type: "text/html" });
    if (win) win.location.href = URL.createObjectURL(blob);
  } catch (e) {
    if (win) win.document.body.innerText = `Could not load the brief: ${e.message}`;
  }
}
