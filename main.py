from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, CallbackQueryHandler
import json
import os
from datetime import datetime, timedelta
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
import asyncio

# === CONFIG ===
CALENDAR_ID = "heebie7@gmail.com"
REMINDER_MINUTES_BEFORE = 60  # за час до встречи
CHECK_INTERVAL_MINUTES = 5    # проверять каждые 5 минут
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# === BOT TOKEN ===
try:
    bot_token = os.environ['BOT_TOKEN']
except:
    os.environ['BOT_TOKEN'] = input("Please enter your telegram bot token: ")
    bot_token = os.environ['BOT_TOKEN']

# === TESTS DATA ===
tests = {}
test_names = {
    "beckRu": "Шкала депрессии Бека (Русский)",
    "beckEn": "Beck Depression Inventory (english)",
    "sensoryProfileRu": "Профиль чувствительности (русский)",
    "sensoryProfileEn": "Sensory Profile (english)"
}

with open("beck_ru.json", "r", encoding="utf-8") as f:
    tests["beckRu"] = json.load(f)
with open("beck_en.json", "r", encoding="utf-8") as f:
    tests["beckEn"] = json.load(f)
with open("sensory_profile_ru.json", "r", encoding="utf-8") as f:
    tests["sensoryProfileRu"] = json.load(f)
with open("sensory_profile_en.json", "r", encoding="utf-8") as f:
    tests["sensoryProfileEn"] = json.load(f)

# === USER STORAGE ===
USERS_FILE = "registered_users.json"

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

def register_user(username, chat_id):
    users = load_users()
    if username:
        users[username.lower()] = chat_id
        save_users(users)
        print(f"Registered user: @{username} -> {chat_id}")

def get_chat_id(username):
    users = load_users()
    return users.get(username.lower().replace("@", ""))

# === SENT REMINDERS TRACKING ===
SENT_FILE = "sent_reminders.json"

def load_sent():
    if os.path.exists(SENT_FILE):
        with open(SENT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sent, f)

def mark_sent(event_id, event_time):
    sent = load_sent()
    key = f"{event_id}_{event_time}"
    if key not in sent:
        sent.append(key)
        # Keep only last 100 entries
        if len(sent) > 100:
            sent = sent[-100:]
        save_sent(sent)
    return key

def was_sent(event_id, event_time):
    sent = load_sent()
    return f"{event_id}_{event_time}" in sent

# === GOOGLE CALENDAR ===
def get_calendar_service():
    creds = None
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file('oauth-credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        with open('token.json', 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)

def get_upcoming_events():
    """Get events happening in the next REMINDER_MINUTES_BEFORE + CHECK_INTERVAL_MINUTES minutes"""
    try:
        service = get_calendar_service()
        now = datetime.utcnow()

        # Window: from (REMINDER_MINUTES_BEFORE - CHECK_INTERVAL) to (REMINDER_MINUTES_BEFORE + CHECK_INTERVAL)
        time_min = now + timedelta(minutes=REMINDER_MINUTES_BEFORE - CHECK_INTERVAL_MINUTES)
        time_max = now + timedelta(minutes=REMINDER_MINUTES_BEFORE + CHECK_INTERVAL_MINUTES)

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=time_min.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        return events_result.get('items', [])
    except Exception as e:
        print(f"Error fetching calendar: {e}")
        return []

def extract_username_from_event(event):
    """Extract telegram username from event description"""
    description = event.get('description', '')
    # Look for @username pattern or just username
    for line in description.split('\n'):
        line = line.strip()
        if line.startswith('@'):
            return line[1:].lower()
        # If line looks like a username (alphanumeric, underscore)
        if line and all(c.isalnum() or c == '_' for c in line):
            return line.lower()
    return None

# === REMINDER JOB ===
async def check_and_send_reminders(context):
    """Check calendar and send reminders"""
    print(f"[{datetime.now()}] Checking calendar...")
    events = get_upcoming_events()

    for event in events:
        event_id = event.get('id')
        event_start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
        event_summary = event.get('summary', 'Встреча')

        if was_sent(event_id, event_start):
            continue

        username = extract_username_from_event(event)
        if not username:
            print(f"  No username in event: {event_summary}")
            continue

        chat_id = get_chat_id(username)
        if not chat_id:
            print(f"  User @{username} not registered")
            continue

        # Format time nicely
        try:
            start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
            time_str = start_dt.strftime('%H:%M')
        except:
            time_str = event_start

        # Send reminder
        try:
            message = f"Напоминание: {event_summary} через час ({time_str})"
            await context.bot.send_message(chat_id=chat_id, text=message)
            mark_sent(event_id, event_start)
            print(f"  Sent reminder to @{username} for {event_summary}")
        except Exception as e:
            print(f"  Error sending to @{username}: {e}")

# === BOT HANDLERS ===
TEST, QUESTION, FINISH = range(3)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Register user for reminders
    if user.username:
        register_user(user.username, update.effective_chat.id)

    keyboard = [[InlineKeyboardButton(f'{test_names[i]}', callback_data=f'test_{i}')] for i in tests.keys()]
    keyboard_markup = InlineKeyboardMarkup(keyboard)

    welcome = "Добро пожаловать! Вы зарегистрированы для получения напоминаний о встречах.\n\n"
    welcome += "Выберите тест, который хотите пройти:"

    await update.message.reply_text(welcome, reply_markup=keyboard_markup)
    return TEST

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled")
    return ConversationHandler.END

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    test_name = query.data.split("_")[1]
    context.user_data['test'] = test_name
    context.user_data['block'] = 0
    context.user_data['question'] = 0
    context.user_data['answers'] = {'0': {}}
    await query.message.reply_text(tests[test_name][0]['name'] + '\n\n' + tests[test_name][0]['description'])

    question = tests[test_name][0]["questions"][0]
    answers = list(question['a'].keys())
    keyboard = [[InlineKeyboardButton(i+1, callback_data=f'answer_{i}_{0}_{0}') for i, text in enumerate(answers)]]
    keyboard_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(question['q'] + '\n\n' + '\n'.join([f'{i+1}: {n}' for i, n in enumerate(answers)]), reply_markup=keyboard_markup)
    context.user_data['question'] += 1
    return QUESTION

async def question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    test_name = context.user_data['test']
    question_number = context.user_data['question']
    block_number = context.user_data['block']
    context.user_data['answers'][query.data.split("_")[3]][query.data.split("_")[2]] = tests[test_name][block_number]["questions"][int(query.data.split("_")[2])]['a'][list(tests[test_name][block_number]["questions"][int(query.data.split("_")[2])]['a'].keys())[int(query.data.split("_")[1])]]

    if question_number >= len(tests[test_name][block_number]["questions"]):
        context.user_data['block'] += 1
        context.user_data['question'] = 0
        question_number = 0
        block_number = context.user_data['block']
        if context.user_data['block'] >= len(tests[test_name]):
            message = "Тест завершен\n\nРезультаты:\n"
            for block_number, block in context.user_data['answers'].items():
                message += f"{tests[test_name][int(block_number)]['name']}:\n"
                for question_number, answer in block.items():
                    message += f"  Вопрос {question_number}: {answer}\n"
                message += f"Сумма баллов: {sum(block.values())}\n\n"
            await query.message.reply_text(message)
            return ConversationHandler.END
        await query.message.reply_text(tests[test_name][block_number]['name'] + '\n\n' + tests[test_name][block_number]['description'])
        context.user_data['answers'][str(context.user_data['block'])] = {}

    question = tests[test_name][block_number]["questions"][question_number]
    if (question_number-1 == int(query.data.split("_")[2]) and block_number == int(query.data.split("_")[3])) or question_number == 0:
        answers = list(question['a'].keys())
        keyboard = [[InlineKeyboardButton(i+1, callback_data=f'answer_{i}_{question_number}_{block_number}') for i, text in enumerate(answers)]]
        keyboard_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f'{question["q"]}\n\n' + '\n'.join([f'{i+1}: {n}' for i, n in enumerate(answers)]), reply_markup=keyboard_markup)
        context.user_data['question'] += 1
    else:
        await query.message.reply_text("Выберите один из вариантов")

    return QUESTION

async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# === MAIN ===
def main():
    # Initialize calendar service (will prompt for auth on first run)
    print("Initializing Google Calendar connection...")
    try:
        get_calendar_service()
        print("Calendar connected!")
    except Exception as e:
        print(f"Calendar connection failed: {e}")
        print("Bot will start, but reminders won't work until calendar is connected.")

    application = Application.builder().token(bot_token).build()

    # Add reminder job
    job_queue = application.job_queue
    job_queue.run_repeating(check_and_send_reminders, interval=CHECK_INTERVAL_MINUTES * 60, first=10)

    main_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            TEST: [CallbackQueryHandler(test)],
            QUESTION: [CallbackQueryHandler(question)],
            FINISH: [CallbackQueryHandler(finish)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )

    application.add_handler(main_handler)

    print("Bot started! Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
