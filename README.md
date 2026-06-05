# LeadRadar — B2B Lead Generation for Credit Rating Agencies

Find, score, and enrich company leads across Indian cities using Google Places, Hunter.io, Zauba Corp, and Claude AI.

---

## Quick Start (Local)

### 1. Clone & enter the project
```bash
git clone <your-repo-url>
cd lead-gen-tool
```

### 2. Create your `.env` file
```
GOOGLE_MAPS_API_KEY=your_key_here
GOOGLE_PLACES_API_KEY=your_key_here
HUNTER_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
SUPABASE_URL=your_url_here
SUPABASE_KEY=your_key_here
```

> **Note:** The app works without API keys — it falls back to mock data so you can test the UI immediately.

### 3. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 4. Build the frontend
```bash
cd frontend
npm install
npm run build
cd ..
```

### 5. Start the backend (serves the built frontend too)
```bash
uvicorn backend.main:app --reload --port 8000
```

Open http://localhost:8000

### 5b. (Alternative) Run frontend dev server separately
```bash
# Terminal 1
uvicorn backend.main:app --reload --port 8000

# Terminal 2
cd frontend
npm run dev   # runs on http://localhost:5173 with proxy to backend
```

Add `VITE_GOOGLE_MAPS_API_KEY=your_key` to `frontend/.env` for the map to render.

---

## Where to Get Each API Key

| Key | Where to get it | Free tier |
|-----|----------------|-----------|
| `GOOGLE_MAPS_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com) → Enable Maps JavaScript API | $200/mo credit |
| `GOOGLE_PLACES_API_KEY` | Same project → Enable Places API | Included above |
| `HUNTER_API_KEY` | [hunter.io/api-keys](https://hunter.io/api-keys) | 25 searches/mo free |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | Pay-as-you-go |
| `SUPABASE_URL` + `SUPABASE_KEY` | [supabase.com](https://supabase.com) → New project → Settings → API | Free tier |

> **Google Maps note:** Use the same key for both `GOOGLE_MAPS_API_KEY` and `GOOGLE_PLACES_API_KEY`, or create separate restricted keys. Restrict the Maps JS key to your domain (HTTP referrers) and the Places key to your server IP.

---

## Supabase Setup (Optional — for persistent search history)

Run this SQL in the Supabase SQL editor to create the searches table:

```sql
create table searches (
  id text primary key,
  city text,
  industry text,
  results jsonb,
  created_at timestamp with time zone default now()
);
```

---

## Deploy to Railway (Under 10 Minutes)

**Prerequisites:** [Railway CLI](https://docs.railway.app/develop/cli) or use the web dashboard.

### Step 1 — Push your code to GitHub
```bash
git init && git add . && git commit -m "Initial commit"
git remote add origin <your-github-repo>
git push -u origin main
```

### Step 2 — Create Railway project
1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub Repo
2. Select your repository
3. Railway auto-detects `railway.toml` and `Procfile`

### Step 3 — Add environment variables
In Railway dashboard → your service → Variables, add all keys from your `.env` file.

Railway will build and deploy automatically. Your app will be live at `https://your-app.up.railway.app` within ~3 minutes.

> **Tip:** Add `VITE_GOOGLE_MAPS_API_KEY` as a Railway variable too, then run `npm run build` as part of your Railway build command if you want the map key injected at build time. Or add it as a static env var in `railway.toml`.

---

## Architecture

```
Browser  →  React (Vite)  →  FastAPI  →  Google Places API   (company discovery)
                                      →  Hunter.io API        (email contacts)
                                      →  Zauba Corp scraper   (MCA/CIN/directors)
                                      →  Claude AI            (lead scoring)
                                      →  Supabase             (persistence + CSV export)
```

## Folder Structure

```
lead-gen-tool/
├── backend/
│   ├── main.py              # FastAPI app, routes
│   ├── config.py            # Env var loader
│   ├── models.py            # Pydantic models
│   ├── database.py          # Supabase client
│   └── pipeline/
│       ├── discovery.py     # Google Places search
│       ├── enricher.py      # Hunter.io email lookup
│       ├── mca_scraper.py   # Zauba Corp scraper
│       └── scorer.py        # Claude AI scoring
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   └── components/
│   │       ├── SearchBar.jsx
│   │       ├── MapView.jsx
│   │       ├── Sidebar.jsx
│   │       ├── CompanyCard.jsx
│   │       └── LeadScore.jsx
│   ├── package.json
│   └── index.html
├── .env
├── requirements.txt
├── railway.toml
└── Procfile
```
