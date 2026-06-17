"""
Garmin Connect Connector - fetches daily fitness KPIs and intraday samples.
Uses the unofficial garminconnect PyPI package.
"""
import logging
import time
from datetime import datetime, timedelta, date as date_type
from typing import List, Optional, Dict, Any

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

    # ── Public API ────────────────────────────────────────────────────────────

    def get_stats_since(self, since_date: Optional[str]) -> Dict[str, Any]:
        """
        Fetch daily stats AND intraday samples from since_date to today.

        Returns a structured dict:
        {
            "daily":          [...],  # daily BMR/active totals (kept for BMR normalisation)
            "activities":     [...],  # individual workouts with exact timestamps
            "hr_samples":     [...],  # {timestamp, bpm}
            "stress_samples": [...],  # {timestamp, stress_level}
            "resp_samples":   [...],  # {timestamp, breaths_per_min}
            "body_battery":   [...],  # {timestamp, level}
        }
        """
        if not self._client:
            raise RuntimeError("Not logged in. Call login() first.")

        if since_date:
            start = datetime.fromisoformat(since_date).date()
        else:
            start = date_type.today() - timedelta(days=90)

        end = date_type.today()
        total = max(1, (end - start).days + 1)

        result: Dict[str, Any] = {
            "daily":          [],
            "activities":     [],
            "hr_samples":     [],
            "stress_samples": [],
            "resp_samples":   [],
            "body_battery":   [],
        }

        current = start
        while current <= end:
            date_str = current.strftime("%Y-%m-%d")

            # Daily stats (BMR, active kcal totals — kept for BMR rate)
            try:
                stats = self._get_day_stats(date_str)
                if stats:
                    result["daily"].append(stats)
            except Exception as e:
                logger.warning(f"Garmin daily stats unavailable for {date_str}: {e}")
            time.sleep(0.35)

            # Intraday samples
            intraday = self._get_intraday_samples(date_str)
            result["hr_samples"].extend(intraday["hr"])
            result["stress_samples"].extend(intraday["stress"])
            result["resp_samples"].extend(intraday["resp"])
            result["body_battery"].extend(intraday["body_battery"])

            current += timedelta(days=1)

        # Activities: single range query is more efficient than per-day
        result["activities"] = self._get_activities_range(start, end)

        logger.info(
            f"Garmin: {len(result['daily'])}/{total} days · "
            f"{len(result['activities'])} activities · "
            f"{len(result['hr_samples'])} HR samples · "
            f"{len(result['stress_samples'])} stress samples · "
            f"{len(result['resp_samples'])} resp samples"
        )
        return result

    # ── Private helpers ───────────────────────────────────────────────────────

    def _get_day_stats(self, date_str: str) -> Optional[dict]:
        """Fetch all EPD-relevant KPIs for one calendar day."""
        try:
            daily = self._client.get_stats(date_str)
        except Exception as e:
            logger.warning(f"get_stats({date_str}): {e}")
            return None

        if not daily:
            return None

        avg_respiration = None
        try:
            resp = self._client.get_respiration_data(date_str)
            if resp:
                avg_respiration = (
                    resp.get("avgWakingRespirationValue")
                    or resp.get("avgRespirationValue")
                )
            time.sleep(0.35)
        except Exception:
            pass

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
                time.sleep(0.35)
            except Exception:
                pass

        high_s = daily.get("highlyActiveSeconds") or 0
        mod_s  = daily.get("activeSeconds") or 0
        intensity_minutes = int((high_s + mod_s) / 60)

        return {
            "date":              date_str,
            "calories_bmr":      self._safe_int(daily.get("bmrKilocalories")) or 0,
            "calories_active":   self._safe_int(daily.get("activeKilocalories")) or 0,
            "avg_stress":        self._safe_float(daily.get("averageStressLevel")),
            "avg_respiration":   self._safe_float(avg_respiration),
            "intensity_minutes": intensity_minutes,
            "resting_hr":        resting_hr,
            "raw_data":          daily,
        }

    def _get_intraday_samples(self, date_str: str) -> Dict[str, list]:
        """Fetch intraday HR, stress, respiration and body battery for one day."""
        out = {"hr": [], "stress": [], "resp": [], "body_battery": []}

        # Heart rate
        try:
            data = self._client.get_heart_rates(date_str)
            if data:
                for entry in (data.get("heartRateValues") or []):
                    if entry and len(entry) >= 2 and entry[0] and entry[1] and entry[1] > 0:
                        out["hr"].append({
                            "timestamp": datetime.fromtimestamp(entry[0] / 1000).isoformat(),
                            "bpm":       int(entry[1]),
                        })
            time.sleep(0.35)
        except Exception as e:
            logger.debug(f"HR samples unavailable for {date_str}: {e}")

        # Stress
        try:
            data = self._client.get_stress_data(date_str)
            if data:
                for entry in (data.get("stressValuesArray") or []):
                    if entry and len(entry) >= 2 and entry[0] and entry[1] is not None and entry[1] >= 0:
                        out["stress"].append({
                            "timestamp":   datetime.fromtimestamp(entry[0] / 1000).isoformat(),
                            "stress_level": int(entry[1]),
                        })
            time.sleep(0.35)
        except Exception as e:
            logger.debug(f"Stress samples unavailable for {date_str}: {e}")

        # Respiration
        try:
            data = self._client.get_respiration_data(date_str)
            if data:
                for entry in (data.get("respirationValuesArray") or []):
                    if entry and len(entry) >= 2 and entry[0] and entry[1] and entry[1] > 0:
                        out["resp"].append({
                            "timestamp":       datetime.fromtimestamp(entry[0] / 1000).isoformat(),
                            "breaths_per_min": float(entry[1]),
                        })
            time.sleep(0.35)
        except Exception as e:
            logger.debug(f"Respiration samples unavailable for {date_str}: {e}")

        # Body battery
        try:
            data = self._client.get_body_battery(date_str)
            # get_body_battery returns a list; take the first item
            if data and isinstance(data, list):
                data = data[0]
            if data:
                for entry in (data.get("bodyBatteryValuesArray") or []):
                    if entry and len(entry) >= 2 and entry[0] and entry[1] is not None:
                        out["body_battery"].append({
                            "timestamp": datetime.fromtimestamp(entry[0] / 1000).isoformat(),
                            "level":     float(entry[1]),
                        })
            time.sleep(0.35)
        except Exception as e:
            logger.debug(f"Body battery unavailable for {date_str}: {e}")

        return out

    def _get_activities_range(
        self, start: date_type, end: date_type
    ) -> List[dict]:
        """Fetch all activities in the date range in a single API call."""
        try:
            raw_list = self._client.get_activities_by_date(
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            )
            time.sleep(0.35)
        except Exception as e:
            logger.warning(f"Activities fetch failed: {e}")
            return []

        activities = []
        for raw in (raw_list or []):
            try:
                start_local = raw.get("startTimeLocal", "")
                if not start_local:
                    continue

                # Normalise "YYYY-MM-DD HH:MM:SS" → ISO
                try:
                    start_dt = datetime.strptime(start_local, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    start_dt = datetime.fromisoformat(start_local)

                duration_s = float(raw.get("duration") or 0)
                end_dt     = start_dt + timedelta(seconds=duration_s)

                activity_type = ""
                raw_type = raw.get("activityType") or {}
                if isinstance(raw_type, dict):
                    activity_type = raw_type.get("typeKey", "")

                activities.append({
                    "activity_id":      str(raw.get("activityId", "")),
                    "start_time":       start_dt.isoformat(),
                    "end_time":         end_dt.isoformat(),
                    "calories":         self._safe_int(raw.get("calories")),
                    "activity_type":    activity_type,
                    "duration_seconds": duration_s,
                    "distance_meters":  self._safe_float(raw.get("distance")),
                    "avg_hr":           self._safe_int(raw.get("averageHR")),
                })
            except Exception as e:
                logger.debug(f"Skipping activity: {e}")

        return activities

    # ── Type helpers ─────────────────────────────────────────────────────────

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
