# 🏃 EnForma

Dashboard deportivo personal — local, responsivo, cifrado.

## Requisitos mínimos
- Ubuntu 20.04+
- Python 3.10+
- Cuenta Renpho Health (app de icono azul, **no** la roja antigua)

## Instalación (primera vez)

```bash
# 1. Descomprime o clona el proyecto
cd sports-dashboard

# 2. (Opcional pero recomendado) Entorno virtual
python3 -m venv venv
source venv/bin/activate

# 3. Instala dependencias
pip install -r requirements.txt

# 4. Arranca
python3 run.py
```

Se abrirá automáticamente en `http://localhost:8000`.

## Arranque normal (después de la primera vez)

```bash
cd sports-dashboard
source venv/bin/activate   # si usas venv
python3 run.py
```

O simplemente:

```bash
bash start.sh
```

## Primer uso

1. Al entrar verás el botón **"Conectar Renpho"**
2. Introduce tu email y contraseña de Renpho Health
3. El sistema descargará **todo tu histórico** — puede tardar unos segundos
4. Tus credenciales se cifran con AES-256 derivado del ID de tu máquina — nunca salen del disco

## Uso diario

- Botón **Actualizar** (arriba a la derecha) → sincroniza solo los nuevos registros
- Click en cualquier **tarjeta KPI** → abre el histórico con gráfico interactivo
- Filtros: Todo / Año / Mes / Semana

## Estructura de datos

```
data/
├── .key          # Clave cifrada (no compartir)
└── dashboard.db  # SQLite con histórico cifrado
```

## Notas de seguridad

- Todo se almacena **en local**, nada se envía a terceros
- Las credenciales se cifran con PBKDF2-SHA256 + Fernet (AES-128-CBC)
- La clave se deriva de tu `machine-id` + hostname — solo funciona en este equipo

## Próximas integraciones previstas

- [ ] Garmin Connect (actividades, VO2max, HRV)
- [ ] Strava (rutas, potencia, cadencia)
- [ ] Apple Health / Google Fit
- [ ] Oura Ring (sueño, HRV)
