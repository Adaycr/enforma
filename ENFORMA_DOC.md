# EnForma — Documentación Completa

**Versión:** 1.2 · **Fecha:** junio 2026 · **Repositorio:** https://github.com/Adaycr/enforma

---

## Índice

1. [Visión general](#1-visión-general)
2. [Arquitectura del sistema](#2-arquitectura-del-sistema)
3. [Componentes funcionales](#3-componentes-funcionales)
4. [Algoritmo EPD](#4-algoritmo-epd-estimador-de-peso-dinámico)
5. [Modelo de datos](#5-modelo-de-datos)
6. [API REST](#6-api-rest)
7. [Seguridad y cifrado](#7-seguridad-y-cifrado)
8. [Instalación y arranque](#8-instalación-y-arranque)
9. [Flujo de uso diario](#9-flujo-de-uso-diario)
10. [Limitaciones y roadmap](#10-limitaciones-y-roadmap)

---

## 1. Visión general

EnForma es un dashboard de salud personal **completamente local** que combina datos de una báscula inteligente Renpho y un wearable Garmin para estimar el peso corporal en tiempo real entre pesajes, usando un algoritmo propio llamado **EPD (Estimador de Peso Dinámico)**.

### Propósito

Las básculas inteligentes solo registran el peso en el momento del pesaje. EnForma resuelve la pregunta *"¿cuánto peso ahora mismo, sin subirme a la báscula?"* aplicando fisiología básica: el cuerpo pierde masa de forma continua por metabolismo (oxidación de tejido) y evaporación (respiración + sudoración). Con los datos de actividad, frecuencia cardíaca, estrés y respiración de Garmin, el modelo afina estas tasas de pérdida en tiempo real.

### Principios de diseño

- **Local-first**: ningún dato sale del equipo. Todo se procesa y almacena en SQLite local.
- **Cifrado en reposo**: las credenciales se cifran con AES-256 derivado del ID de máquina.
- **Auto-calibración**: el algoritmo EPD aprende de cada pesaje nuevo y ajusta sus parámetros internos.
- **Datos intradiarios**: desde v1.1, usa muestras de FC, estrés y respiración a nivel de minuto (no promedios diarios) y calorías activas con timestamp exacto por actividad.
- **Sync automático**: cron job diario a las 7:00 que sincroniza los conectores si la app está corriendo.

---

## 2. Arquitectura del sistema

```
┌─────────────────────────────────────────────────────────┐
│                        FRONTEND                         │
│   index.html  (HTML + CSS + JS vanilla, sin framework)  │
│                                                         │
│   KPI cards · Gráficos Chart.js · EPD live ticker       │
│   Modales de configuración · Sync button                │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP / REST (localhost:8000)
┌────────────────────────▼────────────────────────────────┐
│                    BACKEND (FastAPI)                     │
│                                                         │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │   main.py   │  │   epd.py     │  │   crypto.py   │  │
│  │  API routes │  │ EPDEstimator │  │ CryptoManager │  │
│  └──────┬──────┘  └──────────────┘  └───────────────┘  │
│         │                                               │
│  ┌──────▼──────────────────────────────────────────┐   │
│  │              database.py (Database)             │   │
│  │   SQLite · cifrado · queries EPD / KPI          │   │
│  └──────┬──────────────────────────────────────────┘   │
│         │                                               │
│  ┌──────▼──────────┐    ┌──────────────────────────┐   │
│  │ renpho.py       │    │ garmin.py                │   │
│  │ RenphoConnector │    │ GarminConnector          │   │
│  └─────────────────┘    └──────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
                         │                  │
               API Renpho Health    API Garmin Connect
                (unofficial)          (unofficial)
```

### Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | Python 3.10+ · FastAPI · Uvicorn |
| Base de datos | SQLite 3 (fichero local) |
| Cifrado | `cryptography` (Fernet / AES-128-CBC + PBKDF2-SHA256) |
| Conector Renpho | `renpho-api` (PyPI) |
| Conector Garmin | `garminconnect` ≥ 0.2.22 (PyPI) |
| Frontend | HTML5 + CSS3 + JavaScript vanilla · Chart.js (CDN) |
| Servidor de archivos estáticos | FastAPI `StaticFiles` |

---

## 3. Componentes funcionales

### 3.1 Dashboard (frontend)

Single-page application sin framework. Se carga desde `/` y comunica con el backend exclusivamente vía `fetch()` a la API REST.

**Tarjetas KPI:**
- **EPD — Peso estimado ahora** (tarjeta destacada): muestra el peso calculado en tiempo real con live ticker que se actualiza cada 5 segundos sin llamadas al servidor.
- **Peso báscula (Renpho)**: último pesaje registrado + delta vs. último pesaje de un **día distinto** (no vs. el pesaje inmediatamente anterior del mismo día).
- **% Grasa corporal (Renpho)**: última medición de bioimpedancia + delta vs. día anterior.
- **Tasa metabólica fitness (Garmin)**: kcal/h ajustadas por FC de reposo como proxy de forma física.
- **Factor kcal/kg (EPD)**: parámetro interno del algoritmo; refleja cuántas kcal equivalen a 1 kg de tejido según calibración personal.

**Gráficos históricos**: al pulsar cualquier tarjeta KPI se abre un modal con gráfico de línea (Chart.js). Filtros: Todo / Año / Mes / Semana.

**Live ticker EPD**: JavaScript puro que interpola el peso localmente usando `estimated_weight`, `computed_at` y `loss_rate_kg_h` recibidos de la API. No hace polling; el peso se mueve suavemente en pantalla sin tráfico de red.

**Flujo de nuevo pesaje**: tras sincronizar, si el sistema detecta un peso nuevo sin procesar (`needs_processing: true`), muestra un diálogo que pregunta si el usuario estaba en ayunas. La respuesta determina si se ejecuta calibración del algoritmo.

### 3.2 Conector Renpho (`backend/connectors/renpho.py`)

Interfaz con la API no oficial de Renpho Health a través del paquete `renpho-api`.

**Métodos principales:**
- `login()`: autenticación y descubrimiento de básculas disponibles en la cuenta.
- `get_all_measurements()`: descarga el histórico completo paginando por `count` del dispositivo.
- `get_measurements_since(date)`: filtra mediciones posteriores a una fecha dada (para sync incremental).
- `_normalize(raw)`: mapea los campos del API de Renpho a la nomenclatura interna. Maneja múltiples formatos de timestamp (epoch en ms, epoch en s, strings `YYYY-MM-DD HH:MM:SS`).

**Datos recogidos por medición:**
`weight_kg`, `body_fat_pct`, `muscle_mass_kg`, `bone_mass_kg`, `water_pct`, `bmi`, `visceral_fat`, `bmr`, `metabolic_age` + `measured_at` (ISO local).

### 3.3 Conector Garmin (`backend/connectors/garmin.py`)

Interfaz con Garmin Connect a través del paquete `garminconnect`. Cachea el token OAuth para evitar autenticación repetida.

**`get_stats_since(since_date)`** — método principal. Retorna un dict estructurado con 6 claves:

```python
{
  "daily":          [],  # stats diarios (BMR)
  "activities":     [],  # actividades con timestamp exacto
  "hr_samples":     [],  # muestras de FC (~15 s de granularidad)
  "stress_samples": [],  # muestras de estrés (~3 min)
  "resp_samples":   [],  # muestras de respiración
  "body_battery":   [],  # nivel de batería corporal
}
```

**Datos diarios** (`_get_day_stats`): `calories_bmr`, `calories_active`, `avg_stress`, `avg_respiration`, `intensity_minutes`, `resting_hr`. Fuente: `get_stats()` + `get_respiration_data()` + `get_rhr_day()`.

**Actividades** (`_get_activities_range`): una llamada para todo el rango. Campos: `activity_id`, `start_time`, `end_time` (calculado = start + duration), `calories`, `activity_type`, `duration_seconds`, `distance_meters`, `avg_hr`.

**Muestras intradiarias** (`_get_intraday_samples`): por cada día, 4 llamadas separadas:
- `get_heart_rates()` → `heartRateValues[[ts_ms, bpm], ...]`
- `get_stress_data()` → `stressValuesArray[[ts_ms, nivel], ...]`
- `get_respiration_data()` → `respirationValuesArray[[ts_ms, rpm], ...]`
- `get_body_battery()` → `bodyBatteryValuesArray[[ts_ms, nivel, ...], ...]`

Todos los timestamps se convierten de epoch-ms UTC a ISO hora local via `datetime.fromtimestamp(ts_ms/1000).isoformat()`.

**Rate limiting**: sleep de 0.35 s entre cada llamada a la API de Garmin para no ser bloqueado.

---

## 4. Algoritmo EPD (Estimador de Peso Dinámico)

El EPD modela la pérdida de masa corporal entre pesajes como la suma de dos componentes fisiológicos:

```
Peso_estimado = Peso_referencia − ΔMetabólico − ΔAgua
```

### 4.1 Componente metabólico (ΔMetabólico)

Refleja la masa de tejido oxidado por el metabolismo:

```
ΔMetabólico = kcal_quemadas_desde_referencia / kcal_factor
```

- `kcal_quemadas = (BMR_rate_kg_h × elapsed_h) + kcal_actividades_post_pesaje`
- `BMR_rate_kg_h = calories_bmr_total / (days × 24)` — tasa horaria normalizada sobre los días con datos
- `kcal_actividades` = suma de calorías de actividades cuyo `start_time > timestamp_pesaje` (exacto al minuto gracias a los datos intradiarios)
- `kcal_factor` (default 7700 kcal/kg): parámetro auto-calibrable. Fisiológicamente representa las kcal que equivalen a 1 kg de tejido mixto. Rango válido: 5000–11000 kcal/kg.

### 4.2 Componente hídrico (ΔAgua)

Refleja la pérdida de agua por respiración y sudoración:

```
ΔAgua = evaporation_rate × intensity_f × stress_f × resp_f × hr_f × fitness_factor × elapsed_h
```

**Multiplicadores dinámicos** (calculados con datos reales del periodo post-pesaje):

| Factor | Fórmula | Fuente de datos |
|---|---|---|
| `intensity_f` | `1 + min(intens_min/60, 3) × 0.20` | Minutos de intensidad de actividades post-pesaje |
| `stress_f` | `1 + min(avg_stress, 100)/100 × 0.10` | Media de muestras de estrés desde el pesaje |
| `resp_f` | `1 + max(0, (avg_resp − 12)/30) × 0.05` | Media de muestras de respiración desde el pesaje |
| `hr_f` | `1 + min(max(0, (avg_hr − 65)/65), 0.20)` | Media de muestras de FC desde el pesaje |
| `fitness_factor` | Derivado de respiración en reposo (0.70–1.30) | Tendencia histórica de respiración nocturna |

**`evaporation_rate`** (default 0.040 kg/h): tasa base de pérdida hídrica en reposo. Rango fisiológico: 0.010–0.120 kg/h. Es el parámetro de mayor impacto y el primero que se auto-calibra.

### 4.3 Auto-calibración (gradient descent)

Se ejecuta cuando se cumplen **dos condiciones simultáneas**:
1. Han transcurrido **≥ 2 horas** desde el pesaje de referencia anterior.
2. El usuario confirma que **no ha ingerido comida ni bebida** desde el pesaje anterior.

Con menos de 2 horas, el sistema actualiza la referencia pero no calibra: la señal fisiológica (pérdida hídrica y metabólica real) es demasiado pequeña comparada con el ruido del sensor de la báscula.

> **Por qué 2 horas y no más**: en pruebas con múltiples pesajes intradía, umbrales de 8 horas o el filtro de "mismo día" resultaban demasiado restrictivos para usuarios que ayunan 6–7 horas. Con 2 horas hay suficiente señal y el usuario controla la validez mediante la confirmación de ayuno.

**Calibración de `evaporation_rate`:**
```
error_total = peso_estimado − peso_báscula   (>0 = sobreestimé pérdida → bajar tasa)
water_error = error_total − corrección_metabólica   (si hay datos de grasa)
Δevap = learn_rate × (water_error / elapsed_hours)
evaporation_rate = clamp(evaporation_rate − Δevap, 0.010, 0.120)
```

**Calibración de `kcal_factor`** (solo cuando hay dos pesajes en días distintos con datos de % grasa corporal de Renpho y pérdida de grasa ≥ 50 g):
```
fat_lost_kg = fat_mass_ref − fat_mass_new
kcal_factor_implied = kcal_quemadas / fat_lost_kg
Δkcal = learn_rate_kcal × (kcal_factor_implied − kcal_factor)
kcal_factor = clamp(kcal_factor + Δkcal, 5000, 11000)
```

> **Por qué el factor kcal/kg puede no actualizarse**: la calibración exige una diferencia de grasa medible ≥ 50 g entre los dos pesajes (umbral del ruido de la bioimpedancia). Si todos los pesajes son intradía (0–6 h de diferencia), la señal de grasa es menor que el ruido de la báscula y el factor no cambia. Necesita pesajes en días distintos con ayuno verificado para observar pérdida real de tejido adiposo.

**Calibración de `fitness_factor`** (basada en respiración de reposo nocturna):
- Respiración < 13 rpm → reduce 0.3%/día (atleta muy fit → menos pérdida por respiración)
- Respiración > 18 rpm → aumenta 0.3%/día

### 4.4 Parámetros del modelo

| Parámetro | Valor inicial | Rango | Se calibra |
|---|---|---|---|
| `evaporation_rate_kg_h` | 0.040 | 0.010–0.120 | Sí (cada pesaje en ayunas) |
| `kcal_factor` | 7700.0 | 5000–11000 | Sí (requiere datos de grasa) |
| `fitness_factor` | 1.0 | 0.70–1.30 | Sí (automático por respiración) |

### 4.5 Uso de datos intradiarios vs. fallback diario

Cuando las tablas intradiarias tienen datos (`intraday_available: True`):
- `avg_stress`, `avg_respiration`, `avg_hr` → promedios de muestras reales desde el timestamp exacto del pesaje
- `calories_active` → suma de calorías de actividades con `start_time > pesaje`

Cuando no hay datos intradiarios (primer uso o Garmin no sincronizado):
- `avg_stress`, `avg_respiration` → promedios de los resúmenes diarios de Garmin
- `avg_hr` → 65 bpm (valor por defecto)
- `calories_active` → 0 (conservador; nunca sobreatribuye pérdida)

---

## 5. Modelo de datos

Base de datos SQLite en `data/dashboard.db`.

### 5.1 Tablas de mediciones

#### `renpho_measurements`
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | TEXT PK | ID de Renpho o timestamp como fallback |
| `measured_at` | TEXT | ISO datetime local del pesaje |
| `weight_kg` | REAL | Peso en kg |
| `body_fat_pct` | REAL | % grasa corporal (bioimpedancia) |
| `muscle_mass_kg` | REAL | Masa muscular en kg |
| `bone_mass_kg` | REAL | Masa ósea en kg |
| `water_pct` | REAL | % agua corporal |
| `bmi` | REAL | Índice de masa corporal |
| `visceral_fat` | INTEGER | Grasa visceral (índice 1–20) |
| `bmr` | INTEGER | Metabolismo basal estimado (kcal/día) |
| `metabolic_age` | INTEGER | Edad metabólica estimada |
| `raw_data` | TEXT | JSON completo original de Renpho |

#### `garmin_daily_stats`
| Campo | Tipo | Descripción |
|---|---|---|
| `date` | TEXT PK | Fecha YYYY-MM-DD |
| `calories_bmr` | INTEGER | Kcal basales del día completo |
| `calories_active` | INTEGER | Kcal activas del día completo |
| `avg_stress` | REAL | Nivel de estrés medio (0–100) |
| `avg_respiration` | REAL | Frecuencia respiratoria media (rpm) |
| `intensity_minutes` | INTEGER | Minutos de intensidad (moderada + vigorosa) |
| `resting_hr` | INTEGER | FC de reposo del día (ppm) |
| `raw_data` | TEXT | JSON completo de `get_stats()` |

### 5.2 Tablas intradiarias (v1.1)

#### `garmin_activities`
| Campo | Tipo | Descripción |
|---|---|---|
| `activity_id` | TEXT PK | ID de Garmin Connect |
| `start_time` | TEXT | ISO datetime local de inicio |
| `end_time` | TEXT | ISO datetime local de fin (start + duration) |
| `calories` | INTEGER | Kcal quemadas en la actividad |
| `activity_type` | TEXT | Tipo: `running`, `cycling`, `strength_training`... |
| `duration_seconds` | REAL | Duración en segundos |
| `distance_meters` | REAL | Distancia en metros (si aplica) |
| `avg_hr` | INTEGER | FC media de la actividad |

Índice: `idx_activity_start` sobre `start_time`.

#### `garmin_hr_samples`
| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | TEXT PK | ISO datetime local de la muestra |
| `bpm` | INTEGER | Frecuencia cardíaca en ppm |

Granularidad: ~15 segundos. Índice: `idx_hr_ts`.

#### `garmin_stress_samples`
| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | TEXT PK | ISO datetime local |
| `stress_level` | INTEGER | Nivel de estrés Garmin (0–100; -1 = no disponible) |

Granularidad: ~3 minutos. Índice: `idx_stress_ts`.

#### `garmin_resp_samples`
| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | TEXT PK | ISO datetime local |
| `breaths_per_min` | REAL | Frecuencia respiratoria en rpm |

Índice: `idx_resp_ts`.

#### `garmin_body_battery`
| Campo | Tipo | Descripción |
|---|---|---|
| `timestamp` | TEXT PK | ISO datetime local |
| `level` | REAL | Nivel de batería corporal (0–100) |

### 5.3 Tablas del sistema

#### `connectors`
| Campo | Tipo | Descripción |
|---|---|---|
| `name` | TEXT PK | `renpho` o `garmin` |
| `credentials_enc` | TEXT | Credenciales cifradas con Fernet (JSON) |
| `last_sync` | TEXT | Timestamp ISO del último sync exitoso |

#### `epd_parameters`
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER PK | Siempre 1 (singleton) |
| `evaporation_rate_kg_h` | REAL | Tasa de evaporación actual |
| `kcal_factor` | REAL | Factor kcal/kg actual |
| `fitness_factor` | REAL | Factor de fitness actual |
| `last_ref_weight_kg` | REAL | Peso del último pesaje procesado |
| `last_ref_weight_at` | TEXT | Timestamp del último pesaje procesado |
| `updated_at` | TEXT | Última actualización de parámetros |

#### `epd_calibration_history`
| Campo | Tipo | Descripción |
|---|---|---|
| `id` | INTEGER PK | Autoincrement |
| `calibrated_at` | TEXT | Timestamp de la calibración |
| `scale_weight` | REAL | Peso real de la báscula |
| `estimated_weight` | REAL | Peso que predijo el EPD |
| `error_kg` | REAL | Error (scale − estimated); positivo = subestimé pérdida |
| `evaporation_before` | REAL | Tasa de evaporación antes de calibrar |
| `evaporation_after` | REAL | Tasa de evaporación después de calibrar |
| `elapsed_hours` | REAL | Horas transcurridas desde el pesaje anterior |
| `kcal_factor_before` | REAL | Factor kcal/kg antes |
| `kcal_factor_after` | REAL | Factor kcal/kg después |
| `fat_lost_kg` | REAL | Grasa perdida según bioimpedancia (si disponible) |

---

## 6. API REST

Base URL: `http://localhost:8000`

### 6.1 Estado y configuración

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Sirve el frontend (index.html) |
| `GET` | `/api/status` | Estado de conectores configurados y fechas de último sync |

### 6.2 Conectores

| Método | Ruta | Body | Descripción |
|---|---|---|---|
| `POST` | `/api/connectors/renpho/setup` | `{email, password}` | Primera configuración: valida credenciales y descarga histórico completo |
| `GET` | `/api/connectors/renpho/status` | — | Estado del conector Renpho |
| `DELETE` | `/api/connectors/renpho` | — | Elimina conector y todos sus datos |
| `POST` | `/api/connectors/garmin/setup` | `{email, password}` | Primera configuración Garmin: descarga 90 días de histórico intradiario |
| `GET` | `/api/connectors/garmin/status` | — | Estado del conector Garmin |
| `DELETE` | `/api/connectors/garmin` | — | Elimina conector y todos sus datos |

### 6.3 Sincronización

| Método | Ruta | Descripción |
|---|---|---|
| `POST` | `/api/sync` | Sync incremental de todos los conectores configurados |

Respuesta de sync:
```json
{
  "results": {
    "renpho": {"success": true, "new_records": 1, "new_weight": true},
    "garmin": {"success": true, "new_records": 2, "new_activities": 1, "new_hr_samples": 288}
  },
  "synced_at": "2026-06-17T16:14:45.117902"
}
```

### 6.4 KPIs

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/kpi/weight` | Peso actual + delta vs. anterior |
| `GET` | `/api/kpi/body-fat` | % grasa actual + delta |
| `GET` | `/api/kpi/metabolic-rate` | Tasa metabólica fitness (kcal/h) |
| `GET` | `/api/kpi/kcal-factor` | Factor kcal/kg del EPD |

Formato estándar de KPI:
```json
{
  "value": 82.0,
  "unit": "kg",
  "measured_at": "2026-06-16T06:43:08",
  "delta": -0.45,
  "delta_unit": "kg"
}
```

### 6.5 Históricos

| Método | Ruta | Query param | Descripción |
|---|---|---|---|
| `GET` | `/api/history/weight` | `period=all\|year\|month\|week` | Serie histórica de peso |
| `GET` | `/api/history/body-fat` | `period=...` | Serie histórica de % grasa |
| `GET` | `/api/history/metabolic-rate` | `period=...` | Serie histórica de tasa metabólica |
| `GET` | `/api/history/kcal-factor` | `period=...` | Histórico de calibraciones del factor kcal/kg |

Formato de serie: `[{"date": "2026-06-16", "value": 82.0}, ...]`

### 6.6 EPD

| Método | Ruta | Body | Descripción |
|---|---|---|---|
| `GET` | `/api/kpi/epd` | — | Calcula el peso estimado en este instante |
| `POST` | `/api/epd/process_weight` | `{"fasting": bool}` | Procesa un nuevo pesaje: con ayunas calibra el algoritmo |

Posibles valores del campo `action` en la respuesta de `POST /api/epd/process_weight`:

| Valor de `action` | Condición | Efecto |
|---|---|---|
| `reference_set` | Primera vez (no había referencia anterior) | Solo guarda la referencia |
| `reference_updated_interval_too_short` | `fasting=true` pero < 2 h desde último pesaje | Actualiza referencia; **no calibra** (señal insuficiente) |
| `calibrated` | `fasting=true` y ≥ 2 h | Calibra parámetros y actualiza referencia |
| `reference_updated_no_calibration` | `fasting=false` | Actualiza referencia sin calibrar |

Respuesta de `/api/kpi/epd`:
```json
{
  "estimated_weight": 80.147,
  "last_weight_kg": 82.0,
  "last_weight_at": "2026-06-16T06:43:08",
  "computed_at": "2026-06-17T16:14:00",
  "elapsed_hours": 33.51,
  "calories_burned": 3633,
  "delta_metabolica_kg": 0.305,
  "delta_agua_kg": 0.926,
  "total_lost_kg": 1.231,
  "loss_rate_kg_h": 0.0503,
  "tasa_evaporacion": 0.02671,
  "intensity_factor": 1.31,
  "stress_factor": 1.030,
  "hr_factor": 1.132,
  "avg_hr": 73.6,
  "garmin_connected": true,
  "garmin_days": 2,
  "garmin_last_sync": "2026-06-17T16:14:45",
  "needs_processing": false,
  "garmin_data_gap": false
}
```

---

## 7. Seguridad y cifrado

### Cifrado de credenciales

Las credenciales de Renpho y Garmin nunca se almacenan en texto plano. El proceso de cifrado usa dos capas:

1. **Derivación de clave** (PBKDF2-SHA256):
   - Material: `machine-id` (Linux `/etc/machine-id`) + `hostname` + `username`
   - Salt: 16 bytes aleatorios generados en el primer arranque
   - Iteraciones: 100.000
   - Resultado: clave de 32 bytes → codificada en base64 URL-safe

2. **Cifrado** (Fernet = AES-128-CBC + HMAC-SHA256):
   - Las credenciales JSON se cifran con esta clave
   - El salt se guarda en `data/.key` (permisos `0600`)
   - La clave derivada **no se almacena**; se recalcula en cada arranque

**Consecuencia práctica**: el fichero `data/dashboard.db` es ilegible en otro equipo (la clave se deriva del hardware/SO local). Si se copia a otra máquina, las credenciales no se pueden descifrar.

### Superficie de ataque

- La app solo escucha en `localhost:8000` — no expuesta a red local.
- No hay autenticación de sesión (diseño single-user local).
- Las contraseñas de Renpho/Garmin se transmiten a sus respectivas APIs externas usando HTTPS.
- Ningún dato de salud abandona el equipo local.

---

## 8. Instalación y arranque

### Requisitos

- Ubuntu 20.04+ (o cualquier Linux con Python 3.10+)
- Cuenta Renpho Health (app azul — **no** la roja/antigua)
- Cuenta Garmin Connect (opcional; mejora la precisión del EPD)

### Primera instalación

```bash
git clone https://github.com/Adaycr/enforma
cd enforma
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

El servidor arranca en `http://localhost:8000` y abre el navegador automáticamente.

### Arranque habitual

```bash
cd enforma
source venv/bin/activate
python3 run.py
```

O con el script de conveniencia:

```bash
bash start.sh
```

### Primera configuración en la app

1. Pulsar **"Conectar Renpho"** → introducir email/password de Renpho Health → se descarga todo el histórico.
2. Pulsar **"Conectar Garmin"** → introducir email/password de Garmin Connect → se descargan 90 días de datos intradiarios (puede tardar 3–5 minutos por los rate limits de la API).
3. El EPD se inicializa automáticamente con los parámetros por defecto y empieza a calibrarse en cada pesaje en ayunas.

### Estructura de ficheros

```
enforma/
├── run.py                    # Entry point; arranca Uvicorn
├── start.sh                  # Script de arranque rápido
├── requirements.txt          # Dependencias Python
├── backend/
│   ├── main.py               # API FastAPI + rutas
│   ├── epd.py                # Algoritmo EPD
│   ├── crypto.py             # Cifrado de credenciales
│   ├── db/
│   │   └── database.py       # SQLite: esquema, queries, métodos
│   └── connectors/
│       ├── renpho.py         # Conector Renpho Health
│       └── garmin.py         # Conector Garmin Connect
├── frontend/
│   └── index.html            # SPA: dashboard, KPIs, gráficos
├── sync_morning.sh           # Script de sync automático (cron 7:00)
└── data/                     # Generado automáticamente; NO commitear
    ├── .key                  # Salt de cifrado (permisos 0600)
    ├── dashboard.db          # Base de datos SQLite
    └── sync_morning.log      # Log de syncs automáticos
```

---

## 9. Flujo de uso diario

### 9.1 Sync automático matutino (07:00)

El cron job `sync_morning.sh` se ejecuta cada día a las 7:00 y llama a `POST /api/sync` si la app está corriendo. El resultado queda en `data/sync_morning.log`. El usuario no necesita hacer nada; cuando abre el dashboard, los datos de la noche ya están sincronizados.

Instalar / verificar cron:
```bash
crontab -l   # debe aparecer: 0 7 * * * /home/xilinx/Escritorio/enforma/sync_morning.sh
```

### 9.2 Flujo tras un nuevo pesaje

```
07:00 — sync_morning.sh (automático si la app está corriendo)
         ├─▶ Garmin: nuevas actividades, FC, estrés, respiración del día anterior
         └─▶ Renpho: pesajes nocturnos o matutinos pendientes
         │
         ▼ (o manualmente: pulsar "Actualizar" en el dashboard)

Usuario se pesa (báscula Renpho)
         │
         ▼
[Pulsa "Actualizar" en la app]
         │
         ├─▶ /api/sync ──▶ Renpho: descarga nuevas mediciones
         │               └─▶ Garmin: descarga actividades + samples desde último pesaje
         │
         ▼
¿Hay peso nuevo sin procesar?
  SÍ ──▶ Modal: "¿Han pasado ≥ 2 h y estabas en ayunas?"
         │
         ├─▶ SÍ ──▶ /api/epd/process_weight {fasting: true}
         │          ├─▶ ¿elapsed_h ≥ 2?
         │          │    SÍ ──▶ Calibra evaporation_rate + kcal_factor
         │          │           Guarda en epd_calibration_history
         │          │    NO ──▶ Solo actualiza referencia (action: reference_updated_interval_too_short)
         │          └─▶ Actualiza last_ref_weight en ambos casos
         │
         └─▶ NO ──▶ /api/epd/process_weight {fasting: false}
                    └─▶ Solo actualiza last_ref_weight (sin calibrar)
         │
         ▼
Dashboard actualizado:
  - EPD: nuevo peso estimado (≈ peso báscula, elapsed_h ≈ 0)
  - Live ticker: empieza a contar pérdida desde ahora
  - KPIs Renpho: nuevo peso, % grasa, deltas vs. día anterior
  - KPIs Garmin: tasa metabólica, FC reposo
```

---

## 10. Limitaciones y roadmap

### Limitaciones actuales

| Limitación | Detalle |
|---|---|
| APIs no oficiales | Renpho y Garmin usan APIs no documentadas públicamente; pueden cambiar sin previo aviso |
| MFA Garmin | Si la cuenta tiene MFA activado, el conector no puede autenticarse automáticamente |
| Calorías de movimiento ligero | Las kcal de movimiento no estructurado (caminar, escaleras) no tienen timestamp intradiario; se excluyen del EPD hasta que sean parte de una actividad registrada |
| Single-user | Sin autenticación web; solo apto para uso personal en equipo local |
| Portabilidad de datos | El fichero `data/` solo funciona en el mismo equipo (clave ligada al hardware) |
| Garmin sync lento | El setup inicial descarga datos intradiarios día a día respetando rate limits (~3–5 min para 90 días) |

### Correcciones aplicadas (historial)

| Versión | Corrección |
|---|---|
| v1.1 | Calorías activas de Garmin ahora usan timestamp exacto por actividad (antes: acumulado diario completo que incluía horas previas al pesaje) |
| v1.1 | Datos intradiarios de FC, estrés y respiración reemplazaron los promedios diarios |
| v1.2 | Guardia de calibración: si < 2 h desde último pesaje, el sistema actualiza la referencia pero no calibra (antes podía calibrar con señal de ruido y corromper parámetros) |
| v1.2 | KPI delta de peso ahora compara vs. pesaje de un día distinto (antes comparaba vs. pesaje inmediatamente anterior, mostrando variaciones intradía de ±200–400 g) |
| v1.2 | Cron job matutino de sync automático a las 07:00 |

### Roadmap

- [ ] Calorías intradiarias de movimiento ligero via steps × MET
- [ ] HRV nocturno de Garmin como señal adicional de recuperación
- [ ] Integración Oura Ring (sueño, temperatura cutánea, HRV)
- [ ] Exportación de datos a CSV/JSON
- [ ] Soporte multi-usuario (autenticación local)
- [ ] App móvil (PWA)
- [ ] Alertas por umbral (p.ej. notificar si el estimado supera cierto valor)
