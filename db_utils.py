# db_utils.py (VERSIÓN COMPLETA Y FINAL)

import datetime
from google.cloud.firestore_v1.base_client import BaseClient
from firebase_admin import firestore
import logging

# --- Cliente de Firestore ---
def get_firestore_client() -> BaseClient | None:
    """Intenta obtener el cliente de Firestore si la inicialización fue exitosa."""
    try:
        return firestore.client()
    except Exception:
        return None

# --- Obtener y Resetear Datos del Usuario (Doble Saldo) ---
def get_user_data(user_id: int, max_credits: int) -> dict:
    """Obtiene los datos del usuario, calculando el saldo total y reseteando los créditos gratuitos."""
    db = get_firestore_client()

    if db is None:
        # Fail-safe
        return {'total_credits': 9999, 'free_credits': 9999, 'paid_credits': 0}

    user_ref = db.collection('users').document(str(user_id))
    doc = user_ref.get()
    now = datetime.datetime.now(datetime.timezone.utc)

    if not doc.exists:
        initial_data = {
            'free_credits': max_credits,     # Bonificación semanal
            'paid_credits': 0,               # Saldo comprado
            'last_reset': now,
            'total_images_created': 0
        }
        user_ref.set(initial_data)
        initial_data['total_credits'] = max_credits
        return initial_data

    data = doc.to_dict()
    last_reset = data.get('last_reset')

    paid_credits = data.get('paid_credits', 0)
    free_credits = data.get('free_credits', 0)

    if last_reset and last_reset.tzinfo is None:
        last_reset = last_reset.replace(tzinfo=datetime.timezone.utc)

    # Lógica de reseteo semanal (7 días) - NO ACUMULATIVA
    if last_reset and (now - last_reset) > datetime.timedelta(days=7):

        # Restablece los créditos gratuitos al máximo semanal
        free_credits = max_credits 
        data['last_reset'] = now

        user_ref.update({
            'free_credits': free_credits, 
            'last_reset': now
        })

    # Devolvemos el saldo calculado para uso en handlers.py
    data['free_credits'] = free_credits
    data['paid_credits'] = paid_credits
    data['total_credits'] = free_credits + paid_credits

    return data

# --- Descontar Crédito (Primero Free, Luego Paid) ---
def decrement_credit(user_id: int):
    """Descuenta UN crédito. Lo descuenta primero de free_credits y luego de paid_credits."""
    db = get_firestore_client()
    if db is not None:
        user_ref = db.collection('users').document(str(user_id))

        @firestore.transactional
        def run_decrement(transaction):
            snapshot = user_ref.get(transaction=transaction)
            data = snapshot.to_dict()

            current_free = data.get('free_credits', 0)
            current_paid = data.get('paid_credits', 0)

            if current_free > 0:
                transaction.update(user_ref, {'free_credits': current_free - 1})
            elif current_paid > 0:
                transaction.update(user_ref, {'paid_credits': current_paid - 1})

        transaction = db.transaction()
        try:
            run_decrement(transaction)
        except Exception as e:
            logging.error(f"Error en decrement_credit: {e}")

# --- Registro de Uso (Tracking) ---
def record_image_usage(user_id: int, style: str, is_watermarked: bool):
    """
    Registra el uso de la imagen en el documento del usuario y en las estadísticas globales.
    """
    db = get_firestore_client()
    if db is None:
        logging.error("No se pudo obtener el cliente de Firestore para registrar el uso.")
        return

    try:
        stats_ref = db.collection('stats').document('usage_metrics')
        user_ref = db.collection('users').document(str(user_id))

        @firestore.transactional
        def update_usage_transaction(transaction):
            # A. Actualizar métricas globales (estilos, WM, total)
            stats_snapshot = stats_ref.get(transaction=transaction)
            stats_data = stats_snapshot.to_dict() or {}

            style_counts = stats_data.get('style_counts', {})
            style_counts[style] = style_counts.get(style, 0) + 1

            watermark_count = stats_data.get('watermark_count', 0)
            if is_watermarked:
                watermark_count += 1

            transaction.set(stats_ref, {
                'style_counts': style_counts,
                'total_images_processed': firestore.Increment(1),
                'watermark_count': watermark_count,
            }, merge=True)

            # B. Actualizar métricas del usuario (histórico y actividad)
            transaction.update(user_ref, {
                'total_images_created': firestore.Increment(1),
                'last_activity': datetime.datetime.now(datetime.timezone.utc)
            })

        transaction = db.transaction()
        update_usage_transaction(transaction)

        logging.info(f"Uso registrado: User {user_id}, Style {style}, WM: {is_watermarked}")

    except Exception as e:
        logging.error(f"Error al registrar el uso en Firestore: {e}")