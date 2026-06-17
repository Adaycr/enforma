# EnForma

Dashboard de salud personal **local y cifrado** que estima el peso corporal en tiempo real entre pesajes, combinando datos de báscula Renpho y wearable Garmin mediante el algoritmo EPD (Estimador de Peso Dinámico).

## ¿Qué hace?

Las básculas solo registran el peso en el momento del pesaje. EnForma responde a *"¿cuánto peso ahora mismo, sin subirme a la báscula?"* modelando la pérdida continua de masa por metabolismo y evaporación. Con los datos de actividad, frecuencia cardíaca, estrés y respiración de Garmin, el modelo se afina en tiempo real y se auto-calibra con cada nuevo pesaje.

![Dashboard](https://github.com/Adaycr/enforma/raw/main/docs/screenshot.png)

## Características

- **EPD en vivo** — peso estimado con live ticker que se actualiza cada 5 s sin polling al servidor
- **Datos intradiarios** — FC (~15 s), estrés (~3 min), respiración y actividades con timestamp exacto; no promedios diarios
- **Auto-calibración** — gradient descent ajusta los parámetros del modelo cuando han pasado ≥ 2 h y el usuario confirma ayuno
- **Sync matutino automático** — cron job a las 7:00 que sincroniza Renpho y Garmin si la app está corriendo
- **100% local** — ningún dato sale del equipo; todo en SQLite local
- **Cifrado en reposo** — credenciales cifradas con AES-256 derivado del ID de máquina (PBKDF2 + Fernet)
- **Históricos con gráficos** — peso, % grasa, tasa metabólica, factor kcal/kg; filtros por semana / mes / año

## Requisitos

- Ubuntu 20.04+ · Python 3.10+
- Cuenta [Renpho Health](https://renpho.com) (app azul)
- Cuenta [Garmin Connect](https://connect.garmin.com) (opcional; mejora la precisión)

## Instalación

```bash
git clone https://github.com/Adaycr/enforma
cd enforma
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 run.py
```

Se abre automáticamente en `http://localhost:8000`.

## Uso diario

El sync matutino (7:00) descarga automáticamente los datos de la noche si la app está corriendo.

1. Pésate por la mañana en ayunas
2. Pulsa **Actualizar** — sincroniza nuevos pesajes y datos Garmin
3. Si hay un peso nuevo, confirma que llevabas ≥ 2 h en ayunas → el algoritmo calibra sus parámetros
4. El ticker EPD muestra el peso estimado en tiempo real

## Algoritmo EPD

```
Peso_estimado = Peso_referencia − ΔMetabólico − ΔAgua

ΔMetabólico = kcal_quemadas / kcal_factor
ΔAgua       = evaporation_rate × (intensity_f × stress_f × hr_f × resp_f) × elapsed_h
```

Los tres parámetros (`evaporation_rate`, `kcal_factor`, `fitness_factor`) se auto-calibran por gradient descent. Los multiplicadores dinámicos se calculan con muestras intradiarias reales desde el último pesaje.

Documentación completa: [`ENFORMA_DOC.md`](ENFORMA_DOC.md)

## Estructura

```
backend/
├── main.py          # API FastAPI
├── epd.py           # Algoritmo EPD + calibración
├── crypto.py        # Cifrado AES-256
├── db/database.py   # SQLite: 10 tablas, queries EPD
└── connectors/
    ├── renpho.py    # Conector Renpho Health
    └── garmin.py    # Conector Garmin (diario + intradiario)
frontend/
└── index.html       # SPA vanilla: KPIs, gráficos, live ticker
sync_morning.sh      # Sync automático vía cron (07:00 diario)
```

## Seguridad

Las credenciales se cifran con una clave derivada del hardware local (PBKDF2-SHA256 sobre `machine-id` + hostname). El fichero `data/` no es portable entre equipos.
