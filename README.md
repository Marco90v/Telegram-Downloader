# Telegram Mass Downloader

Descarga masiva de fotos y videos de grupos/canales de Telegram.

## Requisitos

- Python 3.10+
- `pip install -r requirements.txt`
- API ID + API Hash de [my.telegram.org](https://my.telegram.org/apps)

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
python descarga.py
```

1. Opcional: filtrar por rango de fechas
2. Confirma que querés empezar
3. El script descarga en lotes, preguntando si querés continuar después de cada uno
4. Ctrl+C interrumpe limpia y ordenadamente

Los archivos se guardan como `YYYYMMDD_MessageID.ext` en una carpeta por chat, con subcarpeta si el chat tiene un canal vinculado.

## Salida en terminal

- `📷` = foto, `🎬` = video
- Barra de progreso con porcentaje y MB descargados / total
- `✓` = descargado, `⏭` = ya existía u omitido, `✗` = error
