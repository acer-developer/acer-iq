// In development: empty string → Vite proxy → localhost:8000
// In production (Vercel): set VITE_API_URL to your Railway URL
export const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");
export const apiUrl = (path) => `${API_BASE}${path}`;
