"""MAX message routing + user-friendly progress + Redis FSM."""

import logging
from typing import Any

from redis.asyncio import Redis

from bot.agent_client import AgentClient, AgentUnavailable
from bot.asr import SpeechRecognitionError, SpeechRecognizer
from bot.max_api import MaxAPI, MaxAPIError
from bot.state import StateStore

logger = logging.getLogger(__name__)

_HELP = (
    "Привет! Отправь текст или голосовое сообщение. Например: "
    "«Напомни завтра в 10 позвонить Ивану»."
)
_BUSY = "Ещё думаю над прошлым запросом. Попробуй через несколько секунд."
_AUDIO_EXT = (".ogg", ".oga", ".opus", ".mp3", ".m4a", ".wav", ".aac")


def _attachments(message: dict[str, Any]) -> list[dict[str, Any]]:
    body = message.get("body") or {}
    return [a for a in (body.get("attachments") or []) if isinstance(a, dict)]


def _is_audio_attachment(attachment: dict[str, Any]) -> bool:
    kind = attachment.get("type")
    if kind in {"audio", "voice"}:
        return True
    if kind != "file":
        return False
    payload = attachment.get("payload") or {}
    filename = str(
        payload.get("filename")
        or payload.get("name")
        or attachment.get("filename")
        or ""
    ).lower()
    mime = str(payload.get("mime_type") or payload.get("content_type") or "").lower()
    if filename:
        return filename.endswith(_AUDIO_EXT)
    if mime:
        return mime.startswith("audio/")
    # MAX can omit filename/mime in update. Treat an untyped file as
    # an audio candidate in this voice-focused bot; ASR will reject non-audio.
    return True


def extract_audio(message: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (MAX audio download URL, optional transcription)."""
    for attachment in _attachments(message):
        if not _is_audio_attachment(attachment):
            continue
        payload = attachment.get("payload") or {}
        text = payload.get("transcription") or attachment.get("transcription")
        url = payload.get("url") or attachment.get("url")
        return (
            url if isinstance(url, str) and url else None,
            text.strip() if isinstance(text, str) and text.strip() else None,
        )
    return None, None


def _audio_present(message: dict[str, Any]) -> bool:
    return any(_is_audio_attachment(a) for a in _attachments(message))


def _brief_attachment_diagnostics(message: dict[str, Any]) -> list[dict[str, Any]]:
    """No raw URL, token, transcription, or personal filename in logs."""
    return [
        {
            "type": a.get("type"),
            "url": bool((a.get("payload") or {}).get("url")),
            "filename": bool((a.get("payload") or {}).get("filename")),
            "transcription": bool((a.get("payload") or {}).get("transcription")),
        }
        for a in _attachments(message)
    ]


class BotHandler:
    def __init__(
        self,
        max_api: MaxAPI,
        asr: SpeechRecognizer,
        agent: AgentClient,
        state: StateStore,
        redis: Redis,
    ):
        self.max_api = max_api
        self.asr = asr
        self.agent = agent
        self.state = state
        self.redis = redis

    async def handle_update(self, update: dict[str, Any]) -> None:
        kind = update.get("update_type")
        if kind == "bot_started":
            user = update.get("user") or {}
            user_id = user.get("user_id")
            chat_id = update.get("chat_id")
            if chat_id is not None:
                await self.max_api.send(int(chat_id), _HELP)
            elif user_id is not None:
                await self.max_api.send(int(user_id), _HELP, to_user=True)
            return
        if kind != "message_created":
            return

        message = update.get("message") or {}
        sender = message.get("sender") or {}
        if sender.get("is_bot"):
            return
        user_id = sender.get("user_id")
        recipient = message.get("recipient") or {}
        chat_id = recipient.get("chat_id")
        # Private assistant: don't disclose task information in a group or channel.
        if recipient.get("chat_type") not in (None, "dialog"):
            return
        if user_id is None:
            return
        to_user = chat_id is None
        chat_id = int(user_id if to_user else chat_id)
        user_id = str(user_id)

        async def reply(message_text: str) -> None:
            await self.max_api.send(chat_id, message_text, to_user=to_user)

        mid = (message.get("body") or {}).get("mid")
        if mid is not None:
            is_new = await self.redis.set(f"bot:seen:{mid}", "1", nx=True, ex=86400)
            if not is_new:
                return

        text = ((message.get("body") or {}).get("text") or "").strip()
        if text in {"/start", "/help"}:
            await reply(_HELP)
            return
        logger.info("MAX incoming: text=%s attachments=%s", bool(text),
                    _brief_attachment_diagnostics(message))
        url, transcription = extract_audio(message)
        is_voice = _audio_present(message)

        # The MAX event may omit payload.url/filename (or all attachments).
        # The message endpoint can return richer details for this same mid.
        if not text and (not is_voice or not (url or transcription)) and mid:
            try:
                full_message = await self.max_api.get_message(str(mid))
                logger.info("MAX fetched message: attachments=%s",
                            _brief_attachment_diagnostics(full_message))
                fetched_url, fetched_transcription = extract_audio(full_message)
                if _audio_present(full_message):
                    is_voice = True
                    url = fetched_url or url
                    transcription = fetched_transcription or transcription
            except MaxAPIError as exc:
                logger.warning("MAX message details unavailable: %s", exc)

        if is_voice and not (url or transcription):
            logger.warning("Audio without URL/transcription, details=%s",
                           _brief_attachment_diagnostics(message))
            await reply("Аудиосообщение получено, но MAX не передал ссылку для распознавания. "
                        "Попробуй отправить голосовое ещё раз.")
            return
        if not text and not is_voice:
            await reply("Пришли текст или голосовое сообщение. "
                        "Для MP3 выбери отправку файла.")
            return

        request_id = await self.state.acquire(
            user_id, "recognizing" if is_voice else "thinking"
        )
        if request_id is None:
            await reply(_BUSY)
            return

        try:
            if is_voice:
                await reply("🎤 Голос получил, распознаю…")
                if transcription:
                    text = transcription
                else:
                    audio = await self.max_api.get_audio(url)
                    text = await self.asr.transcribe(audio)
                if not text.strip():
                    raise SpeechRecognitionError("Не получилось распознать речь")
                await self.state.transition(user_id, request_id)
                await reply(f"📝 Распознал: {text}\n⏳ Думаю…")
            else:
                await reply("⏳ Думаю над запросом…")

            result = await self.agent.ask(user_id=user_id, text=text)
            await reply(result.reply_text or "Агент не вернул ответ")
        except (SpeechRecognitionError, MaxAPIError) as exc:
            logger.warning("Audio problem for user %s: %s", user_id, exc)
            await reply(f"Не получилось обработать голос: {exc}. Попробуй ещё раз.")
        except AgentUnavailable:
            logger.exception("Agent request failed for user %s", user_id)
            await reply("Агент временно недоступен. Попробуй ещё раз позже.")
        finally:
            await self.state.release(user_id, request_id)
