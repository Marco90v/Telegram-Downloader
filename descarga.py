import os
import asyncio
from telethon import TelegramClient

# --- CONFIGURACIÓN DE CREDENCIALES ---
# El ID es un número (int), no lleva comillas.
API_ID =  34415379   

# El Hash es texto (str), SI lleva comillas.
API_HASH = "4e6613ec2f2bdcdf74df4c477db9fc00"  

# El ID del grupo/canal obtenido de la URL web (int), sin comillas.
TARGET_CHAT = -3025926105

# Ruta absoluta en tu Debian donde se guardará el multimedia
OUTPUT_DIR = os.path.expanduser("~/Descargas/Telegram_Masivo")

# Creamos el directorio si no existe
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Inicializamos el cliente nativo de Telegram
client = TelegramClient('sesion_debiantg', API_ID, API_HASH)

async def main():
    print("Conectando a los servidores de Telegram...")
    
    # El script leerá el historial de mensajes de forma inversa (de más nuevo a más viejo)
    async for message in client.iter_messages(TARGET_CHAT):
        # Filtramos: Solo nos interesan mensajes que contengan archivos multimedia (fotos o videos)
        if message.media:
            print(f"Detectado archivo en Mensaje ID: {message.id}. Descargando...")
            try:
                # La API nativa descarga el archivo real saltándose los bloqueos estéticos de la App
                path = await client.download_media(message, OUTPUT_DIR)
                print(f"Guardado con éxito en: {path}")
            except Exception as e:
                print(f"Error descargando el mensaje {message.id}: {e}")

# Ejecución del bucle asíncrono
with client:
    client.loop.run_until_complete(main())
