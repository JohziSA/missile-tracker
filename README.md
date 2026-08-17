
# Missile & Splashdown Tracker

Near-real-time web application that monitors public launch data, reported missile-related events, and splashdowns, displays them on an interactive map, and supports locking onto any object for closer tracking.

## Architecture

- **Backend** (`backend/src/main.py`): FastAPI + WebSockets  
  - Polls Launch Library 2 (public free API) every 60 seconds  
  - Adds clearly-marked demo anomaly / missile / splashdown objects so the UI always has interesting live targets  
  - Maintains object state and broadcasts updates over WebSocket  
  - Supports lock/unlock messages from clients  

- **Frontend** (`frontend/index.html`): Single-page MapLibre app  
  - Live WebSocket connection  
  - Interactive map with color-coded markers (missile / splashdown / launch / anomaly)  
  - Object list with filters (All / Missiles / Splashdowns / Launches / Abnormalities)  
  - **Lock** button: map flies to object and continuously follows it while locked  
  - Detail panel while locked  

## Running

### Backend
```bash
cd backend
pip install -r requirements.txt
cd src
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
python -m http.server 5173
```
Then open http://localhost:5173

(Backend must be running on port 8000.)

## Important Limitations (Truthful)

- Real-time tracking of operational military missiles is **not** publicly available.  
- This system uses only public sources (Launch Library 2) + simulated demonstration anomalies.  
- Positions for most objects are launch-pad locations or approximate reported areas.  
- No classified sensors, no illegal access, no weapon-construction information.

## Next Possible Extensions

- Additional public news / GDELT keyword streams for reported events  
- Trajectory arcs when public telemetry exists  
- Historical replay  
- User-configurable data sources  
- React + MapLibre rewrite for richer component model  

Built as the foundation for a larger monitoring project.
