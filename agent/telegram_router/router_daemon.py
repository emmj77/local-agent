"""Router Telegram — démon qui écoute Telegram et route vers les agents via Redis.

R9: Un fichier = une fonction métier (ici: pont Telegram ↔ agents via Redis).
R5: Hard crash si config invalide. Pas de masquage.
R8b: agent_id dans les messages.
R19: Redis remplace ZMQ — LPUSH queue + SUBSCRIBE events.

Architecture:
  Telegram API (long polling)
      v
  router_daemon.py
      |-- /main  -> Redis LPUSH agent:agent1:queue + SUBSCRIBE agent:agent1:events
      |-- /bis   -> Redis LPUSH agent:agent2:queue + SUBSCRIBE agent:agent2:events
      |-- /ds    -> Redis LPUSH agent:agent3:queue + SUBSCRIBE agent:agent3:events
      |-- /local -> Redis LPUSH agent:agentlocal:queue + SUBSCRIBE agent:agentlocal:events
      |-- /free  -> Redis LPUSH agent:subagent1:queue + SUBSCRIBE agent:subagent1:events
      |-- /paid  -> Redis LPUSH agent:subagent2:queue + SUBSCRIBE agent:subagent2:events
      |-- /status -> réponse interne
      `-- (default) -> agent1

Usage:
  python router_daemon.py  (ou via honcho/Procfile)
"""

import os
import sys
import json
import uuid
import asyncio
import signal
from pathlib import Path

# --- Chemins d'import ---
_ROUTER_DIR = Path(__file__).parent
_AGENT_DIR = _ROUTER_DIR.parent
sys.path.insert(0, str(_AGENT_DIR))
sys.path.insert(0, str(_ROUTER_DIR))

import redis
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, FSInputFile
from aiogram.enums import ParseMode, ChatAction
from aiogram.filters import Command
from dotenv import load_dotenv
from telegramify_markdown import markdownify
from slash_commands_map import (
    resolve_agent, extract_prompt, is_internal_command,
    SLASH_MAP, DEFAULT_AGENT, AGENTS_TG, AGENT_BY_KEY, MODELS_TG,
)
from voice import transcribe, synthesize_ogg

from config import GlobalConfig
from security.zombie_killer import install_hooks

AGENT_ID = "07_telegram_router"

# --- Chargement .env (racine, valeurs réelles — PAS telegram_router/.env qui a des placeholders) ---
load_dotenv(_AGENT_DIR.parent / ".env")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ALLOWED_CHAT_IDS = set(
    filter(None, os.environ.get("TELEGRAM_CHAT_ID", "").split(","))
)

# --- Redis client global (R19) — depuis config_global.yaml ---
_gc = GlobalConfig.load(str(_AGENT_DIR / "configs/config_global.yaml"))
_redis_client = redis.Redis(
    host=_gc.env.redis_host,
    port=_gc.env.redis_port,
    db=_gc.env.redis_db,
    decode_responses=True,
)

# --- Rate limit pour edit_message_text ---
_EDIT_INTERVAL = 1.5  # secondes

# Phase I: Installer les hooks de sécurité
install_hooks()

# --- Stop flag ---
_stop_flag = {"stop": False}


def _format_tool_message(tool_name: str, tool_args: dict) -> str:
    args_str = json.dumps(tool_args, ensure_ascii=False)
    if len(args_str) > 200:
        args_str = args_str[:200] + "..."
    return f"⚙️ {tool_name}\n📋 {args_str}"


def _format_tool_result(tool_name: str, output: str, success: bool) -> str:
    emoji = "✅" if success else "❌"
    display = output[:300] + "..." if len(output) > 300 else output
    return f"{emoji} {tool_name}\n```\n{display}\n```"


async def _send_to_agent(bot: Bot, chat_id: str, message: Message, agent_key: str, prompt: str) -> str:
    """Envoie un prompt à un agent via Redis LPUSH + écoute les events via SUBSCRIBE.

    R19: Redis remplace ZMQ. LPUSH sur la queue, SUBSCRIBE sur le channel events.
    """
    correlation_id = str(uuid.uuid4())
    queue_key = f"agent:{agent_key}:queue"
    events_channel = f"agent:{agent_key}:events"
    response_key = f"agent:response:{correlation_id}"

    # 1. Envoyer la requête via LPUSH
    request = {
        "prompt": prompt,
        "source": "telegram",
        "correlation_id": correlation_id,
    }
    _redis_client.lpush(queue_key, json.dumps(request))

    _stop_flag["stop"] = False

    # 2. Subscribe au channel events (pubsub)
    pubsub = None
    tool_msg_id = None
    accumulated_text = ""
    stream_msg_id = None
    last_edit_time = 0.0
    final_response = None

    try:
        pubsub = _redis_client.pubsub()
        pubsub.subscribe(events_channel)

        while True:
            if _stop_flag["stop"]:
                return "🛑 Arrêté par /stop — les process en cours continuent"

            # Check events (non-bloquant via get_message)
            msg = pubsub.get_message(timeout=0.1)
            if msg and msg["type"] == "message":
                try:
                    event = json.loads(msg["data"])
                    etype = event.get("type")

                    if etype == "tool_start":
                        tool_display = _format_tool_message(
                            event["tool"], event.get("args", {})
                        )
                        sent = await message.answer(tool_display, parse_mode=ParseMode.MARKDOWN_V2)
                        tool_msg_id = sent.message_id
                        await bot.send_chat_action(chat_id, ChatAction.TYPING)

                    elif etype == "tool_result":
                        result_display = _format_tool_result(
                            event["tool"], event.get("output", ""), event.get("success", True)
                        )
                        if tool_msg_id:
                            try:
                                await bot.edit_message_text(
                                    result_display,
                                    chat_id=chat_id,
                                    message_id=tool_msg_id,
                                    parse_mode=ParseMode.MARKDOWN_V2,
                                )
                            except (Exception,):
                                pass
                        await bot.send_chat_action(chat_id, ChatAction.TYPING)

                    elif etype == "stream_chunk":
                        accumulated_text += event.get("text", "")
                        now = asyncio.get_event_loop().time()
                        if now - last_edit_time >= _EDIT_INTERVAL:
                            if stream_msg_id is None:
                                sent = await message.answer(
                                    markdownify(accumulated_text),
                                    parse_mode=ParseMode.MARKDOWN_V2,
                                )
                                stream_msg_id = sent.message_id
                            else:
                                try:
                                    await bot.edit_message_text(
                                        markdownify(accumulated_text),
                                        chat_id=chat_id,
                                        message_id=stream_msg_id,
                                        parse_mode=ParseMode.MARKDOWN_V2,
                                    )
                                except (Exception,):
                                    pass
                            last_edit_time = now

                    elif etype == "final":
                        final_response = event.get("text", "")

                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

            # 3. Check la réponse finale (BRPOP avec timeout 1s — bloquant mais avec timeout)
            raw_response = _redis_client.brpop(response_key, timeout=1)
            if raw_response:
                _, raw = raw_response
                data = json.loads(raw)
                final_response = data.get("text", "")
                break

            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(0.1)

    finally:
        if pubsub is not None:
            try:
                pubsub.unsubscribe(events_channel)
                pubsub.close()
            except redis.ConnectionError:
                pass

    if stream_msg_id and final_response:
        try:
            await bot.edit_message_text(
                markdownify(final_response),
                chat_id=chat_id,
                message_id=stream_msg_id,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
            return None
        except (Exception,):
            pass

    return final_response or "[Pas de réponse de l'agent]"


def _check_auth(message: Message) -> bool:
    chat_id = str(message.chat.id)
    if not ALLOWED_CHAT_IDS:
        return True
    return chat_id in ALLOWED_CHAT_IDS


def _active_agent(chat_id: str) -> str:
    """Agent actif choisi via /main (stocké dans Redis), sinon défaut."""
    try:
        return _redis_client.get(f"telegram:{chat_id}:agent") or DEFAULT_AGENT
    except redis.RedisError:
        return DEFAULT_AGENT


def _agent_keyboard() -> InlineKeyboardMarkup:
    """Bulle de choix d'agent (/main)."""
    rows = [[InlineKeyboardButton(text=a["label"], callback_data=f"a:{a['key']}")]
            for a in AGENTS_TG]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _model_keyboard(agent_key: str) -> InlineKeyboardMarkup:
    """Bulle de choix de modèle (/model) — index en callback (limite 64 octets)."""
    ep = AGENT_BY_KEY.get(agent_key, {}).get("endpoint", "")
    models = MODELS_TG.get(ep, [])
    rows = [[InlineKeyboardButton(text=m.split("/")[-1][:45], callback_data=f"m:{agent_key}:{i}")]
            for i, m in enumerate(models)]
    return InlineKeyboardMarkup(inline_keyboard=rows or [[InlineKeyboardButton(
        text="(aucun modèle configuré)", callback_data="noop")]])


async def main():
    """Démarre le router Telegram (long polling)."""
    if not BOT_TOKEN:
        print(f"[{AGENT_ID}] ERREUR: TELEGRAM_BOT_TOKEN manquant dans .env")
        sys.exit(1)

    # Vérifier Redis (R5)
    try:
        _redis_client.ping()
    except redis.ConnectionError as e:
        print(f"[{AGENT_ID}] ERREUR: Redis injoignable — {e}")
        sys.exit(1)

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    @dp.message(F.text)
    async def handle_message(message: Message):
        if not _check_auth(message):
            return

        text = message.text or ""

        if is_internal_command(text):
            cmd = text.strip().lower()
            if cmd == "/status":
                await message.answer("🟢 Local_Agent — Router Telegram actif (Redis)")
            elif cmd == "/stop":
                _stop_flag["stop"] = True
                await message.answer("🛑 Arrêt demandé — les process en cours continuent")
            elif cmd == "/kill":
                await message.answer("💀 Kill demandé — arrêt complet")
                # Propre: signal SIGTERM au lieu de os._exit(1)
                os.kill(os.getpid(), signal.SIGTERM)
            return

        cmd0 = text.strip().split()[0].lower() if text.strip() else ""
        # Bulles de choix
        if cmd0 in ("/main", "/agent", "/agents"):
            await message.answer("🎯 Choisis l'agent actif :", reply_markup=_agent_keyboard())
            return
        if cmd0 == "/model":
            ak = _active_agent(str(message.chat.id))
            lbl = AGENT_BY_KEY.get(ak, {}).get("label", ak)
            await message.answer(f"🧠 Modèle pour {lbl} :", reply_markup=_model_keyboard(ak))
            return
        if cmd0 == "/help":
            await message.answer(
                "🤖 Commandes Local Agent :\n"
                "/main — choisir l'agent actif (bulle)\n"
                "/model — choisir le modèle de l'agent actif (bulle)\n"
                "/img <prompt> — image SD1.5 (rapide)\n"
                "/sdxl <prompt> — image SDXL (qualité)\n"
                "/flux <prompt> — image Flux (top qualité)\n"
                "/say <texte> — le lire à voix haute (Piper)\n"
                "🎤 message vocal — transcrit (whisper) et envoyé à l'agent\n"
                "/status — état · /stop — arrêter · /kill — tout arrêter\n"
                "Sinon : écris ton message → il part à l'agent actif.")
            return
        if cmd0 == "/say":
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.answer("Usage : /say <texte à dire>")
                return
            try:
                ogg = await asyncio.get_event_loop().run_in_executor(None, synthesize_ogg, parts[1])
                await message.answer_voice(FSInputFile(ogg))
            except Exception as e:
                await message.answer(f"❌ Piper: {type(e).__name__}: {e}")
            return
        _IMG_PRESETS = {
            "/img":     ("dreamshaper_8.safetensors", 768, 768, 25),
            "/comfyui": ("dreamshaper_8.safetensors", 768, 768, 25),
            "/sdxl":    ("sd_xl_base_1.0.safetensors", 1024, 1024, 30),
            "/flux":    ("flux1-dev-fp8.safetensors", 1024, 1024, 20),
        }
        if cmd0 in _IMG_PRESETS:
            parts = text.split(maxsplit=1)
            if len(parts) < 2:
                await message.answer(f"Usage : {cmd0} <description de l'image>")
                return
            ckpt, w, h, steps = _IMG_PRESETS[cmd0]
            await message.answer(f"🎨 Génération ({ckpt.split('.')[0]})…")
            await bot.send_chat_action(str(message.chat.id), ChatAction.UPLOAD_PHOTO)
            try:
                from tools.comfyui_generate import generate, GenerateArgs
                path = await asyncio.get_event_loop().run_in_executor(
                    None, generate, GenerateArgs(prompt=parts[1], checkpoint=ckpt,
                                                width=w, height=h, steps=steps))
                await message.answer_photo(FSInputFile(path), caption=f"🖼️ {parts[1][:150]}")
            except Exception as e:
                await message.answer(f"❌ ComfyUI: {type(e).__name__}: {e} (ComfyUI lancé sur :8188 ?)")
            return

        agent_key = resolve_agent(text)
        if agent_key is None:
            await message.answer("❌ Commande inconnue")
            return
        # Texte simple (sans slash) -> agent actif choisi via /main
        if cmd0 not in SLASH_MAP:
            agent_key = _active_agent(str(message.chat.id))

        prompt = extract_prompt(text)
        if not prompt:
            await message.answer("❓ Aucun prompt. Usage: /main ton message")
            return

        chat_id = str(message.chat.id)

        if _stop_flag["stop"]:
            await message.answer("🛑 Système en arrêt. Utilise /status pour vérifier.")
            return

        try:
            result = await _send_to_agent(bot, chat_id, message, agent_key, prompt)
            if result:
                converted = markdownify(result)
                await message.answer(converted, parse_mode=ParseMode.MARKDOWN_V2)
        except (redis.ConnectionError, TimeoutError):
            await message.answer("⏱️ L'agent n'a pas répondu (timeout). Agent démarré?")
        except (ValueError, KeyError, RuntimeError) as e:
            await message.answer(f"❌ Erreur: {type(e).__name__}: {e}")

    @dp.message(F.voice)
    async def handle_voice(message: Message):
        if not _check_auth(message):
            return
        try:
            f = await bot.get_file(message.voice.file_id)
            oga = f"/tmp/tg_{message.voice.file_unique_id}.oga"
            await bot.download_file(f.file_path, oga)
            txt = await asyncio.get_event_loop().run_in_executor(None, transcribe, oga)
        except Exception as e:
            await message.answer(f"❌ Transcription: {type(e).__name__}: {e}")
            return
        if not txt:
            await message.answer("🎤 (rien compris)")
            return
        await message.answer(f"🎤 « {txt} »")
        agent_key = _active_agent(str(message.chat.id))
        try:
            result = await _send_to_agent(bot, str(message.chat.id), message, agent_key, txt)
            if result:
                await message.answer(markdownify(result), parse_mode=ParseMode.MARKDOWN_V2)
        except (redis.ConnectionError, TimeoutError):
            await message.answer("⏱️ L'agent n'a pas répondu.")

    @dp.callback_query(F.data.startswith("a:"))
    async def on_agent_pick(cq: CallbackQuery):
        if not _check_auth(cq.message):
            await cq.answer()
            return
        key = cq.data.split(":", 1)[1]
        _redis_client.set(f"telegram:{cq.message.chat.id}:agent", key)
        lbl = AGENT_BY_KEY.get(key, {}).get("label", key)
        await cq.message.edit_text(
            f"✅ Agent actif : {lbl}\nÉcris ton message, ou /model pour changer de modèle.")
        await cq.answer("Agent sélectionné")

    @dp.callback_query(F.data.startswith("m:"))
    async def on_model_pick(cq: CallbackQuery):
        if not _check_auth(cq.message):
            await cq.answer()
            return
        try:
            _, key, idx = cq.data.split(":")
            model = MODELS_TG.get(AGENT_BY_KEY.get(key, {}).get("endpoint", ""), [])[int(idx)]
        except (ValueError, IndexError):
            await cq.answer("Choix invalide")
            return
        _redis_client.set(f"agent:{key}:model_override", model)
        lbl = AGENT_BY_KEY.get(key, {}).get("label", key)
        await cq.message.edit_text(f"✅ Modèle de {lbl} : {model}")
        await cq.answer("Modèle changé")

    @dp.callback_query(F.data == "noop")
    async def on_noop(cq: CallbackQuery):
        await cq.answer()

    print(f"[{AGENT_ID}] Démarrage — long polling Telegram (Redis)")
    print(f"[{AGENT_ID}] Chat IDs autorisés: {ALLOWED_CHAT_IDS or 'tous'}")

    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())