import os
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import db, create_document, get_documents
from schemas import Line, Stop

app = FastAPI(title="Atomo10 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"name": "Atomo10", "status": "ok"}


# ---------------------------
# Utilities
# ---------------------------

def collection_name(model_cls) -> str:
    return model_cls.__name__.lower()


def _to_public(doc: Dict[str, Any]):
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    return d


# ---------------------------
# Lines CRUD
# ---------------------------

@app.post("/api/lines", response_model=dict)
def create_line(line: Line):
    inserted_id = create_document(collection_name(Line), line)
    return {"id": inserted_id}


@app.get("/api/lines", response_model=List[dict])
def list_lines():
    docs = get_documents(collection_name(Line))
    return [_to_public(doc) for doc in docs]


@app.get("/api/lines/{line_id}", response_model=dict)
def get_line(line_id: str):
    from bson import ObjectId
    try:
        doc = db[collection_name(Line)].find_one({"_id": ObjectId(line_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid line id")
    if not doc:
        raise HTTPException(status_code=404, detail="Line not found")
    return _to_public(doc)


class StopInput(BaseModel):
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    travel_minutes_from_prev: int = 0


@app.post("/api/lines/{line_id}/stops", response_model=dict)
def add_stop(line_id: str, stop: StopInput):
    from bson import ObjectId
    try:
        result = db[collection_name(Line)].update_one(
            {"_id": ObjectId(line_id)},
            {"$push": {"stops": stop.model_dump()}},
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid line id")
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Line not found")
    return {"ok": True}


class StopPatch(BaseModel):
    index: int
    name: Optional[str] = None
    lat: Optional[float] = None
    lng: Optional[float] = None
    travel_minutes_from_prev: Optional[int] = None


@app.patch("/api/lines/{line_id}/stops", response_model=dict)
def edit_stop(line_id: str, patch: StopPatch):
    from bson import ObjectId
    doc = db[collection_name(Line)].find_one({"_id": ObjectId(line_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Line not found")
    stops = doc.get("stops", [])
    if patch.index < 0 or patch.index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")
    stop = stops[patch.index]
    for k in ["name", "lat", "lng", "travel_minutes_from_prev"]:
        v = getattr(patch, k)
        if v is not None:
            stop[k] = v
    stops[patch.index] = stop
    db[collection_name(Line)].update_one({"_id": doc["_id"]}, {"$set": {"stops": stops}})
    return {"ok": True}


class StopDelete(BaseModel):
    index: int


@app.delete("/api/lines/{line_id}/stops", response_model=dict)
def delete_stop(line_id: str, payload: StopDelete):
    from bson import ObjectId
    doc = db[collection_name(Line)].find_one({"_id": ObjectId(line_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Line not found")
    stops = doc.get("stops", [])
    if payload.index < 0 or payload.index >= len(stops):
        raise HTTPException(status_code=400, detail="Invalid stop index")
    stops.pop(payload.index)
    db[collection_name(Line)].update_one({"_id": doc["_id"]}, {"$set": {"stops": stops}})
    return {"ok": True}


class SchedulePayload(BaseModel):
    schedules: List[str]


@app.put("/api/lines/{line_id}/schedules", response_model=dict)
def set_schedules(line_id: str, payload: SchedulePayload):
    from bson import ObjectId
    try:
        db[collection_name(Line)].update_one(
            {"_id": ObjectId(line_id)}, {"$set": {"schedules": payload.schedules}}
        )
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid line id")
    return {"ok": True}


# ---------------------------
# OCR Upload and Parsing
# ---------------------------

@app.post("/api/ocr/upload", response_model=Dict[str, Any])
async def upload_timetable(image: UploadFile = File(...)):
    """
    Accepts an image containing stop names and times. This starter implementation
    doesn't perform real OCR; instead, it returns a mocked parse to prove the
    pipeline. You can swap the parsing logic to use Tesseract later.
    """
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    # MOCKED result: sample stops & times
    stops = [
        {"name": "Capolinea", "travel_minutes_from_prev": 0},
        {"name": "Centro", "travel_minutes_from_prev": 5},
        {"name": "Stazione", "travel_minutes_from_prev": 4},
        {"name": "Ospedale", "travel_minutes_from_prev": 6},
    ]
    departures = ["07:30", "08:00", "08:30", "09:00"]

    return {"stops": stops, "schedules": departures}


# ---------------------------
# Helpers: ETA calculation
# ---------------------------

@app.get("/api/lines/{line_id}/eta", response_model=Dict[str, Any])
def compute_eta(line_id: str, from_stop_index: int = 0, now: Optional[str] = None):
    from bson import ObjectId
    doc = db[collection_name(Line)].find_one({"_id": ObjectId(line_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Line not found")

    stops = doc.get("stops", [])
    schedules = doc.get("schedules", [])
    if not schedules:
        return {"etas": []}

    if now:
        base_time = datetime.strptime(now, "%H:%M")
        base_time = datetime.combine(datetime.today(), base_time.time())
    else:
        base_time = datetime.now()

    etas = []
    cumulative = 0
    for i, s in enumerate(stops):
        if i == 0:
            cumulative = 0
        else:
            cumulative += int(s.get("travel_minutes_from_prev", 0))
        # For each schedule, compute arrival time at this stop
        arrivals = []
        for dep in schedules:
            hh, mm = map(int, dep.split(":"))
            departure_dt = base_time.replace(hour=hh, minute=mm, second=0, microsecond=0)
            arrival_dt = departure_dt + timedelta(minutes=cumulative)
            arrivals.append(arrival_dt.strftime("%H:%M"))
        etas.append({"stop": s.get("name"), "arrivals": arrivals})

    # Focus from requested stop index
    if 0 <= from_stop_index < len(etas):
        etas = etas[from_stop_index:]

    return {"etas": etas}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
