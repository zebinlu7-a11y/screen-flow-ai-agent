"""
Doubao VL LangChain ChatModel wrapper using the OpenAI-compatible API.

Volcano Ark exposes an OpenAI-compatible chat completions endpoint, so this
module uses openai.OpenAI instead of the Ark runtime SDK. The public class name
is kept as ChatDoubaoVL to avoid changing the rest of the project.
"""
from typing import Any, Iterator, List, Mapping, Optional, Union

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ChatMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from openai import OpenAI
try:
    from langchain_core.pydantic_v1 import Field, PrivateAttr
except ImportError:
    from pydantic.v1 import Field, PrivateAttr

from config import ARK_API_KEY, ARK_BASE_URL, DOUBAO_MODEL_NAME


class ChatDoubaoVL(BaseChatModel):
    """
    Doubao multimodal chat model wrapper.

    Input/output follow the OpenAI chat completions format:
    - text blocks: {"type": "text", "text": "..."}
    - image blocks: {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
    """

    model_name: str = Field(default="")
    api_key: str = Field(default="")
    base_url: str = Field(default="")
    temperature: float = Field(default=0.7)

    _client: OpenAI = PrivateAttr()

    def __init__(self, **kwargs):
        from config import ARK_API_KEY, ARK_BASE_URL
        kwargs.setdefault("api_key", ARK_API_KEY)
        kwargs.setdefault("base_url", ARK_BASE_URL)
        kwargs.setdefault("model_name", "")
        kwargs.setdefault("temperature", 0.7)
        super().__init__(**kwargs)
        # Pydantic v1 compat fix: 确保所有 field 值是原生 Python 类型
        object.__setattr__(self, "temperature", float(kwargs["temperature"]))
        self._create_client()

    def _create_client(self):
        """Create or recreate the OpenAI-compatible client."""
        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
        )

    def reload_api_key(self, new_key: str):
        """Update API key and rebuild the client."""
        self.api_key = new_key
        self._create_client()

    @property
    def _llm_type(self) -> str:
        return "doubao-vl-openai-compatible"

    @property
    def _identifying_params(self) -> Mapping[str, Any]:
        return {
            "model_name": self.model_name or DOUBAO_MODEL_NAME,
            "base_url": self.base_url,
        }

    def _active_model(self) -> str:
        from config import DOUBAO_MODEL_NAME

        return self.model_name or DOUBAO_MODEL_NAME

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> ChatResult:
        """Non-streaming OpenAI-compatible chat completions call."""
        response = self._client.chat.completions.create(
            model=self._active_model(),
            messages=self._convert_messages_to_openai(messages),
            temperature=self.temperature,
            stop=stop,
        )

        text = ""
        if response.choices:
            text = response.choices[0].message.content or ""

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> Iterator[ChatGenerationChunk]:
        """Streaming OpenAI-compatible chat completions call."""
        stream = self._client.chat.completions.create(
            model=self._active_model(),
            messages=self._convert_messages_to_openai(messages),
            temperature=self.temperature,
            stop=stop,
            stream=True,
        )

        for event in stream:
            if not event.choices:
                continue
            delta = event.choices[0].delta.content or ""
            if not delta:
                continue
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=delta))
            if run_manager:
                run_manager.on_llm_new_token(delta, chunk=chunk)
            yield chunk

    def _convert_messages_to_openai(self, messages: List[BaseMessage]) -> List[dict]:
        openai_messages = []
        for msg in messages:
            content = self._convert_content_to_openai(msg.content)
            if content is None:
                continue
            openai_messages.append({
                "role": self._get_openai_role(msg),
                "content": content,
            })
        return openai_messages

    def _get_openai_role(self, message: BaseMessage) -> str:
        if isinstance(message, HumanMessage):
            return "user"
        if isinstance(message, AIMessage):
            return "assistant"
        if isinstance(message, SystemMessage):
            return "system"
        if isinstance(message, ChatMessage):
            return message.role
        return "user"

    def _convert_content_to_openai(
        self, content: Union[str, List[dict]]
    ) -> Optional[Union[str, List[dict]]]:
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return str(content)

        openai_content = []
        for block in content:
            if not isinstance(block, dict):
                continue

            block_type = block.get("type", "")
            if block_type == "text":
                openai_content.append({
                    "type": "text",
                    "text": block.get("text", ""),
                })
            elif block_type == "image_url":
                image_url_data = block.get("image_url", {})
                if isinstance(image_url_data, dict):
                    url = image_url_data.get("url", "")
                else:
                    url = str(image_url_data or "")
                if url:
                    openai_content.append({
                        "type": "image_url",
                        "image_url": {"url": url},
                    })

        return openai_content or None


def create_llm(streaming: bool = False) -> ChatDoubaoVL:
    """
    Create a ChatDoubaoVL instance.

    The streaming argument is kept for backwards compatibility. Use invoke()
    for non-streaming and stream()/astream() for streaming.
    """
    return ChatDoubaoVL()


def build_multimodal_message(text: str, image_base64: str = "",
                             image_base64_list: Optional[List[str]] = None) -> HumanMessage:
    """
    Build a multimodal HumanMessage containing text and one or more JPEG images.
    """
    content: List[dict] = []

    if text and text.strip():
        content.append({
            "type": "text",
            "text": text.strip(),
        })

    images = image_base64_list or []
    if not images and image_base64:
        images = [image_base64]

    for b64 in images:
        if b64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{b64}",
                },
            })

    if not content:
        content.append({
            "type": "text",
            "text": "请描述图片内容。",
        })

    return HumanMessage(content=content)


def build_text_message(text: str) -> HumanMessage:
    """Build a plain text HumanMessage."""
    return HumanMessage(content=text.strip())
