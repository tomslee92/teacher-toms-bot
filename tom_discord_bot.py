import os
import logging
import tempfile
import asyncio
import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq

# ── Configuration ─────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

# ── Setup ─────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# ── Detect Korean text ────────────────────────────────────────────────────────
def is_korean(text: str) -> bool:
    korean_chars = sum(1 for c in text if '\uAC00' <= c <= '\uD7A3' or '\u1100' <= c <= '\u11FF')
    return korean_chars > 2

# ── Transcribe voice with Groq Whisper ───────────────────────────────────────
async def transcribe_audio(file_path: str) -> str:
    with open(file_path, "rb") as audio_file:
        transcription = groq_client.audio.transcriptions.create(
            file=(os.path.basename(file_path), audio_file),
            model="whisper-large-v3",
            response_format="text"
        )
    return transcription.strip()

# ── Grammar feedback ──────────────────────────────────────────────────────────
async def get_grammar_feedback(text: str, student_name: str) -> str:
    # Adjust feedback depth based on length of transcription
    word_count = len(text.split())
    if word_count > 50:
        depth_instruction = f"""This is a longer response ({word_count} words). 
Analyze the ENTIRE text carefully and identify ALL grammar errors.
List each error separately under 문법 피드백.
Be thorough — do not skip any mistakes."""
    else:
        depth_instruction = "Analyze the text and identify the main grammar issues."

    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=800,
        messages=[{
            "role": "system",
            "content": "You are Tom, a warm English grammar coach for Korean learners at Wayve English. You ONLY write in Korean and English. You NEVER use Chinese, Japanese, Russian, or any other script. Every single word must be either Korean (한글) or English (Latin alphabet). No exceptions."
        }, {
            "role": "user",
            "content": f"""Student {student_name} said or wrote in English:

"{text}"

{depth_instruction}

Give grammar feedback using ONLY Korean and English (no other languages or scripts).

Format exactly like this:

🎯 문법 점수: X/10
[Korean sentence explaining the overall score]

✅ 잘한 점
[One encouraging Korean sentence about what they did well]

📝 문법 피드백
[List EVERY grammar error found — number each one]
1. [Korean explanation of error]
→ 수정: [Corrected version in English]

2. [Korean explanation of next error if any]
→ 수정: [Corrected version in English]

(continue for all errors found)

💡 더 자연스러운 표현
→ [The most natural native English version of their full message]

💪 [One short motivating Korean sentence]

Scoring guide:
10/10 = Perfect grammar
8-9/10 = 1-2 minor issues
6-7/10 = Several noticeable mistakes
4-5/10 = Many grammar issues
1-3/10 = Major restructuring needed

CRITICAL: Korean and English ONLY. The final motivational line must be pure Korean hangul — no Chinese characters, no hanja."""
        }]
    )
    return response.choices[0].message.content

# ── Korean question handler ───────────────────────────────────────────────────
async def handle_korean_question(text: str, student_name: str) -> str:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        max_tokens=400,
        messages=[{
            "role": "system",
            "content": "You are Tom, a warm English teacher for Korean learners at Wayve English. You ONLY write in Korean and English. You NEVER use Chinese, Japanese, Russian, or any other script. Every single word must be either Korean (한글) or English (Latin alphabet). No exceptions."
        }, {
            "role": "user",
            "content": f"""Student {student_name} asked in Korean: "{text}"

Answer using ONLY Korean and English (absolutely no other languages or scripts).

Format exactly like this:

🇰🇷 한국어 표현
[The exact Korean phrase the student asked about]

🗣 영어로는 이렇게 말해요!
[The English translation]

📌 예문 (Example Sentences)
1. "[English sentence]"
→ "[Korean translation]"

2. "[English sentence]"
→ "[Korean translation]"

💡 사용 팁 (Usage Tip)
[One short Korean tip about when to use this naturally]

💪 [One short encouraging Korean sentence — pure Korean hangul only, no Chinese characters]

Korean and English ONLY."""
        }]
    )
    return response.choices[0].message.content

# ── Split long messages for Discord (2000 char limit) ────────────────────────
async def send_long_message(channel, content: str, reference=None):
    if len(content) <= 1900:
        if reference:
            await reference.reply(content)
        else:
            await channel.send(content)
        return

    # Split at natural breakpoints
    parts = []
    current = ""
    for line in content.split("\n"):
        if len(current) + len(line) + 1 > 1900:
            parts.append(current)
            current = line
        else:
            current += "\n" + line if current else line
    if current:
        parts.append(current)

    for i, part in enumerate(parts):
        if i == 0 and reference:
            await reference.reply(part)
        else:
            await channel.send(part)

# ── Bot ready ─────────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    logger.info(f"Tom is online! Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} commands")
    except Exception as e:
        logger.error(f"Failed to sync commands: {e}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.listening,
        name="영어 연습 중 🎙"
    ))

# ── Handle messages ───────────────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)
    student_name = message.author.display_name

    # ── Voice/audio attachments ──
    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ['.mp3', '.wav', '.ogg', '.m4a', '.webm', '.mp4']):
                async with message.channel.typing():
                    try:
                        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
                            tmp_path = tmp_file.name
                        await attachment.save(tmp_path)
                        await message.reply("🎙 잠깐만요, 듣고 있어요...\n*(Just a moment, listening...)*")
                        transcription = await transcribe_audio(tmp_path)
                        os.unlink(tmp_path)

                        if not transcription or len(transcription.strip()) < 3:
                            await message.reply("❗ 잘 안 들렸어요. 다시 한번 보내줄 수 있어요?")
                            return

                        if is_korean(transcription):
                            feedback = await handle_korean_question(transcription, student_name)
                            await send_long_message(message.channel, feedback, reference=message)
                        else:
                            feedback = await get_grammar_feedback(transcription, student_name)
                            full_response = f"🎙 **{student_name}이/가 말한 내용**\n\"{transcription}\"\n\n{feedback}"
                            await send_long_message(message.channel, full_response, reference=message)
                    except Exception as e:
                        logger.error(f"Audio error: {e}")
                        await message.reply("❗ 죄송해요, 문제가 생겼어요. 다시 시도해 주세요!")
        return

    # ── Text messages ──
    if message.content and not message.content.startswith("!"):
        async with message.channel.typing():
            try:
                if is_korean(message.content):
                    feedback = await handle_korean_question(message.content, student_name)
                    await send_long_message(message.channel, feedback, reference=message)
                else:
                    feedback = await get_grammar_feedback(message.content, student_name)
                    full_response = f"✍️ **{student_name}이/가 쓴 내용**\n\"{message.content}\"\n\n{feedback}"
                    await send_long_message(message.channel, full_response, reference=message)
            except Exception as e:
                logger.error(f"Text error: {e}")
                await message.reply("❗ 잠깐 문제가 생겼어요. 다시 시도해 주세요!")

# ── Slash Commands ────────────────────────────────────────────────────────────
@bot.tree.command(name="start", description="Tom 소개 / Meet Tom")
async def start(interaction: discord.Interaction):
    name = interaction.user.display_name
    await interaction.response.send_message(
        f"안녕하세요, **{name}**! 👋\n\n"
        f"저는 **Tom**이에요! Wayve English의 AI 영어 코치입니다. 🎙\n\n"
        f"**사용 방법**\n"
        f"🎙 영어 음성 파일 → 문법 피드백 + 점수\n"
        f"✍️ 영어 타이핑 → 문법 교정\n"
        f"🇰🇷 한국어 질문 → 영어 표현 알려드려요!\n\n"
        f"*발음 피드백은 Teacher Toms와 Zoom 수업에서 해드려요! 😊*\n\n"
        f"Let's practice! 💪"
    )

@bot.tree.command(name="help", description="Tom 사용법 / How to use Tom")
async def help_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🎯 **Tom 사용법**\n\n"
        "🎙 영어 음성 파일 → 문법 피드백 + 점수\n"
        "✍️ 영어 텍스트 → 문법 교정\n"
        "🇰🇷 한국어 질문 → 영어 표현 설명\n\n"
        "📚 숙제는 Teacher Toms가 올려드릴게요!\n"
        "발음 피드백은 Zoom 수업에서! 😊\n\n"
        "화이팅! 💪"
    )

@bot.tree.command(name="question", description="오늘의 질문 올리기 (Teacher only)")
@app_commands.describe(text="질문 내용을 입력하세요")
async def question(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(
        f"📚 **오늘의 질문 (Question of the Day)**\n\n"
        f"*{text}*\n\n"
        f"🎙 음성 파일로 대답해 주세요!\n"
        f"*Please answer with a voice message!*"
    )

@bot.tree.command(name="homework", description="이번 주 숙제 올리기 (Teacher only)")
@app_commands.describe(week="예: Week 3", topic="예: Giving Compliments", sentences="문장들을 줄바꿈으로 구분해서 입력")
async def homework(interaction: discord.Interaction, week: str, topic: str, sentences: str):
    sentence_list = sentences.split("\n")
    formatted = "\n\n".join([f"{i+1}. *{s.strip()}*" for i, s in enumerate(sentence_list) if s.strip()])
    await interaction.response.send_message(
        f"📖 **{week} 숙제 — {topic}**\n\n"
        f"아래 문장들을 연습하고 음성 메시지로 보내주세요!\n"
        f"*Practice these sentences and send a voice message!*\n\n"
        f"{formatted}\n\n"
        f"💪 화이팅! 열심히 연습해요!"
    )

# ── Main ──────────────────────────────────────────────────────────────────────
async def main():
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
