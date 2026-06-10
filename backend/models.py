from pydantic import BaseModel
from typing import Optional, List


class Director(BaseModel):
    name: str
    din: str = ""
    designation: str = ""
    linkedin_url: str = ""


class Contact(BaseModel):
    name: str = ""
    email: str = ""
    position: str = ""
    linkedin_url: str = ""


class PastInstrument(BaseModel):
    isin: str = ""
    security_name: str = ""
    instrument_type: str = ""
    face_value: str = ""
    issue_date: str = ""
    maturity_date: str = ""
    coupon_rate: str = ""
    credit_rating: str = ""
    rating_agency: str = ""
    status: str = ""
    amount_crores: str = ""


class OfficeLocation(BaseModel):
    place_id: str = ""
    name: str = ""
    address: str = ""
    lat: float = 0.0
    lng: float = 0.0
    location_type: str = "Branch"  # HQ, Branch, Regional Office


class Company(BaseModel):
    id: str
    name: str
    address: str
    lat: float
    lng: float
    website: str = ""
    phone: str = ""
    cin: str = ""
    incorporation_date: str = ""
    entity_type: str = ""
    sub_type: str = ""           # e.g. Scheduled UCB, Small Finance Bank, MFI, HFC
    layer: str = ""              # NBFC scale layer: Base / Middle / Upper
    discovery_source: str = ""   # rbi_registry / osm / google_places
    directors: List[Director] = []
    contacts: List[Contact] = []
    past_instruments: List[PastInstrument] = []
    office_locations: List[OfficeLocation] = []
    score: int = 0
    score_label: str = "Pending"
    why_quality_lead: List[str] = []
    pain_points: List[str] = []
    recommended_approach: str = ""


class SearchRequest(BaseModel):
    city: str
    industry: str = ""
    entity_type: str = "All"
    instrument_type: str = "All"


class SearchResponse(BaseModel):
    companies: List[Company]
    city_lat: float
    city_lng: float
    search_id: str
