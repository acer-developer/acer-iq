"""
Lightweight India geo helpers for the registry: state from CIN / pincode,
city extraction from addresses, and city centroids for map pins.
"""

import re

# CIN chars 8-9 → state (ROC state code)
CIN_STATE = {
    "AN": "Andaman and Nicobar", "AP": "Andhra Pradesh", "AR": "Arunachal Pradesh",
    "AS": "Assam", "BR": "Bihar", "CH": "Chandigarh", "CT": "Chhattisgarh",
    "CG": "Chhattisgarh", "DD": "Daman and Diu", "DL": "Delhi", "DN": "Dadra and Nagar Haveli",
    "GA": "Goa", "GJ": "Gujarat", "HP": "Himachal Pradesh", "HR": "Haryana",
    "JH": "Jharkhand", "JK": "Jammu and Kashmir", "KA": "Karnataka", "KL": "Kerala",
    "LD": "Lakshadweep", "MH": "Maharashtra", "ML": "Meghalaya", "MN": "Manipur",
    "MP": "Madhya Pradesh", "MZ": "Mizoram", "NL": "Nagaland", "OR": "Odisha",
    "PB": "Punjab", "PY": "Puducherry", "RJ": "Rajasthan", "SK": "Sikkim",
    "TG": "Telangana", "TS": "Telangana", "TN": "Tamil Nadu", "TR": "Tripura",
    "UP": "Uttar Pradesh", "UR": "Uttarakhand", "UT": "Uttarakhand", "WB": "West Bengal",
}

# Pincode first-2-digits → state (approximate but reliable at state level)
PIN_STATE = {
    "11": "Delhi", "12": "Haryana", "13": "Haryana", "14": "Punjab", "15": "Punjab",
    "16": "Chandigarh", "17": "Himachal Pradesh", "18": "Jammu and Kashmir",
    "19": "Jammu and Kashmir", "20": "Uttar Pradesh", "21": "Uttar Pradesh",
    "22": "Uttar Pradesh", "23": "Uttar Pradesh", "24": "Uttarakhand",
    "25": "Uttar Pradesh", "26": "Uttar Pradesh", "27": "Uttar Pradesh",
    "28": "Uttar Pradesh", "30": "Rajasthan", "31": "Rajasthan", "32": "Rajasthan",
    "33": "Rajasthan", "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat",
    "38": "Gujarat", "39": "Gujarat", "40": "Maharashtra", "41": "Maharashtra",
    "42": "Maharashtra", "43": "Maharashtra", "44": "Maharashtra",
    "45": "Madhya Pradesh", "46": "Madhya Pradesh", "47": "Madhya Pradesh",
    "48": "Madhya Pradesh", "49": "Chhattisgarh", "50": "Telangana",
    "51": "Andhra Pradesh", "52": "Andhra Pradesh", "53": "Andhra Pradesh",
    "56": "Karnataka", "57": "Karnataka", "58": "Karnataka", "59": "Karnataka",
    "60": "Tamil Nadu", "61": "Tamil Nadu", "62": "Tamil Nadu", "63": "Tamil Nadu",
    "64": "Tamil Nadu", "67": "Kerala", "68": "Kerala", "69": "Kerala",
    "70": "West Bengal", "71": "West Bengal", "72": "West Bengal", "73": "West Bengal",
    "74": "West Bengal", "75": "Odisha", "76": "Odisha", "77": "Odisha",
    "78": "Assam", "79": "North East", "80": "Bihar", "81": "Jharkhand",
    "82": "Jharkhand", "83": "Jharkhand", "84": "Bihar", "85": "Bihar",
    "90": "APS", "403": "Goa",
}

# City centroids — superset of the map dict in pipeline/discovery.py
CITY_COORDS: dict[str, tuple[float, float]] = {
    "mumbai": (19.0760, 72.8777), "delhi": (28.6139, 77.2090),
    "new delhi": (28.6139, 77.2090), "bengaluru": (12.9716, 77.5946),
    "bangalore": (12.9716, 77.5946), "hyderabad": (17.3850, 78.4867),
    "chennai": (13.0827, 80.2707), "kolkata": (22.5726, 88.3639),
    "pune": (18.5204, 73.8567), "ahmedabad": (23.0225, 72.5714),
    "jaipur": (26.9124, 75.7873), "lucknow": (26.8467, 80.9462),
    "kanpur": (26.4499, 80.3319), "nagpur": (21.1458, 79.0882),
    "visakhapatnam": (17.6868, 83.2185), "indore": (22.7196, 75.8577),
    "thane": (19.2183, 72.9781), "bhopal": (23.2599, 77.4126),
    "patna": (25.6093, 85.1236), "vadodara": (22.3072, 73.1812),
    "ghaziabad": (28.6692, 77.4538), "ludhiana": (30.9010, 75.8573),
    "agra": (27.1767, 78.0081), "nashik": (19.9975, 73.7898),
    "varanasi": (25.3176, 82.9739), "meerut": (28.9845, 77.7064),
    "rajkot": (22.3039, 70.8022), "srinagar": (34.0837, 74.7973),
    "aurangabad": (19.8762, 75.3433), "dhanbad": (23.7957, 86.4304),
    "amritsar": (31.6340, 74.8723), "navi mumbai": (19.0330, 73.0297),
    "prayagraj": (25.4358, 81.8463), "allahabad": (25.4358, 81.8463),
    "howrah": (22.5958, 88.2636), "ranchi": (23.3441, 85.3096),
    "gwalior": (26.2183, 78.1828), "jabalpur": (23.1815, 79.9864),
    "coimbatore": (11.0168, 76.9558), "vijayawada": (16.5062, 80.6480),
    "jodhpur": (26.2389, 73.0243), "madurai": (9.9252, 78.1198),
    "raipur": (21.2514, 81.6296), "kota": (25.2138, 75.8648),
    "chandigarh": (30.7333, 76.7794), "guwahati": (26.1445, 91.7362),
    "solapur": (17.6599, 75.9064), "hubli": (15.3647, 75.1240),
    "mysuru": (12.2958, 76.6394), "mysore": (12.2958, 76.6394),
    "tiruchirappalli": (10.7905, 78.7047), "bareilly": (28.3670, 79.4304),
    "aligarh": (27.8974, 78.0880), "moradabad": (28.8389, 78.7768),
    "gorakhpur": (26.7606, 83.3732), "bikaner": (28.0229, 73.3119),
    "amravati": (20.9374, 77.7796), "noida": (28.5355, 77.3910),
    "jamshedpur": (22.8046, 86.2029), "bhilai": (21.2094, 81.3784),
    "cuttack": (20.4625, 85.8830), "kochi": (9.9312, 76.2673),
    "nellore": (14.4426, 79.9865), "bhavnagar": (21.7645, 72.1519),
    "dehradun": (30.3165, 78.0322), "durgapur": (23.5204, 87.3119),
    "asansol": (23.6739, 86.9524), "rourkela": (22.2604, 84.8536),
    "nanded": (19.1383, 77.3210), "kolhapur": (16.7050, 74.2433),
    "ajmer": (26.4499, 74.6399), "gulbarga": (17.3297, 76.8343),
    "latur": (18.4088, 76.5604), "mangaluru": (12.9141, 74.8560),
    "mangalore": (12.9141, 74.8560), "erode": (11.3410, 77.7172),
    "tiruppur": (11.1085, 77.3411), "shimla": (31.1048, 77.1734),
    "gangtok": (27.3389, 88.6065), "panaji": (15.4909, 73.8278),
    "imphal": (24.8170, 93.9368), "shillong": (25.5788, 91.8933),
    "puducherry": (11.9416, 79.8083), "surat": (21.1702, 72.8311),
    "gandhinagar": (23.2156, 72.6369), "thiruvananthapuram": (8.5241, 76.9366),
    "kozhikode": (11.2588, 75.7804), "thrissur": (10.5276, 76.2144),
    "salem": (11.6643, 78.1460), "tirunelveli": (8.7139, 77.7567),
    "vellore": (12.9165, 79.1325), "warangal": (17.9784, 79.5941),
    "guntur": (16.3067, 80.4365), "udaipur": (24.5854, 73.7125),
    "bhubaneswar": (20.2961, 85.8245), "siliguri": (26.7271, 88.3953),
    "jammu": (32.7266, 74.8570), "rohtak": (28.8955, 76.6066),
    "panipat": (29.3909, 76.9635), "mathura": (27.4924, 77.6737),
    "bilaspur": (22.0797, 82.1409), "sangli": (16.8524, 74.5815),
    "ujjain": (23.1765, 75.7885), "secunderabad": (17.4399, 78.4983),
    "bellary": (15.1394, 76.9214), "faridabad": (28.4089, 77.3178),
    "gurugram": (28.4595, 77.0266), "gurgaon": (28.4595, 77.0266),
    "jalandhar": (31.3260, 75.5762), "anand": (22.5645, 72.9289),
    "mehsana": (23.5880, 72.3693), "nadiad": (22.6916, 72.8634),
    "junagadh": (21.5222, 70.4579), "jamnagar": (22.4707, 70.0577),
    "satara": (17.6805, 74.0183), "ichalkaranji": (16.6915, 74.4605),
    "akola": (20.7002, 77.0082), "jalgaon": (21.0077, 75.5626),
    "dhule": (20.9042, 74.7749), "ahmednagar": (19.0948, 74.7480),
    "ahilyanagar": (19.0948, 74.7480), "belgaum": (15.8497, 74.4977),
    "belagavi": (15.8497, 74.4977), "davangere": (14.4644, 75.9218),
    "shimoga": (13.9299, 75.5681), "tumkur": (13.3379, 77.1173),
    "karad": (17.2900, 74.1843), "baramati": (18.1514, 74.5775),
    "vasai": (19.4700, 72.8000), "virar": (19.4559, 72.8118),
    "kalyan": (19.2403, 73.1305), "dombivli": (19.2183, 73.0864),
    "pimpri": (18.6298, 73.7997), "pimpri-chinchwad": (18.6298, 73.7997),
}

_PIN_RE = re.compile(r"\b([1-9]\d{5})\b")


def state_from_cin(cin: str) -> str:
    if cin and len(cin) >= 9:
        return CIN_STATE.get(cin[7:9].upper(), "")
    return ""


def state_from_pin(pincode: str) -> str:
    if pincode and len(pincode) == 6:
        return PIN_STATE.get(pincode[:2], "")
    return ""


def extract_pin(address: str) -> str:
    m = _PIN_RE.search(address or "")
    return m.group(1) if m else ""


def extract_city(address: str, fallback: str = "") -> str:
    """Best-effort: find a known city name inside the address string."""
    a = (address or "").lower()
    best = ""
    for city in CITY_COORDS:
        if city in a and len(city) > len(best):
            best = city
    return best.title() if best else fallback


def city_coords(city: str) -> tuple[float, float] | None:
    return CITY_COORDS.get((city or "").strip().lower())


def jitter(lat: float, lng: float, key: str) -> tuple[float, float]:
    """Deterministic small offset so HQ pins in the same city don't stack."""
    import zlib
    h = zlib.crc32(key.encode("utf-8", "ignore"))
    dlat = ((h % 1000) / 1000 - 0.5) * 0.06
    dlng = (((h // 1000) % 1000) / 1000 - 0.5) * 0.06
    return lat + dlat, lng + dlng
