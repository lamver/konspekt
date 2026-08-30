"""Работа с языковыми моделями.

Один протокол на всех: и наш сервер, и чужой провайдер, и локальная
модель через llama.cpp говорят в формате OpenAI. Поэтому клиент здесь
ровно один, а различия сводятся к адресу и ключу.
"""

from .client import LlmClient, LlmError, Message
from .manager import LlmManager
from .prompts import summary_messages, chat_messages, title_messages

__all__ = [
    "LlmClient",
    "LlmError",
    "LlmManager",
    "Message",
    "summary_messages",
    "chat_messages",
    "title_messages",
]
