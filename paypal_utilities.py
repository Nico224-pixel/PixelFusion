import httpx
import base64
import json
import logging
from typing import Dict, Any, Optional

# IMPORTANTE: Usar SANDBOX para pruebas. Cambiar a 'https://api-m.paypal.com' para producción.
PAYPAL_API_BASE: str = 'https://api-m.sandbox.paypal.com'

# Variable global para cachear el Access Token
_ACCESS_TOKEN: Optional[str] = None

async def get_paypal_access_token(client_id: str, client_secret: str) -> Optional[str]:
    """
    Obtiene y cachea el token de acceso de PayPal.
    """
    global _ACCESS_TOKEN
    # Simple cache, sin verificar expiración (token dura varias horas)
    if _ACCESS_TOKEN:
        return _ACCESS_TOKEN
        
    logging.info("Attempting to fetch new PayPal Access Token...")

    # Codificación de las credenciales para la cabecera Basic Auth
    auth_string = f"{client_id}:{client_secret}"
    auth_header = base64.b64encode(auth_string.encode()).decode()

    headers = {
        'Accept': 'application/json',
        'Accept-Language': 'en_US',
        'Authorization': f'Basic {auth_header}'
    }
    data = {
        'grant_type': 'client_credentials'
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f'{PAYPAL_API_BASE}/v1/oauth2/token',
                headers=headers,
                data=data
            )
            response.raise_for_status() # Lanza error si el status es 4xx/5xx
            
            result = response.json()
            _ACCESS_TOKEN = result.get('access_token')
            logging.info("Successfully fetched and cached new PayPal Access Token.")
            return _ACCESS_TOKEN

    except httpx.HTTPStatusError as e:
        logging.error(f"PayPal Auth HTTP Error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"General error during PayPal authentication: {e}")
        return None


async def create_paypal_order(
    token: str,
    amount: str,
    description: str,
    return_url: str,
    cancel_url: str
) -> Optional[Dict[str, Any]]:
    """
    Crea una orden de pago estándar de PayPal y retorna el objeto de la orden.
    """
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}'
    }

    # Estructura de la Orden de PayPal
    order_data = {
        "intent": "CAPTURE",
        "purchase_units": [
            {
                "reference_id": description, # Referencia interna
                "description": f"Pixelation Bot Credits ({description})",
                "amount": {
                    "currency_code": "USD",
                    "value": amount
                }
            }
        ],
        "application_context": {
            "return_url": return_url,
            "cancel_url": cancel_url,
            "user_action": "PAY_NOW",
            "shipping_preference": "NO_SHIPPING"
        }
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                f'{PAYPAL_API_BASE}/v2/checkout/orders',
                headers=headers,
                json=order_data
            )
            response.raise_for_status() 
            return response.json()

    except httpx.HTTPStatusError as e:
        logging.error(f"PayPal Order Creation HTTP Error: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        logging.error(f"General error during PayPal order creation: {e}")
        return None
