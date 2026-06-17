"""
Renpho Connector - uses the unofficial renpho-api PyPI package.
https://pypi.org/project/renpho-api/
"""
import logging
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)


class RenphoConnector:
    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self._client = None
        self._scale_table = None

    async def login(self):
        """Login to Renpho and discover scales."""
        try:
            from renpho import RenphoClient
        except ImportError:
            raise RuntimeError(
                "renpho-api package not installed. "
                "Run: pip install renpho-api --break-system-packages"
            )

        self._client = RenphoClient(self.email, self.password)
        self._client.login()
        
        # Discover scales
        device_info = self._client.get_device_info()
        scales = device_info.get("scale", [])
        
        if not scales:
            raise RuntimeError("No scales found in your Renpho account.")
        
        self._scale_table = scales[0]
        logger.info(f"Connected to scale: {self._scale_table.get('tableName', 'unknown')}")

    async def get_all_measurements(self) -> List[dict]:
        """Download full measurement history."""
        if not self._client or not self._scale_table:
            raise RuntimeError("Not logged in. Call login() first.")
        
        total_count = self._scale_table.get("count", 1000)
        logger.info(f"Downloading {total_count} measurements...")
        
        raw = self._client.get_measurements(
            table_name=self._scale_table["tableName"],
            user_id=self._client.user_id,
            total_count=total_count,
        )
        
        return [self._normalize(m) for m in raw]

    async def get_measurements_since(self, since_date: Optional[str]) -> List[dict]:
        """Download only measurements newer than since_date (ISO string)."""
        all_measurements = await self.get_all_measurements()
        
        if not since_date:
            return all_measurements
        
        return [
            m for m in all_measurements
            if m.get("measured_at", "") > since_date
        ]

    def _normalize(self, raw: dict) -> dict:
        """Normalize a raw Renpho measurement to our schema."""
        measured_at = self._parse_date(raw)

        return {
            "id": str(raw.get("id", raw.get("timeStamp", raw.get("time_stamp", measured_at)))),
            "measured_at": measured_at,
            "weight_kg": self._safe_float(raw.get("weight")),
            "body_fat_pct": self._safe_float(raw.get("bodyfat")),
            "muscle_mass_kg": self._safe_float(raw.get("muscle")),
            "bone_mass_kg": self._safe_float(raw.get("bone")),
            "water_pct": self._safe_float(raw.get("water")),
            "bmi": self._safe_float(raw.get("bmi")),
            "visceral_fat": self._safe_int(raw.get("subfat")),
            "bmr": self._safe_int(raw.get("bmr")),
            "metabolic_age": self._safe_int(raw.get("bodyage")),
            "raw": raw
        }

    def _parse_date(self, raw: dict) -> str:
        """Parse Renpho date fields into ISO format."""
        for field in ["timeStamp", "time_stamp", "timestamp", "created_at", "date"]:
            val = raw.get(field)
            if val:
                if isinstance(val, (int, float)):
                    try:
                        # Handle both seconds and milliseconds epoch
                        ts = val / 1000 if val > 1e10 else val
                        return datetime.fromtimestamp(ts).isoformat()
                    except Exception:
                        pass
                elif isinstance(val, str):
                    # Try parsing various formats
                    for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                        try:
                            return datetime.strptime(val, fmt).isoformat()
                        except Exception:
                            pass
                    return val  # Return as-is if we can't parse
        
        return datetime.now().isoformat()

    def _safe_float(self, val) -> Optional[float]:
        if val is None:
            return None
        try:
            f = float(val)
            return f if f > 0 else None
        except (ValueError, TypeError):
            return None

    def _safe_int(self, val) -> Optional[int]:
        if val is None:
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
