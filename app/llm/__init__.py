"""Работа с языковыми моделями.

Один протокол на всех: и наш сервер, и чужой провайдер, и локальная
модель через llama.cpp говорят в формате OpenAI. Поэтому клиент здесь
ровно один, а различия сводятся к адресу и ключу.
"""

from .client import LlmClient, LlmError, Message
from .prompts import summary_messages, chat_messages

__all__ = [
    "LlmClient",
    "LlmError",
    "Message",
    "summary_messages",
    "chat_messages",
]
