from dataclasses import Field
import httpx
import logging
import os
from typing import Optional
from langchain_core.tools import tool
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Crear directorio de logs si no existe
log_dir = "logs"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Configurar el handler para archivo
file_handler = logging.FileHandler(os.path.join(log_dir, "whatsapp.log"))
file_handler.setLevel(logging.INFO)

# Configurar el formato del log
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)

# Agregar el handler al logger
logger.addHandler(file_handler)

# Asegúrate de tener estas variables de entorno configuradas
KOMMO_BASE_URL = os.getenv("KOMMO_BASE_URL")
KOMMO_ACCESS_TOKEN = os.getenv("KOMMO_ACCESS_TOKEN")

logger.info(f"Kommo URL: {KOMMO_BASE_URL}")
logger.info(f"Kommo Access Token: {KOMMO_ACCESS_TOKEN}")

class CreateOrUpdateKommoLeadInput(BaseModel):
    user_name: str = Field(description="El nombre del usuario interesado en el seminario.")
    session_id: str = Field(description="Un identificador único para la sesión actual del usuario, que es el número de teléfono del usuario (ej. '525512345678'). **El modelo DEBE tomar este valor de la variable de contexto 'session_id' proporcionada por el sistema y NUNCA debe inventarlo ni usar la cadena literal 'session_id'.**")

@tool(args_schema=CreateOrUpdateKommoLeadInput)
async def create_or_update_kommo_lead(
    user_name: str,
    session_id: Optional[str] = None, 
    current_status_message: str = "Interés en seminario confirmado por AI Agent.",
    new_stage_id: Optional[int] = None # Para cambiar la etapa del lead
) -> str:
    """
    Crea un lead en Kommo con el estado actual del usuario.
    Si el lead no existe, intenta crearlo. Si existe, lo actualiza y agrega una nota.

    **USA ESTA HERRAMIENTA SÓLO SI EL USUARIO EXPRESA UN INTERÉS CLARO EN COMPRAR, INSCRIBIRSE O RECIBIR INFORMACIÓN COMERCIAL ESPECÍFICA SOBRE EL SEMINARIO O PRODUCTOS DE ALLEN CARR'S EASYWAY.**
    **NO USES ESTA HERRAMIENTA SI EL USUARIO SÓLO PREGUNTA SOBRE EL FUNCIONAMIENTO GENERAL DEL MÉTODO, HISTORIAS DE ÉXITO O INFORMACIÓN NO COMERCIAL.**
    
    Args:
        session_id (str): Un identificador único para la sesión actual del usuario, que es el número de teléfono del usuario (ej. '525512345678'). **El modelo DEBE tomar este valor de la variable de contexto 'session_id' proporcionada por el sistema y NUNCA debe inventarlo ni usar la cadena literal 'session_id'.**
        user_name (str, optional): El nombre del usuario interesado en el seminario, si se conoce.
        current_status_message (str): Un mensaje descriptivo del estado actual para la nota.
        new_stage_id (int, optional): El ID de la etapa a la que mover el lead en Kommo.
                                      Usa los IDs definidos como KOMMO_STAGE_...
    Returns:
        str: Un mensaje de confirmación o error de la operación.
    """
    headers = {
        "Authorization": f"Bearer {KOMMO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    if not KOMMO_BASE_URL or not KOMMO_ACCESS_TOKEN:
        logger.error("Credenciales de Kommo no configuradas.")
        return "Error: Integración con Kommo no configurada."

    async with httpx.AsyncClient(base_url=KOMMO_BASE_URL) as client:
        lead_id = None
                
        try:
            # Primero, intentar crear un lead con el nombre y teléfono
            # Kommo requiere contacts para leads.
            # contact_data = {
            #     "name": user_name if user_name else f"Cliente WhatsApp {session_id}",
            #     "custom_fields_values": [
            #         {
            #             "field_id": YOUR_PHONE_CUSTOM_FIELD_ID_IN_KOMMO, # Reemplaza con el ID del campo de teléfono en Kommo
            #             "values": [ {"value": session_id, "enum_id": YOUR_WHATSAPP_ENUM_ID_FOR_PHONE_FIELD} ] # ID del tipo 'WhatsApp' si tienes uno
            #         }
            #     ]
            # }
            contact_data = {
                "name": user_name
            }

            # Luego crear el lead asociado
            # lead_creation_data = {
            #     "name": f"Interesado en Seminario - {user_name if user_name else session_id}",
            #     "pipeline_id": YOUR_PIPELINE_ID,
            #     "status_id": new_stage_id if new_stage_id else KOMMO_STAGE_INTEREST_CONFIRMED_ID,
            #     # "contact_id": obtained_contact_id # Asociar al contacto creado/existente
            #     # Si el teléfono es un campo del lead directamente, agrégalo al payload
            #     # "custom_fields_values": [...] si el teléfono es un custom field de lead
            # }
            lead_creation_data = {
                "name": user_name,
            }

            logger.info(f"User name: {user_name}")
            logger.info(f"Session ID: {session_id}")

            create_response = await client.post("/leads", headers=headers, json=[lead_creation_data])
            create_response.raise_for_status()
            lead_id = create_response.json()["_embedded"]["leads"][0]["id"]
            logger.info(f"Lead creado en Kommo para {user_name} con ID: {session_id}")
            # Aquí deberías guardar lead_id en tu DB local asociado al session_id
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400 and "already exists" in e.response.text: # Esto es simplificado, Kommo tiene códigos específicos
                logger.warning(f"Lead para {session_id} probablemente ya existe. Intentando actualizar.")
                # Lógica para buscar el lead existente y actualizarlo
                # Esto es lo más difícil sin un mapeo en tu DB. Asume que sabes cómo obtener lead_id.
                # lead_id = get_lead_id_from_your_db(session_id)
                
                # Si no puedes obtener el lead_id de tu DB, esta herramienta puede ser más compleja
                # para manejar la desduplicación o solo agregar notas a leads conocidos.
                return "Advertencia: Lead probablemente ya existe en Kommo. Contacta al administrador."
            else:
                logger.error(f"Error al crear/actualizar lead en Kommo para {session_id}: {e.response.status_code} - {e.response.text}")
                return f"Error al interactuar con Kommo: {e.response.status_code}"
        except Exception as e:
            logger.error(f"Error inesperado en la herramienta Kommo: {e}")
            return f"Error inesperado al interactuar con Kommo: {e}"

    # Si se llegó aquí, lead_id es válido (creado o asumido)
    # Agregar nota al lead
    note_data = {
        "note_type": "common",
        "element_id": lead_id,
        "element_type": "lead",
        "text": f"Interacción del AI Agent: {current_status_message}"
    }
    try:
        add_note_response = await client.post(f"/api/v4/leads/{lead_id}/notes", headers=headers, json=[note_data])
        add_note_response.raise_for_status()
        logger.info(f"Nota '{current_status_message}' agregada al lead {lead_id} en Kommo.")
        return "Lead actualizado exitosamente en Kommo."
    except httpx.HTTPStatusError as e:
        logger.error(f"Error al agregar nota a Kommo para lead {lead_id}: {e.response.status_code} - {e.response.text}")
        return f"Error al agregar nota en Kommo: {e.response.status_code}"
    except Exception as e:
        logger.error(f"Error inesperado al agregar nota en Kommo: {e}")
        return f"Error inesperado al agregar nota en Kommo: {e}"

# Esto es lo que se importará y se pasará al modelo
kommo_integration_tool = create_or_update_kommo_lead