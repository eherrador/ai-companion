import os
from uuid import uuid4
import re
import logging

from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage
from langchain_core.runnables import RunnableConfig

from ai_companion.graph.state import AICompanionState
from ai_companion.graph.utils.chains import (
    get_character_response_chain,
    get_router_chain,
)
from ai_companion.graph.utils.helpers import (
    get_chat_model,
    get_text_to_image_module,
    get_text_to_speech_module,
)
from ai_companion.modules.memory.long_term.memory_manager import get_memory_manager
from ai_companion.modules.schedules.context_generation import ScheduleContextGenerator
from ai_companion.settings import settings

from typing import (
    Any,
    Dict,
)

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

async def router_node(state: AICompanionState):
    chain = get_router_chain()
    response = await chain.ainvoke({"messages": state["messages"][-settings.ROUTER_MESSAGES_TO_ANALYZE :]})
    logger.info(f"RouterNode: Workflow decidido: {response.response_type}")
    return {"workflow": response.response_type}


def context_injection_node(state: AICompanionState):
    schedule_context = ScheduleContextGenerator.get_current_activity()
    if schedule_context != state.get("current_activity", ""):
        apply_activity = True
    else:
        apply_activity = False
    return {"apply_activity": apply_activity, "current_activity": schedule_context}


async def conversation_node(state: AICompanionState, config: RunnableConfig):
    logger.info("conversation_node: Iniciando ejecución.")

    current_activity = ScheduleContextGenerator.get_current_activity()
    memory_context = state.get("memory_context", "")

    logger.info("Conversation_node: current_activity: %s", current_activity)
    logger.info("Conversation_node: memory_context: %s", memory_context)

    user_name = state.get("user_name", "Invitado") # Proporciona un valor por defecto si no se encuentra
    logger.info(f"conversation_node: User Name extraído: {user_name}")

    try:
        chain = get_character_response_chain(state.get("summary", ""))
        logger.info("conversation_node: Cadena de conversación obtenida.")

        logger.info(f"ConversationNode: Llamando a la cadena de respuesta del personaje con el resumen: {state.get('summary', '')}")

        session_id_from_state = state.get("session_id", config.get("configurable", {}).get("thread_id", "NO_SESSION_ID_FOUND"))
        logger.info(f"ConversationNode: Session ID obtenido del estado: {session_id_from_state}")

        input_for_chain = {
            "messages": state["messages"],
            "current_activity": current_activity,
            "memory_context": memory_context,
            "user_name": user_name,
            "session_id": session_id_from_state,
        }
        logger.info(f"ConversationNode: Input completo para chain.ainvoke: {input_for_chain}")

        response = await chain.ainvoke(input_for_chain, config)

        # response = await chain.ainvoke(
        #     {
        #         "messages": state["messages"],
        #         "current_activity": current_activity,
        #         "memory_context": memory_context,
        #         "user_name": user_name, # <-- ¡PASA user_name AQUÍ!
        #         "session_id": session_id_from_state,
        #     },
        #     config,
        # )

        if isinstance(response, AIMessage):
            # If the response is an AIMessage, return it directly
            return {"messages": response}
        elif isinstance(response, str):
            # If the response is a string, wrap it in an AIMessage
            response = AIMessage(content=response)
            return {"messages": response}
        else:
            # If the response is neither, raise an error or handle it accordingly
            raise ValueError("Unexpected response type from the character response chain.")
    
    except Exception as e:
        logger.error(f"conversation_node: ¡ERROR INESPERADO!: {e}", exc_info=True)
        # Podrías decidir retornar un mensaje de error o re-lanzar
        raise # Vuelve a lanzar la excepción para que el grafo la capture


async def image_node(state: AICompanionState, config: RunnableConfig):
    current_activity = ScheduleContextGenerator.get_current_activity()
    memory_context = state.get("memory_context", "")

    chain = get_character_response_chain(state.get("summary", ""))
    text_to_image_module = get_text_to_image_module()

    scenario = await text_to_image_module.create_scenario(state["messages"][-5:])
    os.makedirs("generated_images", exist_ok=True)
    img_path = f"generated_images/image_{str(uuid4())}.png"
    await text_to_image_module.generate_image(scenario.image_prompt, img_path)

    # Inject the image prompt information as an AI message
    scenario_message = HumanMessage(content=f"<image attached by Ava generated from prompt: {scenario.image_prompt}>")
    updated_messages = state["messages"] + [scenario_message]

    response = await chain.ainvoke(
        {
            "messages": updated_messages,
            "current_activity": current_activity,
            "memory_context": memory_context,
        },
        config,
    )

    return {"messages": AIMessage(content=response), "image_path": img_path}


async def audio_node(state: AICompanionState, config: RunnableConfig):
    current_activity = ScheduleContextGenerator.get_current_activity()
    memory_context = state.get("memory_context", "")

    chain = get_character_response_chain(state.get("summary", ""))
    text_to_speech_module = get_text_to_speech_module()

    response = await chain.ainvoke(
        {
            "messages": state["messages"],
            "current_activity": current_activity,
            "memory_context": memory_context,
        },
        config,
    )
    output_audio = await text_to_speech_module.synthesize(response)

    return {"messages": response, "audio_buffer": output_audio}


async def summarize_conversation_node(state: AICompanionState):
    model = get_chat_model()
    summary = state.get("summary", "")

    if summary:
        summary_message = (
            f"This is summary of the conversation to date between Allen Carr seller and the user: {summary}\n\n"
            "Extend the summary by taking into account the new messages above:"
        )
    else:
        summary_message = (
            "Create a summary of the conversation above between Allen Carr seller and the user. "
            "The summary must be a short description of the conversation so far, "
            "but that captures all the relevant information shared between Allen Carr seller and the user:"
        )

    messages = state["messages"] + [HumanMessage(content=summary_message)]
    response = await model.ainvoke(messages)

    delete_messages = [RemoveMessage(id=m.id) for m in state["messages"][: -settings.TOTAL_MESSAGES_AFTER_SUMMARY]]
    return {"summary": response.content, "messages": delete_messages}


async def memory_extraction_node(state: AICompanionState, config: Dict[str, Any]):
    """Extract and store important information from the last message."""
    if not state["messages"]:
        return {}

    session_id = config.get("configurable", {}).get("thread_id")
    logger.info(f"nodes.py memory_extraction_node (function) -> session_id: {session_id}")
    logger.info(f"nodes.py memory_extraction_node (function) -> state: {state}")
    if not session_id:
        logger.warning("Session ID (thread_id) no encontrado en la configuración del nodo de extracción de memoria.")
        return {}
    
    memory_manager = get_memory_manager()
    last_message = state["messages"][-1]
    logger.info(f"Último mensaje recibido: {last_message.content}")

    # --- Nueva lógica para extraer y almacenar el nombre del usuario ---
    user_message_content = last_message.content.lower()
    extracted_user_name = state.get("user_name") # Intentar obtener el nombre si ya existe en el estado

    # Si aún no tenemos el nombre del usuario en el estado
    if not extracted_user_name:
        # Patrones comunes para detectar el nombre. Esto puede ser rudimentario,
        # pero es un buen punto de partida.
        # Considera usar un LLM ligero o un parser más sofisticado para esto si la complejidad aumenta.
        name_match = re.search(r"(?:me llamo|mi nombre es)\s+([a-zA-ZáéíóúÁÉÍÓÚñÑüÜ\s]+?)(?:[.,;!]\s*|$)", user_message_content, re.IGNORECASE)
        if name_match:
            # Captura el grupo que contiene el nombre
            name = name_match.group(1).strip().title() # Capitaliza cada palabra
            # Ignorar nombres comunes que podrían ser respuestas a preguntas genéricas (ej. "nada", "no")
            if name.lower() not in ["nada", "no", "no sé", "gracias", "y fumo", "y fuma"]:
                extracted_user_name = name
                logger.info(f"Nombre de usuario '{extracted_user_name}' extraído para {session_id}")
            else:
                logger.debug(f"Ignorado posible nombre de usuario '{name}' como respuesta genérica o ruido.")
        # Un patrón alternativo para "Hola, soy [Nombre]" o similar
        if not extracted_user_name: # Solo si el primer regex no encontró nada
            hola_soy_match = re.search(r"hola,\s*soy\s+([a-zA-ZáéíóúÁÉÍÓÚñÑüÜ\s]+?)(?:[.,;!]\s*|$)", user_message_content, re.IGNORECASE)
            if hola_soy_match:
                name = hola_soy_match.group(1).strip().title()
                if name.lower() not in ["nada", "no", "no sé", "gracias", "y fumo", "y fuma"]:
                    extracted_user_name = name
                    logger.info(f"Nombre de usuario '{extracted_user_name}' extraído (Hola, soy...) para {session_id}")
                else:
                    logger.debug(f"Ignorado posible nombre de usuario '{name}' como respuesta genérica o ruido.")


    # Retorna el nombre del usuario y el session_id para que se actualice el estado
    # Es crucial que memory_extraction_node retorne el `session_id` para que esté en el estado global.
    return_values = {}
    if extracted_user_name:
        return_values["user_name"] = extracted_user_name
    elif state.get("user_name"): # Asegurarse de que el nombre existente se mantenga si no se actualizó
        return_values["user_name"] = state.get("user_name")

    # Extraer y almacenar memorias en long-term memory
    await memory_manager.extract_and_store_memories(last_message, session_id)
    
    # Siempre asegúrate de que session_id se propague al estado si lo necesitas en otros nodos
    if session_id:
        return_values["session_id"] = session_id
    else:
        logger.warning("Session ID no disponible en memory_extraction_node para retorno.")

    # Si el `memory_context` es generado por `memory_extraction_node`, debe ser una cadena relevante.
    # Si `extract_and_store_memories` es la que genera el `memory_context`, revisa su implementación.
    # Por ahora, no estoy viendo dónde se crea el `memory_context` en tu `memory_extraction_node` para que devuelva un string largo.
    # Asumiré que el `memory_manager.extract_and_store_memories` actualiza la memoria a largo plazo,
    # y `memory_injection_node` es quien recupera y formatea el `memory_context`.
    return return_values

def memory_injection_node(state: AICompanionState, config: Dict[str, Any]):
    """Retrieve and inject relevant memories into the Allen Carr seller card."""

    logger.info("memory_injection_node: Iniciando ejecución.")
    if not state["messages"]:
        logger.warning("memory_injection_node: No hay mensajes en el estado. No se puede inyectar memoria.")
        return {}
    logger.info(f"memory_injection_node: Estado actual: {state}")

    session_id = config.get("configurable", {}).get("thread_id")
    if not session_id:
        logger.warning("Session ID no encontrado en memory_injection_node. No se inyectará contexto de memoria.")
        return state
    
    memory_manager = get_memory_manager()

    # Usa el último mensaje del usuario como contexto para buscar memorias relevantes.
    # Esto es mejor que usar todo el historial, ya que queremos memorias relevantes al *último* turno.
    # Si no hay mensajes, o el último no es un HumanMessage, no busques memorias relevantes.
    last_human_message_content = None
    if state["messages"] and state["messages"][-1].type == "human":
        last_human_message_content = state["messages"][-1].content
    
    retrieved_memories = []
    if last_human_message_content:
        # Aquí se recuperan las memorias. La calidad de estas depende de _analyze_memory y extract_and_store_memories.
        retrieved_memories = memory_manager.get_relevant_memories(
            last_human_message_content,
            session_id
        )
    
    # Formatea las memorias recuperadas como una cadena para el prompt.
    # Esta función (format_memories_for_prompt) debe ser tu MemoryManager.
    formatted_memory_context = memory_manager.format_memories_for_prompt(retrieved_memories)

    if formatted_memory_context:
        logger.info(f"memory_injection_node: Contexto de memoria inyectado (parcial): {formatted_memory_context[:200]}...") # Log parcial
        return {"memory_context": formatted_memory_context}
    else:
        logger.info("memory_injection_node: No se recuperaron memorias relevantes para inyectar.")
        return {"memory_context": ""} # Retorna una cadena vacía si no hay memorias