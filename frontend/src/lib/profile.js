// Which interface is showing: Admin (everything, the default) or one BD's.
// A view, not a permission - sign-in and row level security decide what can
// be changed (backend/pipeline/pipeline_store.py). Remembered per browser.
import { createContext, useContext } from "react";

export const ADMIN = { id: "admin", name: "Admin" };
// Mirrors backend/data/bd_roster.json; /api/roster is the source of truth and
// replaces this once it loads, so a roster change needs no frontend deploy.
export const DEFAULT_BDS = [
  { id: "hema", name: "Hema", segment: "NBFC / HFC / MFI (incl. securitisation)" },
  { id: "avinash", name: "Avinash", segment: "Manufacturing & large corporates" },
  { id: "akash", name: "Akash", segment: "Infra, real estate, power & EPC" },
  { id: "udit", name: "Udit", segment: "SME & bank loan ratings, first-time issuers" },
];

const KEY = "acer-iq.profile";
export function loadProfileId() {
  try { return localStorage.getItem(KEY) || ADMIN.id; } catch { return ADMIN.id; }
}
export function saveProfileId(id) {
  try { localStorage.setItem(KEY, id); } catch { /* private mode: not remembered */ }
}

export const ProfileContext = createContext({ profile: ADMIN, bds: DEFAULT_BDS, isAdmin: true });
export const useProfile = () => useContext(ProfileContext);
