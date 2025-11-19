"""
Database Schemas for Atomo10

Each Pydantic model corresponds to a MongoDB collection whose name is the lowercase
of the class name.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class Stop(BaseModel):
    id: Optional[str] = Field(None, description="Client-side identifier")
    name: str
    lat: Optional[float] = None
    lng: Optional[float] = None
    travel_minutes_from_prev: int = Field(0, ge=0, description="Minutes from previous stop")


class Line(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = Field("#2563eb", description="HEX color for UI")
    stops: List[Stop] = Field(default_factory=list)
    # Departure times from the first stop in HH:MM (24h)
    schedules: List[str] = Field(default_factory=list)
    locale: Optional[str] = Field("it", description="Default language for labels")


# Keep example schemas below if needed by other tools
class User(BaseModel):
    name: str
    email: str
    address: str
    age: Optional[int] = Field(None, ge=0, le=120)
    is_active: bool = True

class Product(BaseModel):
    title: str
    description: Optional[str] = None
    price: float = Field(..., ge=0)
    category: str
    in_stock: bool = True
