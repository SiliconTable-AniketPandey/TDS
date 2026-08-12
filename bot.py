import base64
import json
import time
import os
import requests

from openai import OpenAI
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    ContextTypes,
    filters,
)


TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
AIPIPE_TOKEN = os.environ["AIPIPE_TOKEN"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"]

LOG_FILE = "run.jsonl"

LOG_URL = (
    "https://raw.githubusercontent.com/"
    "SiliconTable-AniketPandey/TDS/main/run.jsonl"
)


client = OpenAI(
    base_url="https://aipipe.org/openai/v1",
    api_key=AIPIPE_TOKEN,
)


conversation_history = {}


def log_event(event: dict):
    event["timestamp"] = time.time()

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def sync_log_to_github():
    """
    Upload the current run.jsonl to GitHub.
    This makes LOG_URL contain the latest Railway runs.
    """

    api_url = (
        f"https://api.github.com/repos/"
        f"{GITHUB_REPO}/contents/run.jsonl"
    )

    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    # Get the current GitHub version so we have its SHA.
    response = requests.get(
        api_url,
        headers=headers,
        timeout=30,
    )

    sha = None

    if response.status_code == 200:
        sha = response.json()["sha"]
    elif response.status_code != 404:
        response.raise_for_status()

    # Read the updated local log.
    with open(LOG_FILE, "rb") as f:
        encoded_content = base64.b64encode(
            f.read()
        ).decode("utf-8")

    payload = {
        "message": "Update bot run log",
        "content": encoded_content,
        "branch": "main",
    }

    if sha:
        payload["sha"] = sha

    response = requests.put(
        api_url,
        headers=headers,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    print("run.jsonl synced to GitHub")


async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id
    user_text = update.message.text

    log_event(
        {
            "type": "incoming",
            "chat_id": chat_id,
            "text": user_text,
        }
    )

    history = conversation_history.setdefault(
        chat_id,
        [],
    )

    history.append(
        {
            "role": "user",
            "content": user_text,
        }
    )

    system_prompt = (
        "You are a careful data analyst. "
        "The user's LAST message asks a data-analysis "
        "question and may specify the exact shape of "
        "the answer. Work out the real answer. "
        "Your response MUST always be a valid JSON "
        'object with exactly one top-level key named "answer". '
        "Place the requested value or requested nested structure "
        'inside "answer". '
        "Do not include log_url because the application adds it. "
        "Return no explanation, markdown, or code fences."
    )

    response = client.chat.completions.create(
        model="gpt-5-mini",
        messages=[
            {
                "role": "system",
                "content": system_prompt,
            }
        ]
        + history[-6:],
    )

    reply_text = (
        response
        .choices[0]
        .message
        .content
        .strip()
    )

    try:
        parsed = json.loads(reply_text)

    except json.JSONDecodeError:
        start = reply_text.find("{")
        end = reply_text.rfind("}")

        parsed = json.loads(
            reply_text[start:end + 1]
        )

    # Always enforce the assignment's required
    # top-level structure.
    if "answer" in parsed:
        answer = parsed["answer"]
    elif "message" in parsed:
        answer = parsed["message"]
    else:
        answer = parsed

    final_object = {
        "answer": answer,
        "log_url": LOG_URL,
    }

    final_reply = json.dumps(final_object)

    history.append(
        {
            "role": "assistant",
            "content": final_reply,
        }
    )

    log_event(
        {
            "type": "outgoing",
            "chat_id": chat_id,
            "text": final_reply,
        }
    )

    # Update the public GitHub log BEFORE replying.
    try:
        sync_log_to_github()
    except Exception as e:
        print(
            "Could not sync run.jsonl to GitHub:",
            e,
        )

    await update.message.reply_text(
        final_reply
    )


app = (
    ApplicationBuilder()
    .token(TELEGRAM_BOT_TOKEN)
    .build()
)

app.add_handler(
    MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_message,
    )
)

print("Bot is running... (Ctrl+C to stop)")

app.run_polling()
