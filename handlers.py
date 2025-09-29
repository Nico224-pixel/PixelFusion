# handlers.py (Modificado)

import logging
from io import BytesIO
import telegram 
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from db_utils import get_user_data, decrement_credit, record_image_usage, get_firestore_client 
from image_processing import pixelate_image, apply_watermark, STYLE_DEFAULTS
from PIL import Image
from firebase_admin import firestore 

# --- PURCHASE CONSTANT ---
# {price_usd: credits_to_add}
PURCHASE_OPTIONS = {
    "2.5": 5,    # $2.5 USD -> 5 Credits
    "4": 10    # $4 USD -> 10 Credits
}

# --- Auxiliary Function for Safe Editing (Handling BadRequest) ---
# ... (safe_edit sin cambios) ...
async def safe_edit(query, text, markup=None, parse_mode="Markdown"):
    """Attempts to edit the message text, using the caption as a fallback if it fails. Accepts the keyboard as 'markup'."""
    try:
        await query.edit_message_text(text, reply_markup=markup, parse_mode=parse_mode)
    except telegram.error.BadRequest as e:
        if "message to edit" in str(e):
            try:
                await query.edit_message_caption(caption=text, reply_markup=markup, parse_mode=parse_mode)
            except Exception as e:
                logging.error(f"Safe editing failed (text and caption): {e}")
                await query.message.reply_text(text, reply_markup=markup, parse_mode=parse_mode)
        else:
             pass 
    except Exception as e:
        logging.error(f"Unknown error in safe_edit: {e}")

# --- Interface Helper: Style Menu ---
# ... (get_style_keyboard sin cambios) ...
def get_style_keyboard():
    """Generates the keyboard ONLY for style selection, WITHOUT balance buttons."""
    keyboard = [[InlineKeyboardButton(name.upper(), callback_data=name)] for name in STYLE_DEFAULTS.keys()]
    return InlineKeyboardMarkup(keyboard)

# --- Interface Helper: Balance/Purchase Menu ---
def get_purchase_options_keyboard():
    """Generates the keyboard for purchase options and the start button."""
    keyboard = [
        [
            # TRANSLATED BUTTON TEXT
            InlineKeyboardButton(f"💳 {price} USD for {credits} Credits", callback_data=f"buy_credits_{price}")
            for price, credits in PURCHASE_OPTIONS.items()
        ],
        [InlineKeyboardButton("🎨 Choose New Style /start", callback_data="start")] # TRANSLATED
    ]
    return InlineKeyboardMarkup(keyboard)

# --- Auxiliary: Simula la generación de un enlace de pago de PayPal ---
def simulate_paypal_link(user_id: int, price: str, credits: int) -> InlineKeyboardMarkup:
    """
    Simula la generación de un enlace de pago de PayPal. 
    En un entorno real, esto llamaría a la API de PayPal.
    Aquí, retorna un enlace a un callback del bot para simular la confirmación.
    """
    
    # 1. El botón de pago real que llevaría a PayPal
    # En un caso real: link_pago = get_paypal_order_link(...)
    # Lo simulamos con una URL ficticia
    paypal_link_url = "https://www.sandbox.paypal.com/checkout" 

    # 2. El botón de 'confirmación' que el usuario pulsaría tras 'pagar' en PayPal
    # Usamos un callback para simular el retorno del pago, pasando precio y créditos
    confirm_callback_data = f"paypal_confirm_{price}_{credits}"
    
    keyboard = [
        [
            InlineKeyboardButton(f"Pagar {price} USD con PayPal 🚀", url=paypal_link_url)
        ],
        [
            # Simulación de la confirmación de PayPal (SOLO PARA DEMO/PRUEBAS)
            InlineKeyboardButton("✅ Simulacro de Confirmación de Pago", callback_data=confirm_callback_data)
        ],
        [
            InlineKeyboardButton("⬅️ Volver al Saldo", callback_data="show_credits")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==========================================================
# 1. MAIN COMMANDS AND CALLBACKS
# ==========================================================

# ... (start sin cambios) ...
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the greeting, the balance (in the text), and the style buttons."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id
        query = None
        
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)
    
    # TRANSLATED MESSAGES
    saldo_msg = f"✨ Hello {update.effective_user.first_name}! I am the Pixel Art Bot.\n\n"
    saldo_msg += f"**💰 Balance:** **{user_data.get('total_credits', 0)}** credits (Free: {user_data.get('free_credits', 0)})"
    saldo_msg += "\n\n**1.** Select a style below. **2.** Send your photo 📸\n"
    saldo_msg += "You can check your detailed balance and buy credits with the /balance command."
    
    if query:
        await safe_edit(query, saldo_msg, markup=get_style_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(saldo_msg, reply_markup=get_style_keyboard(), parse_mode="Markdown")


# ... (show_credits sin cambios) ...
async def show_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the detailed user balance (/balance command or CALLBACK button)."""
    
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
    else:
        user_id = update.message.from_user.id
        query = None
    
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
    
    user_data = get_user_data(user_id, MAX_FREE_CREDITS)

    # TRANSLATED MESSAGES
    saldo_msg = (
        f"💳 **Account Status**\n\n"
        f"   - **Free Credits:** **{user_data.get('free_credits', 0)}** (Refilled weekly up to {MAX_FREE_CREDITS})\n"
        f"   - **Purchased Credits:** **{user_data.get('paid_credits', 0)}** (Watermark-free images)\n"
        f"   - **TOTAL:** **{user_data.get('total_credits', 0)}** credits.\n\n"
        f"**Recharge and remove the watermark!** Select your option:"
    )
    
    if query:
        await safe_edit(query, saldo_msg, markup=get_purchase_options_keyboard(), parse_mode="Markdown")
    else:
        await update.message.reply_text(saldo_msg, reply_markup=get_purchase_options_keyboard(), parse_mode="Markdown")

async def buy_credits_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genera el enlace de pago simulado de PayPal."""
    query = update.callback_query
    await query.answer()

    # Extraer el precio y los créditos de callback_data (e.g., 'buy_credits_5' -> '5')
    price_str = query.data.split('_')[-1]
    CREDITS_TO_ADD = PURCHASE_OPTIONS.get(price_str, 0)
    
    if CREDITS_TO_ADD == 0:
        await query.answer("❌ Invalid purchase option.", show_alert=True) # TRANSLATED
        return

    user_id = query.from_user.id
    
    # Simular la generación del enlace/botón de pago de PayPal
    markup = simulate_paypal_link(user_id, price_str, CREDITS_TO_ADD)

    # TRANSLATED MESSAGES
    payment_msg = (
        f"🛒 **Confirm your purchase:**\n\n"
        f"**Item:** {CREDITS_TO_ADD} Credits\n"
        f"**Price:** {price_str} USD\n\n"
        "Click on the PayPal button to proceed with the payment."
    )
    
    await safe_edit(query, payment_msg, markup=markup, parse_mode="Markdown")


async def paypal_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    SIMULACIÓN de la confirmación de pago de PayPal. 
    En un entorno real, esto se manejaría mediante un Webhook (IPN).
    """
    query = update.callback_query
    await query.answer("Processing payment confirmation...") # TRANSLATED

    # El patrón es 'paypal_confirm_{price}_{credits}'
    try:
        _, _, price_str, credits_str = query.data.split('_')
        CREDITS_TO_ADD = int(credits_str)
        price = price_str
    except Exception as e:
        logging.error(f"Error parsing paypal_confirm data: {e}")
        await query.answer("❌ Error processing confirmation data.", show_alert=True)
        await safe_edit(query, "❌ Error processing the payment confirmation. Please try again or contact support.")
        return

    db = get_firestore_client()
    user_id = query.from_user.id

    if db is None:
        await safe_edit(query, "❌ The database is unavailable. Cannot recharge credits.") # TRANSLATED
        return

    user_ref = db.collection('users').document(str(user_id))

    try:
        # AQUI SE HARÍA LA VERIFICACIÓN REAL DEL PAGO EN PRODUCCIÓN.
        # En la simulación, simplemente actualizamos el saldo:
        user_ref.update({'paid_credits': firestore.Increment(CREDITS_TO_ADD)})
        
        MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10)
        user_data_after = get_user_data(user_id, MAX_FREE_CREDITS)
        
        # TRANSLATED MESSAGES
        saldo_msg = (
            f"✅ Payment successful! **{CREDITS_TO_ADD}** credits have been added to your account for **{price} USD**.\n\n"
            f"   - **New Total Balance:** **{user_data_after.get('total_credits', 0)}** credits.\n"
            "Use them to generate watermark-free images."
        )
        await safe_edit(query, saldo_msg, markup=get_purchase_options_keyboard(), parse_mode="Markdown")
        
    except Exception as e:
        logging.error(f"Error simulating credit recharge after 'PayPal confirmation': {e}")
        await safe_edit(query, "❌ Error updating your balance after payment. Please try again.") # TRANSLATED


# ... (style_selected sin cambios) ...
async def style_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the initial style selection."""
    query = update.callback_query
    await query.answer()
    style = query.data
    context.user_data["style"] = style

    if style != "dithering":
        context.user_data.pop("dithering_colors", None)
        
        # TRANSLATED MESSAGES
        await safe_edit(query, 
                        f"✅ **{style.upper()}** style selected.\n\nNow, **send the photo 📸!**", 
                        parse_mode="Markdown")
        

    else:
        context.user_data.pop("dithering_colors", None)
        keyboard = [
             [InlineKeyboardButton("8 Colors", callback_data="8")], # TRANSLATED BUTTON
             [InlineKeyboardButton("16 Colors", callback_data="16")], # TRANSLATED BUTTON
             [InlineKeyboardButton("32 Colors", callback_data="32")] # TRANSLATED BUTTON
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # TRANSLATED MESSAGES
        await safe_edit(query, 
                        "🎨 **Dithering** selected. How many colors do you want to use?", 
                        markup=reply_markup)

# ... (help_command sin cambios) ...
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows the list of available commands and their brief description."""
    
    help_text = (
        "🤖 **Available Commands**\n\n"
        "Here are the commands you can use with the bot:\n\n"
        "**/start** — Start the bot, select a new pixel style, and check your current balance.\n"
        "**/balance** — View your detailed credit balance (Free and Purchased) and purchase more credits.\n"
        "**/buycredits** — Shortcut to the **/balance** menu for purchasing credits.\n"
        "**/help** — Show this list of commands and brief descriptions.\n\n"
        "💡 **To use the bot:** Select a style using **/start**, then send a photo!"
    )
    
    await update.message.reply_text(help_text, parse_mode="Markdown")

# ... (dithering_colors_selected sin cambios) ...
async def dithering_colors_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the selection of colors for dithering."""
    query = update.callback_query
    await query.answer()
    colors = int(query.data)

    context.user_data["style"] = "dithering"
    context.user_data["dithering_colors"] = colors

    # TRANSLATED MESSAGES
    await safe_edit(query, 
                    f"✅ **Dithering with {colors} colors** ready.\n\nNow, **send the photo 📸!**", 
                    parse_mode="Markdown")


# ==========================================================
# 2. MAIN PHOTO HANDLER
# ==========================================================

# ... (photo_handler sin cambios) ...
async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    # 1. VERIFICATION: Was a style selected?
    if "style" not in context.user_data:
        # TRANSLATED MESSAGES
        await msg.reply_text("🤔 Please select a style first:", reply_markup=get_style_keyboard())
        return

    # Injection of constants
    MAX_FREE_CREDITS = context.application.bot_data.get('MAX_FREE_CREDITS', 10) 
    WATERMARK_TEXT = context.application.bot_data.get('WATERMARK_TEXT', "WM")
    MAX_SIZE = context.application.bot_data.get('MAX_IMAGE_SIZE_BYTES', 2097152) 

    user_id = msg.from_user.id
    style = context.user_data["style"]
    dithering_colors = context.user_data.get("dithering_colors")
    
    # 2. FILE SIZE VERIFICATION (2 MB)
    file_info = msg.photo[-1]
    file_size = file_info.file_size

    if file_size > MAX_SIZE:
        # TRANSLATED MESSAGES
        await msg.reply_text(
            f"❌ **Image too large!** The maximum allowed size is "
            f"{MAX_SIZE / (1024 * 1024):.1f} MB. "
            "Please try with a smaller photo."
        )
        return
    
    # 3. CREDIT VERIFICATION AND DISCOUNT
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

        # Initial warning message (before processing the photo)
        # TRANSLATED MESSAGES
        await msg.reply_text(
            f"😔 **Out of credits!** Your image will be processed, but a **watermark** will be added."
            f"\n\n✨ You have {free_credits + paid_credits} total credits. Use /buycredits to recharge.",
            parse_mode="Markdown"
        )

    # 4. PROCESSING FEEDBACK (UX)
    await context.bot.send_chat_action(chat_id=msg.chat_id, action="upload_photo")
    await msg.reply_text("⚙️ **Photo received!** Processing image, please wait...", parse_mode="Markdown") # TRANSLATED

    # 5. DOWNLOAD AND PROCESSING 
    photo_file = await file_info.get_file()
    photo_bytes = BytesIO()

    try:
        await photo_file.download_to_memory(out=photo_bytes)
        photo_bytes.seek(0)
        img = Image.open(photo_bytes).convert("RGB")
    except Exception as e:
        logging.error(f"Error downloading/opening the photo: {e}")
        await msg.reply_text("❌ Could not download or open the photo. Please try again.") # TRANSLATED
        return

    try:
        pixel_img = pixelate_image(img, style=style, dithering_colors=dithering_colors)
    except Exception as e:
        logging.error(f"Error processing the image: {e}")
        await msg.reply_text("❌ An error occurred while applying the style. Please try again.") # TRANSLATED
        return

    if apply_wm:
        pixel_img = apply_watermark(pixel_img, WATERMARK_TEXT)

    out_bytes = BytesIO()
    pixel_img.save(out_bytes, format="PNG")
    out_bytes.seek(0)

    # 6. SEND RESULT AND CAPTION
    
    # TRANSLATED CAPTIONS
    caption = f"✅ **Style applied:** {style.upper()}"
    if style == "dithering" and dithering_colors:
        caption += f" ({dithering_colors} colors)"

    if not apply_wm:
        caption += f"\n\n💰 Remaining balance: **{total_credits_remaining}** credits.\n(Free: {free_credits}, Purchased: {paid_credits})"
    else:
         caption += "\n\n✨ Generated with a watermark. Recharge with **/buycredits** to remove it!"


    await msg.reply_photo(photo=out_bytes, 
                          caption=caption, 
                          parse_mode="Markdown")

    # 7. LOG USAGE
    record_image_usage(user_id=user_id, style=style, is_watermarked=apply_wm)

    # 8. Clear user_data
    context.user_data.pop("style", None)
    context.user_data.pop("dithering_colors", None)
