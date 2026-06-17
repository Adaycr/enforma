"""
Garmin Connect Connector - fetches daily fitness KPIs for the EPD algorithm.
Uses the unofficial garminconnect PyPI package.
"""
import logging
import time
from datetime import datetime, timedelta, date as date_type
from typing import List, Optional

logger = logging.getLogger(__name__)


class GarminConnector:
    def __init__(self, email: str, password: str, tokenstore: Optional[str] = None):
        self.email = email
        self.password = password
        self.tokenstore = tokenstore
        self._client = None

    def login(self) -> Optional[str]:
        """Login and return serialised token string for caching (v0.3.x API)."""
        try:
            from garminconnect import Garmin
        except ImportError:
            raise RuntimeError(
                "garminconnect package not installed. "
                "Run: pip install garminconnect --break-system-packages"
            )

        self._client = Garmin(self.email, self.password)

        if self.tokenstore:
            try:
                mfa, _ = self._client.login(tokenstore=self.tokenstore)
                if mfa is None:
                    logger.info("Garmin: logged in via cached token")
                    return self._dump_token()
            except Exception as e:
                logger.info(f"Garmin token refresh failed ({e}), re-authenticating")

        mfa, _ = self._client.login()
        if mfa:
            raise RuntimeError(
                "Tu cuenta Garmin tiene MFA activado. "
                "Desactívalo temporalmente en connect.garmin.com o introduce el código MFA."
            )
        logger.info(f"Garmin: authenticated as {self.email}")
        return self._dump_token()

    def _dump_token(self) -> Optional[str]:
        try:
            return self._client.client.dumps()
        except Exception:
            return None

    def get_stats_since(self, since_date: Optional[str]) -> List[dict]:
        """Fetch daily stats from since_date (ISO) to today."""
        if not self._client:
            raise RuntimeError("Not logged in. Call login() first.")

        if since_date:
            start = datetime.fromisoformat(since_date).date()
        else:
            start = date_type.today() - timedelta(days=90)

        end = date_type.today()
        total = max(1, (end - start).days + 1)
        results = []
        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")
            try:
                stats = self._get_day_stats(date_str)
                if stats:
                    results.append(stats)
            except Exception as e:
                logger.warning(f"Garmin stats unavailable for {date_str}: {e}")
            current += timedelta(days=1)
            # Respect Garmin rate limits: pause every request
            time.sleep(0.35)

        logger.info(f"Garmin: downloaded {len(results)}/{total} days")
        return results

    def _get_day_stats(self, date_str: str) -> Optional[dict]:
        """Fetch all EPD-relevant KPIs for one calendar day."""
        try:
            daily = self._client.get_stats(date_str)
        except Exception as e:
            logger.warning(f"get_stats({date_str}): {e}")
            return None

        if not daily:
            return None

        # Respiration rate
        avg_respiration = None
        try:
            resp = self._client.get_respiration_data(date_str)
            if resp:
                avg_respiration = (
                    resp.get("avgWakingRespirationValue")
                    or resp.get("avgRespirationValue")
                )
        except Exception:
            pass

        # Resting heart rate (may be in daily stats directly)
        resting_hr = self._safe_int(daily.get("restingHeartRate"))
        if resting_hr is None:
            try:
                rhr_data = self._client.get_rhr_day(date_str)
                if rhr_data:
                    metrics = (
                        rhr_data.get("allMetrics", {})
                        .get("metricsMap", {})
                        .get("WELLNESS_RESTING_HEART_RATE", [])
                    )
                    if metrics:
                        resting_hr = self._safe_int(metrics[0].get("value"))
            except Exception:
                pass

        # Intensity minutes: sum vigorous + moderate seconds → minutes
        high_s = daily.get("highlyActiveSeconds") or 0
        mod_s = daily.get("activeSeconds") or 0
        intensity_minutes = int((high_s + mod_s) / 60)

        return {
            "date": date_str,
            "calories_bmr": self._safe_int(daily.get("bmrKilocalories")) or 0,
            "calories_active": self._safe_int(daily.get("activeKilocalories")) or 0,
            "avg_stress": self._safe_float(daily.get("averageStressLevel")),
            "avg_respiration": self._safe_float(avg_respiration),
            "intensity_minutes": intensity_minutes,
            "resting_hr": resting_hr,
            "raw_data": daily,
        }

    @staticmethod
    def _safe_float(val) -> Optional[float]:
        if val is None or val == -1:
            return None
        try:
            f = float(val)
            return f if f >= 0 else None
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_int(val) -> Optional[int]:
        if val is None or val == -1:
            return None
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
