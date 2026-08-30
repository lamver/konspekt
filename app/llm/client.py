"""Клиент к OpenAI-совместимому API.

Через httpx, без sdk от OpenAI: тот тянет в билд десятки мегабайт
зависимостей ради нескольких запросов.

Тонкость про потоковый ответ: сервер шлёт строки `data: {...}`, и
последней приходит `data: [DONE]`. Разбираем руками, это проще любой
библиотеки.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import httpx

log = logging.getLogger(__name__)

Message = dict[str, str]

# Ответ модели ждём долго: на слабой машине саммари часовой встречи
# считается минутами. Но соединение должно устанавливаться быстро,
# иначе неверный адрес в настройках вешает окно на минуту.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 600.0


class LlmError(Exception):
    """Понятная человеку ошибка обращения к модели.

    Всё, что летит из клиента наружу, обёрнуто в неё: интерфейсу нужен
    текст для показа, а не стектрейс httpx.
    """


@dataclass
class LlmClient:
    """Один провайдер: адрес, ключ, модель.

    `base_url` указывает на корень API, то есть на то, что стоит перед
    `/chat/completions`. Для llama.cpp это `http://127.0.0.1:8080/v1`.
    """

    base_url: str
    api_key: str = ""
    model: str = ""
    temperature: float = 0.3

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _payload(self, messages: list[Message], stream: bool, **extra: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
        }
        body.update(extra)
        return body

    # --- проверка -------------------------------------------------------

    def check(self) -> dict[str, Any]:
        """Проверить, что сервер жив и модель отвечает.

        Сначала спрашиваем список моделей: это дёшево и сразу ловит
        неверный адрес или ключ. Если списка нет, а такое бывает у
        самодельных серверов, пробуем короткий запрос к самой модели.
        """
        try:
            with httpx.Client(timeout=CONNECT_TIMEOUT) as client:
                resp = client.get(self._url("models"), headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json().get("data") or []
                    names = [m.get("id", "") for m in data if isinstance(m, dict)]
                    return {"ok": True, "models": names}
                if resp.status_code in (401, 403):
                    return {"ok": False, "error": "Сервер не принял ключ"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": _network_error(exc)}

        # Списка моделей нет, спросим саму модель.
        try:
            self.complete([{"role": "user", "content": "ping"}], max_tokens=1)
            return {"ok": True, "models": []}
        except LlmError as exc:
            return {"ok": False, "error": str(exc)}

    # --- запросы --------------------------------------------------------

    def complete(self, messages: list[Message], **extra: Any) -> str:
        """Ответ целиком. Для коротких задач вроде заголовка встречи."""
        try:
            with httpx.Client(timeout=httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)) as client:
                resp = client.post(
                    self._url("chat/completions"),
                    headers=self._headers(),
                    json=self._payload(messages, stream=False, **extra),
                )
                _raise_for_status(resp)
                return _first_message(resp.json())
        except httpx.HTTPError as exc:
            raise LlmError(_network_error(exc)) from exc

    def stream(
        self,
        messages: list[Message],
        on_chunk: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        **extra: Any,
    ) -> str:
        """Ответ по кускам. Возвращает собранный текст целиком.

        `should_stop` позволяет прервать генерацию: пользователь закрыл
        встречу или нажал «Остановить», и дожидаться конца незачем.
        """
        parts: list[str] = []
        try:
            timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
            with httpx.Client(timeout=timeout) as client:
                with client.stream(
                    "POST",
                    self._url("chat/completions"),
                    headers=self._headers(),
                    json=self._payload(messages, stream=True, **extra),
                ) as resp:
                    _raise_for_stream_status(resp)
                    for piece in _iter_stream(resp):
                        if should_stop is not None and should_stop():
                            break
                        parts.append(piece)
                        if on_chunk is not None:
                            on_chunk(piece)
        except httpx.HTTPError as exc:
            # Уже полученное не выбрасываем: половина саммари лучше,
            # чем пустота, если сервер оборвал соединение на середине.
            if parts:
                log.warning("Поток оборвался, отдаём накопленное: %s", exc)
                return "".join(parts)
            raise LlmError(_network_error(exc)) from exc
        return "".join(parts)


def _iter_stream(resp: httpx.Response) -> Iterator[str]:
    for line in resp.iter_lines():
        if not line or not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            text = delta.get("content")
            if text:
                yield text


def _first_message(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise LlmError("Модель вернула пустой ответ")
    message = choices[0].get("message") or {}
    return message.get("content") or ""


def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    raise LlmError(_http_error(resp.status_code, resp.text))


def _raise_for_stream_status(resp: httpx.Response) -> None:
    if resp.status_code == 200:
        return
    # В потоковом режиме тело ещё не прочитано, а без него текст ошибки
    # пустой и человеку непонятно, что случилось.
    resp.read()
    raise LlmError(_http_error(resp.status_code, resp.text))


def _http_error(status: int, body: str) -> str:
    detail = ""
    try:
        data = json.loads(body)
        error = data.get("error")
        if isinstance(error, dict):
            detail = error.get("message") or ""
        elif isinstance(error, str):
            detail = error
    except Exception:
        detail = (body or "")[:200]

    if status in (401, 403):
        return "Сервер не принял ключ доступа"
    if status == 404:
        return "Модель или адрес не найдены, проверьте настройки"
    if status == 429:
        return "Слишком много запросов, попробуйте позже"
    if status >= 500:
        return f"Сервер модели ответил ошибкой {status}"
    return detail or f"Ошибка запроса к модели ({status})"


def _network_error(exc: Exception) -> str:
    if isinstance(exc, httpx.ConnectError):
        return "Не удалось подключиться к серверу модели"
    if isinstance(exc, httpx.ConnectTimeout):
        return "Сервер модели не отвечает"
    if isinstance(exc, httpx.ReadTimeout):
        return "Модель слишком долго думает"
    return f"Ошибка связи с моделью: {exc}"
