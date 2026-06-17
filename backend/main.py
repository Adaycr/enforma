"""
EnForma - Backend API
Serves the frontend and provides REST endpoints for data access.
"""
import asyncio
import os
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .crypto import CryptoManager
from .db.database import Database
from .connectors.renpho import RenphoConnector
from .connectors.garmin import GarminConnector
from .epd import EPDEstimator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="EnForma API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

crypto = CryptoManager(DATA_DIR / ".key")
db = Database(DATA_DIR / "dashboard.db", crypto)

# Mount static files
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "assets")), name="static")


# ── Models ────────────────────────────────────────────────────────────────────

class RenphoCredentials(BaseModel):
    email: str
    password: str


class GarminCredentials(BaseModel):
    email: str
    password: str


class EPDWeightEvent(BaseModel):
    fasting: bool


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/api/status")
async def get_status():
    """Returns app status: whether connectors are configured and last sync dates."""
    connectors = db.get_all_connector_status()
    return {
        "configured": len(connectors) > 0,
        "connectors": connectors
    }


@app.post("/api/connectors/renpho/setup")
async def setup_renpho(creds: RenphoCredentials):
    """First-time setup: validate credentials, download full history, save encrypted."""
    try:
        connector = RenphoConnector(creds.email, creds.password)
        await connector.login()
        
        # Download full history
        logger.info("Downloading full Renpho history...")
        measurements = await connector.get_all_measurements()
        
        # Save encrypted credentials
        db.save_connector_credentials("renpho", {
            "email": creds.email,
            "password": creds.password
        })
        
        # Save measurements
        count = db.save_renpho_measurements(measurements)
        db.update_connector_sync("renpho", datetime.now().isoformat())
        
        return {
            "success": True,
            "message": f"Connected successfully. Downloaded {count} measurements.",
            "count": count
        }
    except Exception as e:
        logger.error(f"Renpho setup error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/sync")
async def sync_all():
    """Incremental sync for all configured connectors."""
    results = {}

    # Sync Renpho
    renpho_creds = db.get_connector_credentials("renpho")
    if renpho_creds:
        try:
            last_sync = db.get_last_sync_date("renpho")
            connector = RenphoConnector(renpho_creds["email"], renpho_creds["password"])
            await connector.login()
            measurements = await connector.get_measurements_since(last_sync)
            count = db.save_renpho_measurements(measurements)
            db.update_connector_sync("renpho", datetime.now().isoformat())
            results["renpho"] = {"success": True, "new_records": count,
                                  "new_weight": count > 0}
        except Exception as e:
            results["renpho"] = {"success": False, "error": str(e)}

    # Sync Garmin — always cover from last Renpho weight date to avoid gaps
    garmin_creds = db.get_connector_credentials("garmin")
    if garmin_creds:
        try:
            last_garmin_sync = db.get_last_sync_date("garmin")

            # Anchor: use the earlier of (last Garmin sync, last Renpho weight date)
            # so we never have a gap between the scale reading and Garmin data
            weight_kpi = db.get_weight_kpi()
            last_renpho_at = weight_kpi["measured_at"] if weight_kpi else None

            if last_garmin_sync and last_renpho_at:
                sync_from = min(last_garmin_sync[:10], last_renpho_at[:10])
            else:
                sync_from = last_renpho_at or last_garmin_sync

            connector = GarminConnector(
                garmin_creds["email"],
                garmin_creds["password"],
                tokenstore=garmin_creds.get("tokenstore"),
            )
            new_token = await asyncio.to_thread(connector.login)
            if new_token:
                garmin_creds["tokenstore"] = new_token
                db.save_connector_credentials("garmin", garmin_creds)

            garmin_data = await asyncio.to_thread(connector.get_stats_since, sync_from)
            daily_count    = db.save_garmin_stats(garmin_data.get("daily", []))
            activity_count = db.save_garmin_activities(garmin_data.get("activities", []))
            hr_count       = db.save_garmin_hr_samples(garmin_data.get("hr_samples", []))
            db.save_garmin_stress_samples(garmin_data.get("stress_samples", []))
            db.save_garmin_resp_samples(garmin_data.get("resp_samples", []))
            db.save_garmin_body_battery(garmin_data.get("body_battery", []))
            db.update_connector_sync("garmin", datetime.now().isoformat())
            results["garmin"] = {
                "success": True,
                "new_records": daily_count,
                "new_activities": activity_count,
                "new_hr_samples": hr_count,
            }
        except Exception as e:
            results["garmin"] = {"success": False, "error": str(e)}

    return {"results": results, "synced_at": datetime.now().isoformat()}


@app.get("/api/kpi/weight")
async def get_weight_kpi():
    """Returns current weight KPI with delta from previous measurement."""
    data = db.get_weight_kpi()
    if not data:
        raise HTTPException(status_code=404, detail="No weight data available")
    return data


@app.get("/api/kpi/body-fat")
async def get_body_fat_kpi():
    """Returns current body fat % KPI."""
    data = db.get_body_fat_kpi()
    if not data:
        raise HTTPException(status_code=404, detail="No body fat data available")
    return data


@app.get("/api/history/weight")
async def get_weight_history(period: str = "all"):
    """
    Returns weight history for chart.
    period: all | year | month | week
    """
    data = db.get_weight_history(period)
    return {"period": period, "data": data}


@app.get("/api/history/body-fat")
async def get_body_fat_history(period: str = "all"):
    """Returns body fat % history."""
    data = db.get_body_fat_history(period)
    return {"period": period, "data": data}


@app.get("/api/kpi/metabolic-rate")
async def get_metabolic_rate_kpi():
    data = db.get_metabolic_rate_kpi()
    if not data:
        raise HTTPException(status_code=404, detail="No Garmin data available")
    return data


@app.get("/api/history/metabolic-rate")
async def get_metabolic_rate_history(period: str = "all"):
    data = db.get_metabolic_rate_history(period)
    return {"period": period, "data": data}


@app.get("/api/kpi/kcal-factor")
async def get_kcal_factor_kpi():
    data = db.get_kcal_factor_kpi()
    if not data:
        raise HTTPException(status_code=404, detail="No EPD calibrations yet")
    return data


@app.get("/api/history/kcal-factor")
async def get_kcal_factor_history(period: str = "all"):
    data = db.get_kcal_factor_history(period)
    return {"period": period, "data": data}


@app.get("/api/connectors/renpho/status")
async def renpho_status():
    creds = db.get_connector_credentials("renpho")
    last_sync = db.get_last_sync_date("renpho")
    return {
        "configured": creds is not None,
        "email": creds["email"] if creds else None,
        "last_sync": last_sync
    }


@app.delete("/api/connectors/renpho")
async def delete_renpho():
    db.delete_connector("renpho")
    return {"success": True}


# ── Garmin Connect ─────────────────────────────────────────────────────────────

@app.post("/api/connectors/garmin/setup")
async def setup_garmin(creds: GarminCredentials):
    """First-time setup: validate credentials, download history, save encrypted."""
    try:
        connector = GarminConnector(creds.email, creds.password)
        new_token = await asyncio.to_thread(connector.login)

        logger.info("Downloading Garmin history (last 30 days)…")
        garmin_data = await asyncio.to_thread(connector.get_stats_since, None)

        db.save_connector_credentials("garmin", {
            "email": creds.email,
            "password": creds.password,
            "tokenstore": new_token,
        })
        daily_count    = db.save_garmin_stats(garmin_data.get("daily", []))
        activity_count = db.save_garmin_activities(garmin_data.get("activities", []))
        hr_count       = db.save_garmin_hr_samples(garmin_data.get("hr_samples", []))
        db.save_garmin_stress_samples(garmin_data.get("stress_samples", []))
        db.save_garmin_resp_samples(garmin_data.get("resp_samples", []))
        db.save_garmin_body_battery(garmin_data.get("body_battery", []))
        db.update_connector_sync("garmin", datetime.now().isoformat())

        return {
            "success": True,
            "message": (
                f"Garmin conectado. {daily_count} días · "
                f"{activity_count} actividades · {hr_count} muestras FC descargadas."
            ),
            "count": daily_count,
        }
    except Exception as e:
        logger.error(f"Garmin setup error: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/connectors/garmin/status")
async def garmin_status():
    creds = db.get_connector_credentials("garmin")
    last_sync = db.get_last_sync_date("garmin")
    return {
        "configured": creds is not None,
        "email": creds["email"] if creds else None,
        "last_sync": last_sync,
    }


@app.delete("/api/connectors/garmin")
async def delete_garmin():
    db.delete_connector("garmin")
    return {"success": True}



# ── EPD – Estimador de Peso Dinámico ──────────────────────────────────────────

@app.get("/api/kpi/epd")
async def get_epd():
    """Compute real-time dynamic weight estimate anchored to the latest Renpho weight."""
    epd_params = db.get_epd_parameters()
    if not epd_params:
        raise HTTPException(status_code=503, detail="EPD not initialised")

    # Always anchor to the latest Renpho measurement
    weight_kpi = db.get_weight_kpi()
    if not weight_kpi:
        raise HTTPException(status_code=404, detail="No reference weight available")

    latest_weight = weight_kpi["value"]
    latest_at     = weight_kpi["measured_at"]

    # Detect if latest Renpho weight is newer than the stored EPD reference
    stored_ref_at = epd_params.get("last_ref_weight_at")
    needs_processing = (not stored_ref_at) or (latest_at > stored_ref_at)

    # Use latest Renpho weight as the calculation anchor
    garmin_configured  = db.get_connector_credentials("garmin") is not None
    garmin_last_sync   = db.get_last_sync_date("garmin")
    garmin_summary     = db.get_garmin_intraday_summary_since(latest_at) if garmin_configured else {}

    # Data gap: Garmin hasn't been synced up to the reference weight date
    garmin_data_gap = (
        garmin_configured
        and (not garmin_last_sync or garmin_last_sync[:10] < latest_at[:10])
    )

    estimator = EPDEstimator(epd_params)
    result    = estimator.estimate(latest_weight, latest_at, garmin_summary)

    result["garmin_connected"]  = garmin_configured
    result["garmin_days"]       = garmin_summary.get("days_with_data", 0)
    result["garmin_last_sync"]  = garmin_last_sync
    result["needs_processing"]  = needs_processing
    result["garmin_data_gap"]   = garmin_data_gap
    return result


@app.post("/api/epd/process_weight")
async def epd_process_weight(event: EPDWeightEvent):
    """
    Called after a new Renpho measurement arrives.
    fasting=True → calibrate algorithm and update reference.
    fasting=False → update reference only (food weight contaminates calibration).
    """
    weight_kpi = db.get_weight_kpi()
    if not weight_kpi:
        raise HTTPException(status_code=404, detail="No scale measurement found")

    new_weight = weight_kpi["value"]
    new_at     = weight_kpi["measured_at"]

    if event.fasting:
        epd_params     = db.get_epd_parameters()
        ref_weight     = epd_params.get("last_ref_weight_kg")
        ref_at         = epd_params.get("last_ref_weight_at")
        garmin_summary = db.get_garmin_summary_since(ref_at) if ref_at else {}

        estimator = EPDEstimator(epd_params)

        if ref_weight and ref_at:
            current_estimate = estimator.estimate(ref_weight, ref_at, garmin_summary)
            elapsed_h        = current_estimate["elapsed_hours"]

            # Require at least 8 h between weigh-ins to calibrate.
            # With shorter intervals, measurement noise (clothing, hydration, meals)
            # dwarfs the real physiological signal and corrupts the parameters.
            if elapsed_h < 8.0:
                db.set_epd_reference_weight(new_weight, new_at)
                return {
                    "action":           "reference_updated_interval_too_short",
                    "elapsed_hours":    round(elapsed_h, 2),
                    "min_hours":        8.0,
                    "new_ref_weight_kg": new_weight,
                    "new_ref_at":       new_at,
                }

            fat_pct_ref = db.get_body_fat_at(ref_at)
            fat_pct_new = db.get_body_fat_at(new_at)

            updated = estimator.calibrate(
                new_weight,
                current_estimate["estimated_weight"],
                elapsed_h,
                garmin_summary,
                fat_pct_ref=fat_pct_ref,
                fat_pct_new=fat_pct_new,
                ref_weight_kg=ref_weight,
            )
            db.save_epd_parameters(estimator.get_params())
            db.save_epd_calibration({
                "calibrated_at":      datetime.now().isoformat(),
                "scale_weight":       new_weight,
                "estimated_weight":   current_estimate["estimated_weight"],
                "error_kg":           updated["error_kg"],
                "evaporation_before": updated["evaporation_before"],
                "evaporation_after":  updated["evaporation_rate_kg_h"],
                "elapsed_hours":      elapsed_h,
                "kcal_factor_before": updated["kcal_factor_before"],
                "kcal_factor_after":  updated["kcal_factor"],
                "fat_lost_kg":        updated["fat_lost_kg"],
            })
            action = "calibrated"
        else:
            action = "reference_set"

    else:
        action = "reference_updated_no_calibration"

    db.set_epd_reference_weight(new_weight, new_at)

    return {
        "action": action,
        "new_ref_weight_kg": new_weight,
        "new_ref_at": new_at,
    }
