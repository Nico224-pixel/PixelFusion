# handlers.py (VERSIÓN FINAL Y CORREGIDA)

import logging
from io import BytesIO
import telegram 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db_utils import get_user_data, decrement_credit, record_image_usage, get_firestore_client 
from image_processing import pixelate_image, apply_watermark, STYLE_DEFAULTS
from PIL import Image
from firebase_admin import firestore 

# --- CONSTANTE DE COMPRA ---
# {precio_dolares: creditos_a_añadir}
PURCHASE_OPTIONS = {
    "5": 5,    # $5 USD -> 5 Creditos
    "8": 10    # $8 USD -> 10 Creditos
}

# --- Función Auxiliar para Edición Segura (Manejando el BadRequest) ---
async def safe_edit(query, text, markup=None, parse_mode="Markdown"):
    """Intenta editar el texto del mensaje, usando el caption como fallback si falla. Acepta el teclado como 'markup'."""
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
    except telegram.error.BadRequest as e:
        if "message to edit" in str(e):
            try:
                await query.edit_message_caption(caption=text, reply_markup=markup, parse_mode=parse_mode)
            except Exception as e:
                logging.error(f"Fallo la edición segura (texto y caption): {e}")
                await query.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)
        else:
             pass 
    except Exception as e:
        logging.error(f"Error desconocido en safe_edit: {e}")

# --- Ayuda de Interfaz: Menú de Estilos ---
def get_style_keyboard():
    """Genera el teclado SÓLO para la selección de estilos, SIN botones de saldo."""
    keyboard = [[InlineKeyboardButton(name.upper(), callback_data=name)] for name in STYLE_DEFAULTS.keys()]
    return InlineKeyboardMarkup(keyboard)

# --- Ayuda de Interfaz: Menú de Saldo/Compra ---
# --- Ayuda de Interfaz: Menú de Saldo/Compra ---
def get_purchase_options_keyboard():
    """Genera el teclado para las opciones de compra y el botón de inicio."""
    keyboard = [
        [
            InlineKeyboardButton(f"💳 {price} USD por {credits} Créditos", callback_data=f"buy_credits_{price}")
            for price, credits in PURCHASE_OPTIONS.items()
        ],
        [InlineKeyboardButton("🎨 Elegir Nuevo Estilo /start", callback_data="start")]
    ]
    return InlineKeyboardMarkup(keyboard)


# ==========================================================
# 1. COMANDOS PRINCIPALES Y CALLBACKS
# ==========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el saludo, el saldo (en el texto) y los botones de estilo."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id
        query = None
        
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)
    
    saldo_msg = f"✨ ¡Hola {update.effective_user.first_name}! Soy el Bot de Pixel Art.\n\n"
    saldo_msg += f"**💰 Saldo:** **{user_data.get('total_credits', 0)}** créditos (Gratuitos: {user_data.get('free_credits', 0)})"
    saldo_msg += "\n\n**1.** Selecciona un estilo abajo. **2.** Envía tu foto 📸\n"
    saldo_msg += "Puedes consultar tu saldo detallado y comprar créditos con el comando /saldo."
    
    if query:
        await safe_edit(query, saldo_msg, markup=get_style_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(saldo_msg, reply_markup=get_style_keyboard(), parse_mode="Markdown")


async def show_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el saldo detallado del usuario (comando /saldo o botón CALLBACK)."""
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id
        query = None
    
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)

    saldo_msg = (
        f"💳 **Estado de tu Cuenta**\n\n"
        f"   - **Créditos Gratuitos:** **{user_data.get('free_credits', 0)}** (Se recargan semanalmente hasta {MAX_FREE_CREDITS})\n"
        f"   - **Créditos Comprados:** **{user_data.get('paid_credits', 0)}** (Imágenes sin marca de agua)\n"
        f"   - **TOTAL:** **{user_data.get('total_credits', 0)}** créditos.\n\n"
        f"**¡Recarga y quita la marca de agua!** Selecciona tu opción:"
    )
    
    if query:
        await safe_edit(query, saldo_msg, markup=get_purchase_options_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(saldo_msg, reply_markup=get_purchase_options_keyboard(), parse_mode="Markdown")

async def buy_credits_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Simulación de compra de créditos desde un callback de botón."""
    query = update.callback_query
    
    # Extraer el precio del callback_data (ej: 'buy_credits_5' -> '5')
    price_str = query.data.split('_')[-1]
    
    # Obtener la cantidad de créditos a añadir, usando el diccionario de opciones
    CREDITS_TO_ADD = PURCHASE_OPTIONS.get(price_str, 0)
    
    if CREDITS_TO_ADD == 0:
        await query.answer("❌ Opción de compra no válida.", show_alert=True)
        return

    await query.answer(f"Procesando compra simulada de {price_str} USD...")
    
    db = get_firestore_client()
    user_id = query.from_user.id

    if db is None:
        await safe_edit(query, "❌ La base de datos no está disponible. No se puede recargar.")
        return

    user_ref = db.collection('users').document(str(user_id))

    try:
        user_ref.update({'paid_credits': firestore.Increment(CREDITS_TO_ADD)})
        
        MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
        user_data_after = get_user_data(user_id, MAX_FREE_CREDITS)
        
        saldo_msg = (
            f"✅ ¡Compra simulada exitosa! Por **{price_str} USD** se han añadido **{CREDITS_TO_ADD}** créditos a tu cuenta.\n\n"
            f"   - **Nuevo Saldo Total:** **{user_data_after.get('total_credits', 0)}** créditos.\n"
            "Úsalos para generar imágenes sin marca de agua."
        )
        await safe_edit(query, saldo_msg, markup=get_purchase_options_keyboard(), parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error al simular la recarga de créditos: {e}")
        await safe_edit(query, "❌ Error al actualizar tu saldo. Intenta de nuevo.")

# ... (El resto de handlers, sin cambios) ...

async def style_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección inicial del estilo."""
    query = update.callback_query
    await query.answer()
    style = query.data
    context.user_data["style"] = style

    if style != "dithering":
        context.user_data.pop("dithering_colors", None)
        
        await safe_edit(query, 
                        f"✅ Estilo **{style.upper()}** seleccionado.\n\nAhora, **¡envía la foto 📸!**", 
                        parse_mode="Markdown")
        

    else:
        context.user_data.pop("dithering_colors", None)
        keyboard = [
             [InlineKeyboardButton("8 Colores", callback_data="8")],
             [InlineKeyboardButton("16 Colores", callback_data="16")],
             [InlineKeyboardButton("32 Colores", callback_data="32")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # *** CORRECCIÓN: Usar 'markup' en lugar de 'reply_markup' ***
        await safe_edit(query, 
                        "🎨 **Dithering** seleccionado. ¿Cuántos colores quieres usar?", 
                        markup=reply_markup)


async def dithering_colors_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maneja la selección de colores para dithering."""
    query = update.callback_query
    await query.answer()
    colors = int(query.data)

    context.user_data["style"] = "dithering"
    context.user_data["dithering_colors"] = colors

    await safe_edit(query, 
                    f"✅ **Dithering con {colors} colores** listo.\n\nAhora, **¡envía la foto 📸!**", 
                    parse_mode="Markdown")


# ==========================================================
# 2. HANDLER PRINCIPAL DE FOTOS
# ==========================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    # 1. VERIFICACIÓN: ¿Se seleccionó un estilo?
    if "style" not in context.user_data:
        await msg.reply_text("🤔 Por favor, selecciona un estilo primero:", reply_markup=get_style_keyboard())
        return

    # Inyección de constantes
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10) 
    WATERMARK_TEXT = context.application.bot_data.get('WATERMARK_TEXT', "WM")
    MAX_SIZE = context.application.bot_data.get('MAX_IMAGE_SIZE_BYTES', 2097152) 

    user_id = msg.from_user.id
    style = context.user_data["style"]
    dithering_colors = context.user_data.get("dithering_colors")
    
    # 2. VERIFICACIÓN DE TAMAÑO DEL ARCHIVO (2 MB)
    file_info = msg.photo[-1]
    file_size = file_info.file_size

    if file_size > MAX_SIZE:
        await msg.reply_text(
            f"❌ **¡Imagen muy grande!** El tamaño máximo permitido es de "
            f"{MAX_SIZE / (1024 * 1024):.1f} MB. "
            "Por favor, intenta con otra foto más pequeña."
        )
        return
    
    # 3. VERIFICACIÓN Y DESCUENTO DE CRÉDITOS
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)
    total_credits_before = user_data.get('total_credits', 0) 
    
    apply_wm = False 
    
    if total_credits_before > 0:
        decrement_credit(user_id) 
        
        user_data_after = get_user_data(user_id, MAX_FREE_CREDITS)
        free_credits = user_data_after.get('free_credits', 0)
        paid_credits = user_data_after.get('paid_credits', 0)
        total_credits_remaining = user_data_after.get('total_credits', 0)

    else:
        apply_wm = True
        free_credits = user_data.get('free_credits', 0)
        paid_credits = user_data.get('paid_credits', 0)
        total_credits_remaining = 0

        # Mensaje de advertencia inicial (antes de procesar la foto)
        await msg.reply_text(
            f"😔 **¡Créditos agotados!** Tu imagen se procesará, pero se le añadirá una **marca de agua**."
            f"\n\n✨ Tienes {free_credits + paid_credits} créditos totales. Usa /buycredits para recargar.",
            parse_mode="Markdown"
        )

    # 4. FEEDBACK DE PROCESAMIENTO (UX)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action="upload_photo")
    await msg.reply_text("⚙️ **¡Foto recibida!** Procesando imagen, espera un momento...", parse_mode="Markdown")

    # 5. DESCARGA Y PROCESAMIENTO 
    photo_file = await file_info.get_file()
    photo_bytes = BytesIO()

    try:
        await photo_file.download_to_memory(out=photo_bytes)
        photo_bytes.seek(0)
        img = Image.open(photo_bytes).convert("RGB")
    except Exception as e:
        logging.error(f"Error al descargar/abrir la foto: {e}")
        await msg.reply_text("❌ No pude descargar o abrir la foto. Intenta de nuevo.")
        return

    try:
        pixel_img = pixelate_image(img, style=style, dithering_colors=dithering_colors)
    except Exception as e:
        logging.error(f"Error al procesar la imagen: {e}")
        await msg.reply_text("❌ Ocurrió un error al aplicar el estilo. Intenta de nuevo.")
        return

    if apply_wm:
        pixel_img = apply_watermark(pixel_img, WATERMARK_TEXT)

    out_bytes = BytesIO()
    pixel_img.save(out_bytes, format="PNG")
    out_bytes.seek(0)

    # 6. ENVIAR RESULTADO Y CAPTION
    
    caption = f"✅ **Estilo aplicado:** {style.upper()}"
    if style == "dithering" and dithering_colors:
        caption += f" ({dithering_colors} colores)"

    if not apply_wm:
        caption += f"\n\n💰 Te queda un saldo de **{total_credits_remaining}** créditos.\n(Gratuitos: {free_credits}, Comprados: {paid_credits})"
    else:
         caption += "\n\n✨ Generada con marca de agua. ¡Recarga con **/buycredits** para quitársela!"


    await msg.reply_photo(photo=out_bytes, 
                          caption=caption, 
                          parse_mode="Markdown")

    # 7. REGISTRAR USO
    record_image_usage(user_id=user_id, style=style, is_watermarked=apply_wm)

    # 8. Limpiar user_data
    context.user_data.pop("style", None)
    context.user_data.pop("dithering_colors", None)