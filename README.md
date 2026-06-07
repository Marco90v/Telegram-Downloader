# Telegram Mass Downloader

Descarga masiva de fotos y videos de grupos/canales de Telegram.

Dos interfaces: **CLI** (ANSI, liviana) y **TUI** (Textual, interactiva).

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

---

## Interfaces

### CLI (descarga.py)

```bash
./run.sh                  # CLI (default)
# o directamente:
python descarga.py
```

Flujo:

1. **Filtro por fechas** (opcional): podés limitar la descarga a un rango de fechas
2. **Confirmación**: confirma que querés arrancar
3. **Reanudación**: si ya hay sesión anterior, elegí:
   - **Solo contenido nuevo** — descarga solo lo que no se bajó antes
   - **Continuar hacia atrás** — salta lo ya descargado pero sigue procesando más atrás
   - **Empezar de nuevo** — ignora el historial
4. **Descarga por lotes**: después de cada lote pregunta si seguir (excepto con `auto_continue: true`)
5. **Archivos grandes**: con `large_file_action: "ask"` pregunta por cada archivo que supere el umbral

#### Comandos de catálogo (CLI)

```bash
# Listar chats con sesión guardada
python descarga.py --catalog list

# Eliminar una entrada del catálogo
python descarga.py --catalog remove <chat_key>

# Eliminar entrada + borrar archivos descargados
python descarga.py --catalog remove <chat_key> --delete-files
```

#### Atajos de teclado (CLI)

| Tecla | Acción |
|-------|--------|
| `s` / `y` | Sí / Seguir |
| `n` / `q` | No / Detener |
| `Ctrl+C` | Interrumpir descarga (cierra sesión limpiamente) |

---

### TUI (tui.py)

```bash
./run.sh --tui
# o directamente:
python tui.py
```

> **Requiere:** `pip install textual>=8.0`

Interfaz gráfica en terminal con 3 paneles, teclas rápidas y diálogos modales.

#### Pantallas

| Pantalla | Descripción |
|----------|-------------|
| **Login** | Ingreso de teléfono, código de verificación y contraseña 2FA (si aplica) |
| **Principal** | 3 paneles: resumen (stats), detalle (archivo actual + barra de progreso), log (historial coloreado) |
| **Config** | Chat, fechas, tamaño de lote, umbral de archivos grandes, acción, auto-continuar, auto-omitir duplicados |
| **Catálogo** | Lista de chats con sesión guardada — permite borrar entradas (con o sin archivos) |
| **Reanudación** | Modal al iniciar si hay sesión anterior: "Solo nuevo" o "Verificar todo" |
| **Continuar** | Modal entre lotes: "Detener" o "Continuar" |

#### Panel izquierdo (stats en vivo)

- Chat activo
- Archivos descargados
- Tamaño total
- Errores
- Procesados en este lote
- Velocidad de descarga
- Tiempo transcurrido

#### Panel derecho (detalle + progreso)

- Nombre del archivo actual con tamaño total
- Barra de progreso con porcentaje
- Lote actual

#### Botones

| Botón | Acción |
|-------|--------|
| ▶ Iniciar | Comienza la descarga (o reintenta tras un error) |
| ⏸ Pausar / ▶ Reanudar | Pausa/reanuda — guarda checkpoint del catálogo al pausar |
| ⚙ Config | Abre pantalla de configuración |
| 📋 Catálogo | Abre el catálogo de sesiones guardadas |
| ✕ Salir | Guarda checkpoint y cierra la app |

#### Atajos de teclado (TUI)

| Tecla | Acción |
|-------|--------|
| `s` | Iniciar descarga |
| `p` | Pausar / Reanudar |
| `c` | Abrir configuración |
| `d` | Alternar tema claro/oscuro |
| `q` | Salir (guarda checkpoint) |
| `Ctrl+T` | Alternar tema (global) |
| `Escape` | Volver (en pantallas secundarias) |

#### Checkpoints

La TUI guarda el catálogo automáticamente en estos momentos:

- Al **pausar** la descarga
- Al **salir** de la app
- Entre **lotes** (antes del diálogo de continuar)
- Al **finalizar** la descarga

Esto evita pérdida de progreso si cerrás la app accidentalmente.

---

## Salida en terminal

| Símbolo | Significado | Color |
|---------|-------------|-------|
| `📷` | Foto | — |
| `🎬` | Video | — |
| `✓` | Descargado correctamente | Verde |
| `⏭` | Ya existía / Omitido | Amarillo |
| `✗` | Error | Rojo |

La CLI usa ANSI escape codes puros. La TUI usa Textual markup con colores integrados.

---

## Catálogo de reanudación

`catalog.json` se crea automáticamente al lado de `settings.json`. Registra por chat:

- Rango de message IDs procesados (el más reciente y el más antiguo)
- Cantidad total y fecha de la última sesión

No requiere mantenimiento manual. Si borrás los archivos del disco y querés descargar solo lo nuevo, el catálogo evita que se descargue todo de nuevo.

---

## Desarrollo

```bash
pip install -r requirements.txt      # instala dependencias + herramientas dev
pre-commit install                    # activa hooks de Ruff al hacer commit
ruff check tui.py descarga.py core.py                # lint
ruff check tui.py descarga.py core.py --fix          # lint + auto-fix
ruff format tui.py descarga.py core.py               # formatear código
pip install -e .                      # instalación editable (opcional)
```

El proyecto usa [Ruff](https://astral.sh/ruff) como linter y formatter, con pre-commit hooks que verifican automáticamente antes de cada commit. Configuración en `pyproject.toml`.

### Arquitectura

```
core.py          → Motor compartido: conexión, descarga, catálogo, contadores
descarga.py      → Interfaz CLI: prompts interactivos, barras ANSI
tui.py           → Interfaz TUI: pantallas, diálogos modales, progreso visual
```

Ambas interfaces usan `core.py` como backend. Los comandos de catálogo (`--catalog list/remove`) también funcionan desde CLI.
