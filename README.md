# Telegram Mass Downloader

Descarga masiva de fotos y videos de grupos/canales de Telegram.

## Requisitos

- Python 3.10+
- API ID + API Hash de [my.telegram.org](https://my.telegram.org/apps)

## Instalación

```bash
git clone <repo-url>
cd descarga
python -m venv venv
venv/bin/pip install -r requirements.txt
```

## Configuración

### 1. `.env` (credenciales)

Copiá `.env.example` a `.env` y completá:

| Variable | Descripción |
|----------|-------------|
| `TELEGRAM_API_ID` | De my.telegram.org |
| `TELEGRAM_API_HASH` | De my.telegram.org |
| `TELEGRAM_TARGET_CHAT` | ID numérico, username o link del chat |
| `TELEGRAM_SESSION_NAME` | Nombre de la sesión (default: `sesion_telegram`) |
| `OUTPUT_DIR` | Carpeta destino (default: `~/Descargas/Telegram_Masivo`) |
| `BATCH_SIZE` | Archivos por lote (default: `100`) |

### 2. `settings.json` (comportamiento)

Se crea automáticamente al ejecutar. Editálo con cualquier editor de texto.

```json
{
  "auto_skip_all_dupes": false,
  "auto_continue": false,
  "large_file_threshold_mb": 50,
  "large_file_action": "ask"
}
```

| Opción | Valores | Default | Qué hace |
|--------|---------|---------|----------|
| `auto_skip_all_dupes` | `true` / `false` | `false` | Si todo el lote fue duplicado u omitido por tamaño (nada nuevo descargado), saltea la pregunta y sigue al siguiente automáticamente |
| `auto_continue` | `true` / `false` | `false` | Modo silencioso total — nunca pregunta entre lotes. Procesa todo sin parar. Ctrl+C para interrumpir. **No afecta** la pregunta inicial de fechas ni la confirmación de arranque |
| `large_file_threshold_mb` | número | `50` | Archivos más grandes que esto (MB) reciben tratamiento especial. `0` desactiva el control |
| `large_file_action` | `"ask"` / `"download"` / `"skip"` | `"ask"` | `ask` → pregunta por cada archivo grande · `download` → descarga siempre · `skip` → omite sin preguntar |

**Combinaciones comunes:**

| Config | Efecto |
|--------|--------|
| Default (todo false, `"ask"`) | Modo interactivo: pregunta por archivos > umbral y al final de cada lote |
| `large_file_action: "skip"` | Omite archivos pesados en silencio, pregunta solo al final del lote |
| `auto_skip_all_dupes: true` + `"skip"` | Saltea la pregunta por lote si nada nuevo se descargó. Pregunta solo cuando algo se bajó |
| `auto_continue: true` + `"skip"` | **Modo fuego y olvido**: configura una vez, ejecutá y dejala correr |

## Uso

```bash
./run.sh
```

O si preferís activar el venv manualmente:

```bash
source venv/bin/activate
python descarga.py
```

1. Opcional: filtrar por rango de fechas
2. Si es la primera vez o elegís empezar de nuevo, confirma que querés comenzar
3. **Reanudar sesiones anteriores**: el script detecta si ya descargaste contenido de este chat y te pregunta:
   - **Solo contenido nuevo**: descarga solo los mensajes posteriores al último visto
   - **Continuar completo**: además del contenido nuevo, sigue descargando hacia atrás saltando lo ya procesado
   - **Empezar de nuevo**: ignora el historial y descarga todo otra vez
4. El script descarga en lotes, preguntando si querés continuar después de cada uno (excepto si `auto_continue: true`)
5. Ctrl+C interrumpe limpia y ordenadamente

Al finalizar, el catálogo (`catalog.json`) se actualiza automáticamente para la próxima reanudación.

Los archivos se guardan como `YYYYMMDD_MessageID.ext` en una carpeta por chat, con subcarpeta si el chat tiene un canal vinculado.

## Salida en terminal

- `📷` = foto, `🎬` = video
- Barra de progreso con porcentaje y MB descargados / total
- `✓` = descargado (verde), `⏭` = ya existía u omitido (amarillo), `✗` = error (rojo)
- Encabezados e información clave en cian
- Sin dependencias — usa ANSI escape codes puros

## Catálogo de reanudación

`catalog.json` se crea automáticamente al lado de `settings.json`. Registra por chat:

- Rango de message IDs procesados (el más reciente y el más antiguo)
- Cantidad total y fecha de la última sesión

No requiere mantenimiento manual. Si borrás los archivos del disco y querés descargar solo lo nuevo, el catálogo evita que se descargue todo de nuevo.

## Desarrollo

```bash
pip install -r requirements.txt      # instala dependencias + herramientas dev
pre-commit install                    # activa hooks de Ruff al hacer commit
ruff check descarga.py                # lint
ruff check descarga.py --fix          # lint + auto-fix
ruff format descarga.py               # formatear código
```

El proyecto usa [Ruff](https://astral.sh/ruff) como linter y formatter, con pre-commit hooks que verifican automáticamente antes de cada commit. Configuración en `pyproject.toml`.
