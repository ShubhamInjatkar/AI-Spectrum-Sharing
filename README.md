# AI-Driven Spectrum Sharing

A demo that simulates dense wireless channel usage and shows how a lightweight AI allocation layer can improve efficiency, reduce interference, and increase throughput.

## Stack

- Frontend: React + Vite + Tailwind CSS
- Backend: FastAPI
- Visualization: Chart.js
- ML: heuristic mock model for channel prediction and allocation

## Project Layout

```text
backend/
  app/
    main.py
  requirements.txt
frontend/
  src/
    App.jsx
    index.css
    main.jsx
  index.html
  package.json
  postcss.config.js
  tailwind.config.js
  vite.config.js
```

## Run Locally

If `npm` or `node` says "not recognized", install Node.js first and reopen your terminal.

```powershell
cd frontend
npm install
npm run dev
```

The frontend runs at [http://127.0.0.1:5173](http://127.0.0.1:5173).

### Single-URL Backend Serving

If you want easier phone testing, build the frontend once and let FastAPI serve it from the same backend URL:

```powershell
cd frontend
npm install
npm run build
cd ..
uvicorn backend.app.main:app --host 0.0.0.0 --reload
```

Then use:

```text
http://<your-backend-host>:8000/
```

The collector remains available at:

```text
http://<your-backend-host>:8000/collector/
```

### One-Command Network Links

If you want the terminal to print clickable dashboard and collector links for all detected interfaces, use:

```powershell
python tools/serve_backend.py
```

That command:

- prints `http://127.0.0.1:8000/`
- prints every detected local network URL like `http://192.168.x.x:8000/`
- prints the matching collector URL for each host
- starts the backend on `0.0.0.0:8000`

You can also filter which interfaces or IPs to show:

```powershell
python tools/serve_backend.py --interfaces Wi-Fi
python tools/serve_backend.py --hosts 192.168.29.109 192.168.11.1
python tools/serve_backend.py --print-only
```

### Backend

The FastAPI backend powers the simulation and AI allocation endpoints used by the dashboard:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload
```

### Real Device Collector

For phones and laptops, open the backend collector page on the device:

```text
http://<your-backend-host>:8000/collector/
```

That page measures live latency with fetch timing, estimates throughput with a probe download, and publishes telemetry to:

```text
POST /api/network/devices
```

You can also run the optional zero-dependency Python collector on laptops:

```powershell
python tools/device_collector.py --base-url http://127.0.0.1:8000
```

## Notes

- The frontend now uses a standard Vite project structure for easier development and hackathon iteration.
- The UI includes the core hackathon experience: controls, metrics, channel cards, AI allocation output, charts, presets, and a live insights feed.
- Live Network Mode uses real device telemetry only and keeps its logic separate from the simulation flow.

## Troubleshooting

- If the frontend fails with `npm` not found, install Node.js and reopen PowerShell.
- If the dashboard cannot load live data, make sure the FastAPI backend is running on port `8000`.
