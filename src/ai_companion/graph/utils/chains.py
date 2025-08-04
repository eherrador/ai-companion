from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from pydantic import BaseModel, Field

from ai_companion.core.prompts import ALLEN_CARR_SELLER_PROMPT, ROUTER_PROMPT # CHARACTER_CARD_PROMPT
from ai_companion.graph.utils.helpers import AsteriskRemovalParser, get_chat_model

from ai_companion.graph.utils.tools import create_or_update_kommo_lead
from langchain_core.tools import Tool 

class RouterResponse(BaseModel):
    response_type: str = Field(
        description="The response type to give to the user. It must be one of: 'conversation', 'image' or 'audio'"
    )


def get_router_chain():
    model = get_chat_model(temperature=0.3).with_structured_output(RouterResponse)

    prompt = ChatPromptTemplate.from_messages(
        [("system", ROUTER_PROMPT), MessagesPlaceholder(variable_name="messages")]
    )

    return prompt | model


def get_character_response_chain(summary: str = ""):
    model = get_chat_model()

    # Crea una lista de herramientas que tu modelo puede usar
    # Necesitas envolver tu función @tool en una instancia de Tool
    # Esto es importante para que LangChain/LangGraph sepa cómo pasarla al modelo
    # tools = [
    #     Tool(
    #         name="create_or_update_kommo_lead",
    #         description=(
    #             "Usa esta herramienta para registrar o actualizar el progreso de un usuario en el sistema CRM Kommo. "
    #             "Invocala tan pronto como el usuario exprese un interés claro y explícito en inscribirse "
    #             "al seminario (por ejemplo, cuando responda 'sí' o una afirmación similar a tu pregunta de inscripción). "
    #             "Parámetros: user_name (nombre del usuario, obligatorio), session_id (número de teléfono del usuario, opcional)."
    #             "No necesitas especificar 'current_status_message' ni 'new_stage_id' a menos que sea una actualización muy específica."
    #         ),
    #         func=create_or_update_kommo_lead # Pasa la función que has decorado con @tool
    #     )
    # ]
    tools = []

    # Bindea las herramientas al modelo. Esto le dice al modelo que puede generar Tool Calls.
    model_with_tools = model.bind_tools(tools)
    
    system_message = ALLEN_CARR_SELLER_PROMPT # CHARACTER_CARD_PROMPT

    # Formatear las herramientas para el prompt
    formatted_tools = "\n".join([f"{tool.name}: {tool.description}" for tool in tools])
    formatted_tool_names = ", ".join([tool.name for tool in tools])

    if summary:
        system_message_content += f"\n\nSummary of conversation earlier between Allen Carr seller and the user: {summary}"

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", system_message),
            MessagesPlaceholder(variable_name="messages"),
        ]
    )

    # Crea el prompt con las variables de herramientas pre-formateadas
    # Esto asume que el ALLEN_CARR_SELLER_PROMPT tiene los placeholders {tools} y {tool_names}
    final_prompt = prompt.partial(
        tools=formatted_tools,
        tool_names=formatted_tool_names
    )

    # return prompt | model_with_tools | AsteriskRemovalParser()
    return final_prompt | model_with_tools | AsteriskRemovalParser()
