import logging
import os
import json
import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore

# --- CORRECCIÓN AQUÍ: Importar Update y ContextTypes ---
from telegram import Update 
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes 

from handlers import start, style_selected, dithering_colors_selected, photo_handler
from db_utils import get_firestore_client 
from PIL import Image

# --- CONSTANTES ---
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
MAX_FREE_CREDITS = 10 # Créditos que se añaden semanalmente
WATERMARK_TEXT = "PIXELADO GRATIS | @PixelFusionBot"
MAX_IMAGE_SIZE_BYTES = 5 * 1024 * 1024 # 5 MB

db_initialized = False
try:
    # Intenta cargar el JSON de la clave de servicio desde la variable de entorno
    service_account_info = json.loads(os.environ["FIREBASE_KEY"]) 
    cred = credentials.Certificate(service_account_info)
    firebase_admin.initialize_app(cred)
    db_initialized = True
    print("Firebase inicializado y listo.")
except KeyError:
    # Este error ocurre si la variable de entorno FIREBASE_KEY no está definida
    print("ERROR FATAL: 'FIREBASE_KEY' no existe o está vacía.")
except Exception as e:
    # Este error atrapa problemas de formato JSON o problemas de conexión
    print(f"ERROR FATAL al inicializar Firebase. Detalle: {e}")
finally:
    if not db_initialized:
        print("El bot funcionará sin lógica de créditos.") # Este mensaje indica la falla

# --- NUEVO HANDLER DE PRUEBA: Recargar créditos ---
async def buy_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = get_firestore_client()
    user_id = update.message.from_user.id

    if db is None:
        await update.message.reply_text("❌ La base de datos no está disponible. No se puede recargar.")
        return

    # SIMULACIÓN: Añadir 5 créditos
    CREDITS_TO_ADD = 5
    user_ref = db.collection('users').document(str(user_id))

    try:
        user_ref.update({'paid_credits': firestore.Increment(CREDITS_TO_ADD)})
        await update.message.reply_text(
            f"✅ ¡Compra simulada exitosa! Se han añadido **{CREDITS_TO_ADD}** créditos a tu cuenta.\n"
            "Úsalos para generar imágenes sin marca de agua.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error(f"Error al simular la recarga de créditos: {e}")
        await update.message.reply_text("❌ Error al actualizar tu saldo. Intenta de nuevo.")


# --- MAIN ARRANQUE DEL BOT ---
if __name__ == '__main__':
    print("Bot reiniciado.")

    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data['MAX_FREE_CREDITS'] = MAX_FREE_CREDITS
    app.bot_data['WATERMARK_TEXT'] = WATERMARK_TEXT
    app.bot_data['MAX_IMAGE_SIZE_BYTES'] = MAX_IMAGE_SIZE_BYTES

    # 1. Comandos
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("buycredits", buy_credits)) # ¡Nuevo comando!

    # 2. Callbacks
    app.add_handler(CallbackQueryHandler(dithering_colors_selected, pattern="^(8|16|32)$"))
    app.add_handler(CallbackQueryHandler(style_selected, pattern="^(?![8|16|32]$).+"))

    # 3. Mensajes
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))

    print("Bot corriendo...")
    app.run_polling()