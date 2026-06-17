"""
Database - SQLite storage for measurements and connector config.
Credentials are stored AES-encrypted.
"""
import sqlite3
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class Database:
    def __init__(self, db_path: Path, crypto):
        self.db_path = db_path
        self.crypto = crypto
        self._init_db()

    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS connectors (
                    name TEXT PRIMARY KEY,
                    credentials_enc TEXT NOT NULL,
                    last_sync TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS renpho_measurements (
                    id TEXT PRIMARY KEY,
                    measured_at TEXT NOT NULL,
                    weight_kg REAL,
                    body_fat_pct REAL,
                    muscle_mass_kg REAL,
                    bone_mass_kg REAL,
                    water_pct REAL,
                    bmi REAL,
                    visceral_fat INTEGER,
                    bmr INTEGER,
                    metabolic_age INTEGER,
                    raw_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_renpho_date
                ON renpho_measurements(measured_at);

                CREATE TABLE IF NOT EXISTS garmin_daily_stats (
                    date TEXT PRIMARY KEY,
                    calories_bmr INTEGER DEFAULT 0,
                    calories_active INTEGER DEFAULT 0,
                    avg_stress REAL,
                    avg_respiration REAL,
                    intensity_minutes INTEGER DEFAULT 0,
                    resting_hr INTEGER,
                    raw_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS garmin_activities (
                    activity_id      TEXT PRIMARY KEY,
                    start_time       TEXT NOT NULL,
                    end_time         TEXT,
                    calories         INTEGER,
                    activity_type    TEXT,
                    duration_seconds REAL,
                    distance_meters  REAL,
                    avg_hr           INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_activity_start
                ON garmin_activities(start_time);

                CREATE TABLE IF NOT EXISTS garmin_hr_samples (
                    timestamp TEXT NOT NULL PRIMARY KEY,
                    bpm       INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_hr_ts
                ON garmin_hr_samples(timestamp);

                CREATE TABLE IF NOT EXISTS garmin_stress_samples (
                    timestamp    TEXT NOT NULL PRIMARY KEY,
                    stress_level INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_stress_ts
                ON garmin_stress_samples(timestamp);

                CREATE TABLE IF NOT EXISTS garmin_resp_samples (
                    timestamp       TEXT NOT NULL PRIMARY KEY,
                    breaths_per_min REAL
                );
                CREATE INDEX IF NOT EXISTS idx_resp_ts
                ON garmin_resp_samples(timestamp);

                CREATE TABLE IF NOT EXISTS garmin_body_battery (
                    timestamp TEXT NOT NULL PRIMARY KEY,
                    level     REAL
                );

                CREATE TABLE IF NOT EXISTS epd_parameters (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    evaporation_rate_kg_h REAL DEFAULT 0.040,
                    kcal_factor REAL DEFAULT 7700.0,
                    fitness_factor REAL DEFAULT 1.0,
                    last_ref_weight_kg REAL,
                    last_ref_weight_at TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS epd_calibration_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calibrated_at TEXT NOT NULL,
                    scale_weight REAL,
                    estimated_weight REAL,
                    error_kg REAL,
                    evaporation_before REAL,
                    evaporation_after REAL,
                    elapsed_hours REAL,
                    kcal_factor_before REAL,
                    kcal_factor_after REAL,
                    fat_lost_kg REAL
                );

                INSERT OR IGNORE INTO epd_parameters (id) VALUES (1);
            """)
            # Migrations: add columns introduced after initial schema
            existing = {row[1] for row in conn.execute(
                "PRAGMA table_info(epd_calibration_history)")}
            for col, definition in [
                ("kcal_factor_before", "REAL"),
                ("kcal_factor_after",  "REAL"),
                ("fat_lost_kg",        "REAL"),
            ]:
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE epd_calibration_history ADD COLUMN {col} {definition}"
                    )

    # ── Connectors ────────────────────────────────────────────────────────────

    def save_connector_credentials(self, name: str, credentials: dict):
        enc = self.crypto.encrypt_dict(credentials)
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO connectors (name, credentials_enc)
                VALUES (?, ?)
            """, (name, enc))

    def get_connector_credentials(self, name: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT credentials_enc FROM connectors WHERE name = ?", (name,)
            ).fetchone()
            if row:
                return self.crypto.decrypt_dict(row["credentials_enc"])
            return None

    def update_connector_sync(self, name: str, sync_time: str):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE connectors SET last_sync = ? WHERE name = ?",
                (sync_time, name)
            )

    def get_last_sync_date(self, name: str) -> Optional[str]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT last_sync FROM connectors WHERE name = ?", (name,)
            ).fetchone()
            return row["last_sync"] if row else None

    def get_all_connector_status(self) -> List[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT name, last_sync, created_at FROM connectors"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_connector(self, name: str):
        with self._get_conn() as conn:
            conn.execute("DELETE FROM connectors WHERE name = ?", (name,))
            if name == "renpho":
                conn.execute("DELETE FROM renpho_measurements")
            elif name == "garmin":
                conn.execute("DELETE FROM garmin_daily_stats")
                conn.execute("DELETE FROM garmin_activities")
                conn.execute("DELETE FROM garmin_hr_samples")
                conn.execute("DELETE FROM garmin_stress_samples")
                conn.execute("DELETE FROM garmin_resp_samples")
                conn.execute("DELETE FROM garmin_body_battery")

    # ── Renpho Measurements ───────────────────────────────────────────────────

    def save_renpho_measurements(self, measurements: List[dict]) -> int:
        """Save measurements, skipping duplicates. Returns count of new records."""
        count = 0
        with self._get_conn() as conn:
            for m in measurements:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO renpho_measurements 
                        (id, measured_at, weight_kg, body_fat_pct, muscle_mass_kg,
                         bone_mass_kg, water_pct, bmi, visceral_fat, bmr, 
                         metabolic_age, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        m.get("id"),
                        m.get("measured_at"),
                        m.get("weight_kg"),
                        m.get("body_fat_pct"),
                        m.get("muscle_mass_kg"),
                        m.get("bone_mass_kg"),
                        m.get("water_pct"),
                        m.get("bmi"),
                        m.get("visceral_fat"),
                        m.get("bmr"),
                        m.get("metabolic_age"),
                        json.dumps(m.get("raw", {}))
                    ))
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception as e:
                    logger.warning(f"Skipping measurement {m.get('id')}: {e}")
        return count

    def get_weight_kpi(self) -> Optional[dict]:
        """Returns current weight + delta vs previous different-day measurement."""
        with self._get_conn() as conn:
            current_row = conn.execute("""
                SELECT measured_at, weight_kg
                FROM renpho_measurements
                WHERE weight_kg IS NOT NULL
                ORDER BY measured_at DESC
                LIMIT 1
            """).fetchone()

            if not current_row:
                return None

            current = dict(current_row)
            today   = current["measured_at"][:10]

            # Compare against the most recent measurement from a different day
            previous_row = conn.execute("""
                SELECT measured_at, weight_kg
                FROM renpho_measurements
                WHERE weight_kg IS NOT NULL
                  AND measured_at < ?
                ORDER BY measured_at DESC
                LIMIT 1
            """, (today,)).fetchone()

            delta = None
            if previous_row and previous_row["weight_kg"]:
                delta = round(current["weight_kg"] - previous_row["weight_kg"], 2)

            return {
                "value":      round(current["weight_kg"], 1),
                "unit":       "kg",
                "measured_at": current["measured_at"],
                "delta":      delta,
                "delta_unit": "kg"
            }

    def get_body_fat_kpi(self) -> Optional[dict]:
        """Returns current body fat % + delta vs previous different-day measurement."""
        with self._get_conn() as conn:
            current_row = conn.execute("""
                SELECT measured_at, body_fat_pct
                FROM renpho_measurements
                WHERE body_fat_pct IS NOT NULL
                ORDER BY measured_at DESC
                LIMIT 1
            """).fetchone()

            if not current_row:
                return None

            current = dict(current_row)
            today   = current["measured_at"][:10]

            previous_row = conn.execute("""
                SELECT measured_at, body_fat_pct
                FROM renpho_measurements
                WHERE body_fat_pct IS NOT NULL
                  AND measured_at < ?
                ORDER BY measured_at DESC
                LIMIT 1
            """, (today,)).fetchone()

            delta = None
            if previous_row and previous_row["body_fat_pct"]:
                delta = round(current["body_fat_pct"] - previous_row["body_fat_pct"], 2)

            return {
                "value":      round(current["body_fat_pct"], 1),
                "unit":       "%",
                "measured_at": current["measured_at"],
                "delta":      delta,
                "delta_unit": "%"
            }

    def _period_filter(self, period: str) -> str:
        """Returns SQL date filter for the given period."""
        now = datetime.now()
        if period == "week":
            cutoff = (now - timedelta(weeks=1)).isoformat()
        elif period == "month":
            cutoff = (now - timedelta(days=30)).isoformat()
        elif period == "year":
            cutoff = (now - timedelta(days=365)).isoformat()
        else:  # all
            return ""
        return f"AND measured_at >= '{cutoff}'"

    def get_weight_history(self, period: str = "all") -> List[dict]:
        period_filter = self._period_filter(period)
        with self._get_conn() as conn:
            rows = conn.execute(f"""
                SELECT measured_at, weight_kg as value
                FROM renpho_measurements
                WHERE weight_kg IS NOT NULL
                {period_filter}
                ORDER BY measured_at ASC
            """).fetchall()
            return [{"date": r["measured_at"][:10], "value": round(r["value"], 2)} 
                    for r in rows]

    # ── Garmin Daily Stats ────────────────────────────────────────────────────

    def save_garmin_stats(self, stats: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for s in stats:
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO garmin_daily_stats
                        (date, calories_bmr, calories_active, avg_stress,
                         avg_respiration, intensity_minutes, resting_hr, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        s.get("date"),
                        s.get("calories_bmr", 0),
                        s.get("calories_active", 0),
                        s.get("avg_stress"),
                        s.get("avg_respiration"),
                        s.get("intensity_minutes", 0),
                        s.get("resting_hr"),
                        json.dumps(s.get("raw_data", {})),
                    ))
                    count += 1
                except Exception as e:
                    logger.warning(f"Skipping Garmin day {s.get('date')}: {e}")
        return count

    # ── Intraday save methods ─────────────────────────────────────────────────

    def save_garmin_activities(self, activities: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for a in activities:
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO garmin_activities
                        (activity_id, start_time, end_time, calories,
                         activity_type, duration_seconds, distance_meters, avg_hr)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        a.get("activity_id"),
                        a.get("start_time"),
                        a.get("end_time"),
                        a.get("calories"),
                        a.get("activity_type"),
                        a.get("duration_seconds"),
                        a.get("distance_meters"),
                        a.get("avg_hr"),
                    ))
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception as e:
                    logger.warning(f"Skipping activity {a.get('activity_id')}: {e}")
        return count

    def save_garmin_hr_samples(self, samples: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for s in samples:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO garmin_hr_samples (timestamp, bpm) VALUES (?, ?)",
                        (s.get("timestamp"), s.get("bpm")),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception:
                    pass
        return count

    def save_garmin_stress_samples(self, samples: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for s in samples:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO garmin_stress_samples (timestamp, stress_level) VALUES (?, ?)",
                        (s.get("timestamp"), s.get("stress_level")),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception:
                    pass
        return count

    def save_garmin_resp_samples(self, samples: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for s in samples:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO garmin_resp_samples (timestamp, breaths_per_min) VALUES (?, ?)",
                        (s.get("timestamp"), s.get("breaths_per_min")),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception:
                    pass
        return count

    def save_garmin_body_battery(self, samples: List[dict]) -> int:
        count = 0
        with self._get_conn() as conn:
            for s in samples:
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO garmin_body_battery (timestamp, level) VALUES (?, ?)",
                        (s.get("timestamp"), s.get("level")),
                    )
                    if conn.execute("SELECT changes()").fetchone()[0] > 0:
                        count += 1
                except Exception:
                    pass
        return count

    # ── EPD Garmin summary ────────────────────────────────────────────────────

    def get_garmin_intraday_summary_since(self, since_timestamp: Optional[str]) -> dict:
        """
        Precise EPD Garmin summary using intraday data when available.

        Active calories: summed from garmin_activities starting AFTER since_timestamp
          (exact: only exercise that happened after the weigh-in counts).
        Intensity: summed from activity durations after since_timestamp.
        HR, stress, respiration: averaged from intraday samples since since_timestamp
          (real-time averages, not day-level estimates).
        BMR: from daily stats (daily total normalised to rate in EPD).
        Falls back to daily averages for stress/respiration if intraday tables are empty.
        """
        with self._get_conn() as conn:
            ref_date = since_timestamp[:10] if since_timestamp else ""
            ts       = since_timestamp or ""

            # BMR from daily stats (reference day included for rate normalisation)
            bmr_row = conn.execute(f"""
                SELECT COALESCE(SUM(calories_bmr), 0) AS bmr_total,
                       COUNT(*) AS days
                FROM garmin_daily_stats
                WHERE date >= '{ref_date}'
            """).fetchone()

            # Active calories: only activities starting AFTER the weigh-in timestamp
            act_row = conn.execute("""
                SELECT COALESCE(SUM(calories), 0)          AS cal_active,
                       COALESCE(SUM(duration_seconds), 0)  AS dur_s
                FROM garmin_activities
                WHERE start_time > ? AND calories > 0
            """, (ts,)).fetchone()

            # Intraday HR average since weigh-in
            hr_row = conn.execute(
                "SELECT AVG(bpm) AS avg_hr FROM garmin_hr_samples WHERE timestamp > ?", (ts,)
            ).fetchone()

            # Intraday stress average (exclude -1 / invalid readings)
            stress_row = conn.execute("""
                SELECT AVG(stress_level) AS avg_stress
                FROM garmin_stress_samples
                WHERE timestamp > ? AND stress_level >= 0
            """, (ts,)).fetchone()

            # Intraday respiration average
            resp_row = conn.execute(
                "SELECT AVG(breaths_per_min) AS avg_resp FROM garmin_resp_samples WHERE timestamp > ?",
                (ts,)
            ).fetchone()

            # Fallback: daily averages if intraday tables are empty
            daily_fallback = conn.execute(f"""
                SELECT AVG(avg_stress) AS avg_stress, AVG(avg_respiration) AS avg_resp
                FROM garmin_daily_stats
                WHERE date >= '{ref_date}'
            """).fetchone()

            avg_hr    = hr_row["avg_hr"]    if hr_row    and hr_row["avg_hr"]    is not None else None
            avg_stress = (
                stress_row["avg_stress"]
                if stress_row and stress_row["avg_stress"] is not None
                else (daily_fallback["avg_stress"] if daily_fallback else None)
            )
            avg_resp = (
                resp_row["avg_resp"]
                if resp_row and resp_row["avg_resp"] is not None
                else (daily_fallback["avg_resp"] if daily_fallback else None)
            )

            intraday_available = (
                (hr_row     and hr_row["avg_hr"]           is not None) or
                (stress_row and stress_row["avg_stress"]   is not None) or
                (resp_row   and resp_row["avg_resp"]       is not None)
            )

            return {
                "calories_bmr_total":      int(bmr_row["bmr_total"])     if bmr_row else 0,
                "calories_active_total":   int(act_row["cal_active"])    if act_row else 0,
                "intensity_minutes_total": int((act_row["dur_s"] or 0) / 60) if act_row else 0,
                "avg_stress":              avg_stress,
                "avg_respiration":         avg_resp,
                "avg_hr":                  avg_hr,
                "days_with_data":          int(bmr_row["days"])          if bmr_row else 0,
                "intraday_available":      bool(intraday_available),
            }

    def get_garmin_summary_since(self, since_date: Optional[str]) -> dict:
        """Delegates to get_garmin_intraday_summary_since for unified EPD summaries."""
        return self.get_garmin_intraday_summary_since(since_date)

    def get_garmin_rhr_trend(self, days: int = 30) -> List[Optional[int]]:
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT resting_hr FROM garmin_daily_stats
                WHERE resting_hr IS NOT NULL
                ORDER BY date DESC LIMIT ?
            """, (days,)).fetchall()
            return [r["resting_hr"] for r in rows]

    # ── Tasa Metabólica Fitness ───────────────────────────────────────────────

    def _fitness_rate(self, calories_bmr: int, calories_active: int,
                      resting_hr: Optional[int], rhr_baseline: Optional[int]) -> float:
        """kcal/h adjusted by FCR fitness factor."""
        base = (calories_bmr + calories_active) / 24.0
        if resting_hr and rhr_baseline and resting_hr > 0:
            factor = max(0.85, min(1.50, rhr_baseline / resting_hr))
        else:
            factor = 1.0
        return base * factor

    def get_metabolic_rate_kpi(self) -> Optional[dict]:
        with self._get_conn() as conn:
            # All-time max resting HR = worst-fitness baseline
            rhr_row = conn.execute("""
                SELECT MAX(resting_hr) AS rhr_max FROM garmin_daily_stats
                WHERE resting_hr IS NOT NULL
            """).fetchone()
            rhr_baseline = rhr_row["rhr_max"] if rhr_row else None

            # Last 2 complete days (exclude today — Garmin accumulates BMR throughout the day)
            rows = conn.execute("""
                SELECT date, calories_bmr, calories_active, resting_hr
                FROM garmin_daily_stats
                WHERE calories_bmr > 0 AND date < date('now')
                ORDER BY date DESC LIMIT 2
            """).fetchall()

            if not rows:
                return None

            cur = dict(rows[0])
            prev = dict(rows[1]) if len(rows) > 1 else None

            cur_rate  = self._fitness_rate(cur["calories_bmr"], cur["calories_active"],
                                           cur["resting_hr"], rhr_baseline)
            prev_rate = self._fitness_rate(prev["calories_bmr"], prev["calories_active"],
                                           prev["resting_hr"], rhr_baseline) if prev else None

            # All-time maximum of the indicator (complete days only)
            all_rows = conn.execute("""
                SELECT calories_bmr, calories_active, resting_hr
                FROM garmin_daily_stats WHERE calories_bmr > 0 AND date < date('now')
            """).fetchall()
            historical_max = max(
                (self._fitness_rate(r["calories_bmr"], r["calories_active"],
                                    r["resting_hr"], rhr_baseline) for r in all_rows),
                default=cur_rate
            )

            delta = round(cur_rate - prev_rate, 1) if prev_rate else None
            return {
                "value":          round(cur_rate, 1),
                "unit":           "kcal/h",
                "measured_at":    cur["date"],
                "delta":          delta,
                "delta_unit":     "kcal/h",
                "historical_max": round(historical_max, 1),
                "resting_hr":     cur["resting_hr"],
                "rhr_baseline":   rhr_baseline,
            }

    def get_metabolic_rate_history(self, period: str = "all") -> List[dict]:
        period_filter = self._period_filter(period).replace("measured_at", "date")
        with self._get_conn() as conn:
            rhr_row = conn.execute("""
                SELECT MAX(resting_hr) AS rhr_max FROM garmin_daily_stats
                WHERE resting_hr IS NOT NULL
            """).fetchone()
            rhr_baseline = rhr_row["rhr_max"] if rhr_row else None

            rows = conn.execute(f"""
                SELECT date, calories_bmr, calories_active, resting_hr
                FROM garmin_daily_stats
                WHERE calories_bmr > 0 AND date < date('now')
                {period_filter}
                ORDER BY date ASC
            """).fetchall()

            return [
                {"date": r["date"],
                 "value": round(self._fitness_rate(
                     r["calories_bmr"], r["calories_active"],
                     r["resting_hr"], rhr_baseline), 1)}
                for r in rows
            ]

    def get_kcal_factor_kpi(self) -> Optional[dict]:
        with self._get_conn() as conn:
            params = conn.execute(
                "SELECT kcal_factor, updated_at FROM epd_parameters WHERE id = 1"
            ).fetchone()
            if not params or not params["kcal_factor"]:
                return None
            rows = conn.execute("""
                SELECT kcal_factor_after FROM epd_calibration_history
                WHERE kcal_factor_after IS NOT NULL
                ORDER BY calibrated_at DESC LIMIT 2
            """).fetchall()
            current = round(params["kcal_factor"], 1)
            delta = None
            if len(rows) >= 2:
                delta = round(rows[0]["kcal_factor_after"] - rows[1]["kcal_factor_after"], 1)
            return {
                "value":       current,
                "measured_at": params["updated_at"],
                "delta":       delta,
            }

    def get_kcal_factor_history(self, period: str = "all") -> List[dict]:
        cutoff_sql = ""
        if period != "all":
            now = datetime.now()
            days = {"week": 7, "month": 30, "year": 365}.get(period, None)
            if days:
                cutoff = (now - timedelta(days=days)).isoformat()
                cutoff_sql = f"AND calibrated_at >= '{cutoff}'"
        with self._get_conn() as conn:
            rows = conn.execute(f"""
                SELECT calibrated_at, kcal_factor_after AS value
                FROM epd_calibration_history
                WHERE kcal_factor_after IS NOT NULL
                {cutoff_sql}
                ORDER BY calibrated_at ASC
            """).fetchall()
            return [{"date": r["calibrated_at"][:16].replace("T", " "), "value": round(r["value"], 1)}
                    for r in rows]

    # ── EPD Parameters ────────────────────────────────────────────────────────

    def get_epd_parameters(self) -> dict:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM epd_parameters WHERE id = 1").fetchone()
            return dict(row) if row else {}

    def save_epd_parameters(self, params: dict):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE epd_parameters SET
                    evaporation_rate_kg_h = ?,
                    kcal_factor           = ?,
                    fitness_factor        = ?,
                    updated_at            = ?
                WHERE id = 1
            """, (
                params.get("evaporation_rate_kg_h", 0.040),
                params.get("kcal_factor", 7700.0),
                params.get("fitness_factor", 1.0),
                datetime.now().isoformat(),
            ))

    def set_epd_reference_weight(self, weight_kg: float, measured_at: str):
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE epd_parameters SET
                    last_ref_weight_kg = ?,
                    last_ref_weight_at = ?,
                    updated_at         = ?
                WHERE id = 1
            """, (weight_kg, measured_at, datetime.now().isoformat()))

    def get_body_fat_at(self, timestamp: str) -> Optional[float]:
        """Return body_fat_pct from the Renpho measurement closest to (and not after) timestamp."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT body_fat_pct FROM renpho_measurements
                WHERE measured_at <= ? AND body_fat_pct IS NOT NULL
                ORDER BY measured_at DESC LIMIT 1
            """, (timestamp,)).fetchone()
            return row["body_fat_pct"] if row else None

    def save_epd_calibration(self, entry: dict):
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO epd_calibration_history
                (calibrated_at, scale_weight, estimated_weight, error_kg,
                 evaporation_before, evaporation_after, elapsed_hours,
                 kcal_factor_before, kcal_factor_after, fat_lost_kg)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.get("calibrated_at"),
                entry.get("scale_weight"),
                entry.get("estimated_weight"),
                entry.get("error_kg"),
                entry.get("evaporation_before"),
                entry.get("evaporation_after"),
                entry.get("elapsed_hours"),
                entry.get("kcal_factor_before"),
                entry.get("kcal_factor_after"),
                entry.get("fat_lost_kg"),
            ))

    def get_body_fat_history(self, period: str = "all") -> List[dict]:
        period_filter = self._period_filter(period)
        with self._get_conn() as conn:
            rows = conn.execute(f"""
                SELECT measured_at, body_fat_pct as value
                FROM renpho_measurements
                WHERE body_fat_pct IS NOT NULL
                {period_filter}
                ORDER BY measured_at ASC
            """).fetchall()
            return [{"date": r["measured_at"][:10], "value": round(r["value"], 2)} 
                    for r in rows]
