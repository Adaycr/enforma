"""
EPD – Estimador de Peso Dinámico en Tiempo Real
Estimates current body weight between scale measurements using
accumulated Garmin KPIs (calories, respiration, stress, intensity).
Self-calibrates via gradient descent when a verified fasting weight arrives.
"""
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_EVAP_BASE         = 0.040   # kg/h  — baseline water loss at rest (40 g/h)
_KCAL_FACTOR       = 7700.0  # kcal per kg oxidised tissue
_LEARN_RATE        = 0.08    # gradient descent step for evaporation_rate
_LEARN_RATE_KCAL   = 0.05    # gradient descent step for kcal_factor (conservative)
_EVAP_MIN          = 0.010   # kg/h  — physiological floor
_EVAP_MAX          = 0.120   # kg/h  — physiological ceiling
_KCAL_FACTOR_MIN   = 5000.0  # kcal/kg — floor (very lean tissue)
_KCAL_FACTOR_MAX   = 11000.0 # kcal/kg — ceiling
_FAT_LOSS_MIN_KG   = 0.050   # minimum detectable fat loss; below this bioimpedance noise dominates


class EPDEstimator:
    """Stateful estimator; persist params via get_params() / load from DB."""

    def __init__(self, params: dict):
        self.evaporation_rate = float(params.get("evaporation_rate_kg_h", _EVAP_BASE))
        self.kcal_factor      = float(params.get("kcal_factor",           _KCAL_FACTOR))
        self.fitness_factor   = float(params.get("fitness_factor",        1.0))

    # ── Public API ──────────────────────────────────────────────────────────────

    def estimate(
        self,
        last_weight_kg: float,
        last_weight_at: str,
        garmin_summary: dict,
    ) -> dict:
        """
        Compute current estimated weight and per-component losses.

        garmin_summary must contain aggregated totals since last_weight_at:
          calories_bmr_total, calories_active_total,
          intensity_minutes_total, avg_stress, avg_respiration
        """
        now       = datetime.now()
        last_dt   = datetime.fromisoformat(last_weight_at)
        elapsed_h = max(0.0, (now - last_dt).total_seconds() / 3600)

        cal_reposo  = float(garmin_summary.get("calories_bmr_total",      0) or 0)
        cal_activas = float(garmin_summary.get("calories_active_total",   0) or 0)
        intens_min  = float(garmin_summary.get("intensity_minutes_total", 0) or 0)
        avg_stress  = float(garmin_summary.get("avg_stress")        or 25.0)
        avg_resp    = float(garmin_summary.get("avg_respiration")    or 15.0)
        avg_hr      = float(garmin_summary.get("avg_hr")             or 65.0)
        days        = float(garmin_summary.get("days_with_data", 0)  or 1)

        # Dynamic evaporation multipliers
        intensity_f = 1.0 + min(intens_min / 60.0, 3.0) * 0.20
        stress_f    = 1.0 + min(avg_stress, 100.0) / 100.0 * 0.10
        resp_f      = 1.0 + max(0.0, (avg_resp - 12.0) / 30.0) * 0.05
        # Heart rate factor: elevated HR → more sweat; normalised to 65 bpm resting baseline
        hr_f        = 1.0 + min(max(0.0, (avg_hr - 65.0) / 65.0), 0.20)

        tasa = self.evaporation_rate * intensity_f * stress_f * resp_f * hr_f * self.fitness_factor

        # Garmin stores DAILY totals (since midnight), not since the weigh-in moment.
        # BMR is a stable background rate → spread over 24h.
        # Active calories are exercise events → attribute them directly to the elapsed period
        # (they happened after the weigh-in, since we query from the weigh-in date).
        bmr_rate_per_h  = cal_reposo  / (days * 24.0) if cal_reposo  > 0 else 1500.0 / 24.0
        kcal_since_ref  = (bmr_rate_per_h * elapsed_h) + cal_activas
        avg_kcal_per_h  = bmr_rate_per_h + (cal_activas / elapsed_h if elapsed_h > 0 else 0)

        delta_metabolica = kcal_since_ref / self.kcal_factor
        delta_agua       = elapsed_h * tasa
        total_lost       = delta_metabolica + delta_agua
        estimated        = last_weight_kg - total_lost

        # Rate for frontend live ticker (BMR rate + current evaporation; active kcal already counted)
        loss_rate_kg_h = (bmr_rate_per_h / self.kcal_factor) + tasa

        return {
            "estimated_weight":    round(max(estimated, last_weight_kg * 0.85), 3),
            "last_weight_kg":      last_weight_kg,
            "last_weight_at":      last_weight_at,
            "computed_at":         now.isoformat(),
            "elapsed_hours":       round(elapsed_h, 3),
            "calories_burned":     int(kcal_since_ref),
            "delta_metabolica_kg": round(delta_metabolica, 4),
            "delta_agua_kg":       round(delta_agua, 4),
            "total_lost_kg":       round(total_lost, 4),
            "loss_rate_kg_h":      round(loss_rate_kg_h, 6),
            "tasa_evaporacion":    round(tasa, 5),
            "intensity_factor":    round(intensity_f, 3),
            "stress_factor":       round(stress_f, 3),
            "hr_factor":           round(hr_f, 3),
            "avg_hr":              round(avg_hr, 1),
        }

    def calibrate(
        self,
        scale_weight: float,
        estimated_weight: float,
        elapsed_hours: float,
        garmin_summary: dict,
        fat_pct_ref: Optional[float] = None,
        fat_pct_new: Optional[float] = None,
        ref_weight_kg: Optional[float] = None,
    ) -> dict:
        """
        Adjust evaporation_rate, kcal_factor, and fitness_factor via gradient descent.
        Called only when user confirms fasting (no food/drink since last measurement).

        fat_pct_ref / fat_pct_new / ref_weight_kg: Renpho bioimpedance data at both
        weighings. When present, separates metabolic loss from water loss so each
        parameter is calibrated against its own signal instead of the total error.
        """
        old_evap   = self.evaporation_rate
        old_kcal   = self.kcal_factor

        # ── Reconstruct calories burned (needed for kcal_factor calibration) ──
        cal_reposo  = float(garmin_summary.get("calories_bmr_total",    0) or 0)
        cal_activas = float(garmin_summary.get("calories_active_total", 0) or 0)
        days        = float(garmin_summary.get("days_with_data",        0) or 1)
        bmr_rate    = cal_reposo / (days * 24.0) if cal_reposo > 0 else 1500.0 / 24.0
        kcal_since_ref = (bmr_rate * elapsed_hours) + cal_activas

        # ── kcal_factor calibration via Renpho body fat ───────────────────────
        fat_lost_kg       = None
        kcal_factor_used  = False
        if (fat_pct_ref is not None and fat_pct_new is not None
                and ref_weight_kg is not None and kcal_since_ref > 0):
            fat_mass_ref = ref_weight_kg  * fat_pct_ref / 100.0
            fat_mass_new = scale_weight   * fat_pct_new / 100.0
            fat_lost_kg  = fat_mass_ref - fat_mass_new

            if fat_lost_kg > _FAT_LOSS_MIN_KG:
                kcal_implied = kcal_since_ref / fat_lost_kg
                step = _LEARN_RATE_KCAL * (kcal_implied - self.kcal_factor)
                self.kcal_factor = max(_KCAL_FACTOR_MIN, min(_KCAL_FACTOR_MAX,
                                       self.kcal_factor + step))
                kcal_factor_used = True

        # ── evaporation_rate calibration ──────────────────────────────────────
        # Total error: positive → model overestimated loss (scale heavier than expected)
        #              negative → model underestimated loss (scale lighter than expected)
        total_error = estimated_weight - scale_weight  # >0 → over-predicted loss → lower rate

        if elapsed_hours > 0.5:
            if kcal_factor_used and fat_lost_kg is not None:
                # Remove the metabolic component from the error so evap_rate is
                # calibrated against water loss only.
                delta_metabolica_model = kcal_since_ref / old_kcal
                delta_metabolica_actual = fat_lost_kg
                metabolic_correction = delta_metabolica_actual - delta_metabolica_model
                water_error = total_error - metabolic_correction
            else:
                water_error = total_error

            step = _LEARN_RATE * (water_error / elapsed_hours)
            self.evaporation_rate = max(_EVAP_MIN, min(_EVAP_MAX,
                                        self.evaporation_rate - step))

        # ── fitness_factor via resting respiration ────────────────────────────
        avg_resp = garmin_summary.get("avg_respiration")
        if avg_resp is not None:
            if avg_resp < 13:
                self.fitness_factor = max(0.70, self.fitness_factor * 0.997)
            elif avg_resp > 18:
                self.fitness_factor = min(1.30, self.fitness_factor * 1.003)

        logger.info(
            f"EPD calibrate: error={-total_error:+.3f} kg  "
            f"evap {old_evap:.4f}→{self.evaporation_rate:.4f} kg/h  "
            f"kcal_factor {old_kcal:.0f}→{self.kcal_factor:.0f}"
            + (f"  fat_lost={fat_lost_kg:.3f} kg" if fat_lost_kg is not None else "")
        )

        return {
            "evaporation_rate_kg_h": round(self.evaporation_rate, 5),
            "kcal_factor":           round(self.kcal_factor, 1),
            "fitness_factor":        round(self.fitness_factor, 4),
            "error_kg":              round(-total_error, 4),
            "evaporation_before":    round(old_evap, 5),
            "kcal_factor_before":    round(old_kcal, 1),
            "fat_lost_kg":           round(fat_lost_kg, 4) if fat_lost_kg is not None else None,
        }

    def get_params(self) -> dict:
        return {
            "evaporation_rate_kg_h": round(self.evaporation_rate, 5),
            "kcal_factor":           self.kcal_factor,
            "fitness_factor":        round(self.fitness_factor, 4),
        }
