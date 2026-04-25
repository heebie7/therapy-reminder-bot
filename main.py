import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
import json
import asyncio
import math

# === LOGGING ===
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv
import base64
import requests

# Load .env file
load_dotenv()

# === GITHUB STORAGE ===
GITHUB_TOKEN = os.environ.get('GITHUB_TOKEN')
GITHUB_REPO = os.environ.get('GITHUB_REPO', 'heebie7/therapy-reminder-bot')
DATA_FILES = ['registered_users.json', 'user_timezones.json', 'test_results.json', 'sent_reminders.json', 'user_notifications.json']

def github_get_file(filename):
    """Get file content from GitHub"""
    if not GITHUB_TOKEN:
        return None
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            content = base64.b64decode(resp.json()['content']).decode('utf-8')
            return json.loads(content)
    except Exception as e:
        logger.error(f"GitHub get error for {filename}: {e}")
    return None

def github_save_file(filename, data):
    """Save file to GitHub"""
    if not GITHUB_TOKEN:
        return False
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/data/{filename}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"}

        # Get current file SHA if exists
        sha = None
        resp = requests.get(url, headers=headers)
        if resp.status_code == 200:
            sha = resp.json()['sha']

        # Prepare content
        content = base64.b64encode(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8')).decode('utf-8')

        payload = {
            "message": f"Update {filename}",
            "content": content,
        }
        if sha:
            payload["sha"] = sha

        resp = requests.put(url, headers=headers, json=payload)
        if resp.status_code in [200, 201]:
            logger.info(f"Saved {filename} to GitHub")
            return True
        else:
            logger.error(f"GitHub save error: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"GitHub save error for {filename}: {e}")
    return False

def init_data_from_github():
    """Load data files from GitHub on startup"""
    if not GITHUB_TOKEN:
        logger.warning("GITHUB_TOKEN not set, data persistence disabled")
        return

    for filename in DATA_FILES:
        if not os.path.exists(filename):
            data = github_get_file(filename)
            if data is not None:
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.info(f"Loaded {filename} from GitHub")

# === CONFIG ===
CALENDAR_ID = "heebie7@gmail.com"
CHECK_INTERVAL_MINUTES = 5    # проверять каждые 5 минут
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

# Напоминания: (минуты до встречи, текст)
REMINDERS = [
    (60 * 24 * 6, "через 6 дней"),  # за 6 дней
    (60, "через час"),               # за час
]

# === BOT TOKEN ===
try:
    bot_token = os.environ['BOT_TOKEN']
except:
    os.environ['BOT_TOKEN'] = input("Please enter your telegram bot token: ")
    bot_token = os.environ['BOT_TOKEN']

# === TESTS DATA ===
tests = {}
test_names = {
    "beckRu": "Шкала депрессии Бека",
    "sensoryProfileRu": "Сенсорный профиль",
    "mqRu": "Опросник монотропизма (MQ)",
    "raadsRRu": "RAADS long (80)",
    "raads14Ru": "RAADS short (14)",
    # English versions (hidden for now):
    # "beckEn": "Beck Depression Inventory (English)",
    # "sensoryProfile": "Sensory Profile",
}

with open("beck_ru.json", "r", encoding="utf-8") as f:
    tests["beckRu"] = json.load(f)
with open("sensory_profile_ru.json", "r", encoding="utf-8") as f:
    tests["sensoryProfileRu"] = json.load(f)
with open("mq_ru.json", "r", encoding="utf-8") as f:
    tests["mqRu"] = json.load(f)
with open("raads_r_ru.json", "r", encoding="utf-8") as f:
    tests["raadsRRu"] = json.load(f)
with open("raads_14_ru.json", "r", encoding="utf-8") as f:
    tests["raads14Ru"] = json.load(f)
# English versions (hidden for now):
# with open("beck_en.json", "r", encoding="utf-8") as f:
#     tests["beckEn"] = json.load(f)
# with open("sensory_profile_en.json", "r", encoding="utf-8") as f:
#     tests["sensoryProfile"] = json.load(f)

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
    github_save_file(USERS_FILE, users)

def register_user(username, chat_id):
    users = load_users()
    if username:
        users[username.lower()] = chat_id
        save_users(users)
        print(f"Registered user: @{username} -> {chat_id}")

def get_chat_id(username):
    users = load_users()
    return users.get(username.lower().replace("@", ""))

# === TIMEZONE STORAGE ===
TIMEZONES_FILE = "user_timezones.json"

# Common timezones for manual selection
COMMON_TIMEZONES = [
    ("Europe/Moscow", "Москва (UTC+3)"),
    ("Europe/Kaliningrad", "Калининград (UTC+2)"),
    ("Europe/Samara", "Самара (UTC+4)"),
    ("Asia/Yekaterinburg", "Екатеринбург (UTC+5)"),
    ("Asia/Omsk", "Омск (UTC+6)"),
    ("Asia/Krasnoyarsk", "Красноярск (UTC+7)"),
    ("Asia/Irkutsk", "Иркутск (UTC+8)"),
    ("Asia/Yakutsk", "Якутск (UTC+9)"),
    ("Asia/Vladivostok", "Владивосток (UTC+10)"),
    ("Asia/Tbilisi", "Тбилиси (UTC+4)"),
    ("Europe/Kiev", "Киев (UTC+2)"),
    ("Europe/Minsk", "Минск (UTC+3)"),
    ("Asia/Almaty", "Алматы (UTC+6)"),
    ("Europe/Berlin", "Берлин (UTC+1)"),
    ("Europe/London", "Лондон (UTC+0)"),
    ("America/New_York", "Нью-Йорк (UTC-5)"),
]

def load_timezones():
    if os.path.exists(TIMEZONES_FILE):
        with open(TIMEZONES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_timezones(tzdata):
    with open(TIMEZONES_FILE, "w", encoding="utf-8") as f:
        json.dump(tzdata, f, ensure_ascii=False, indent=2)
    github_save_file(TIMEZONES_FILE, tzdata)

def set_user_timezone(user_id, timezone_str):
    tzdata = load_timezones()
    tzdata[str(user_id)] = timezone_str
    save_timezones(tzdata)

def get_user_timezone(user_id):
    tzdata = load_timezones()
    return tzdata.get(str(user_id))

def timezone_from_location(latitude, longitude):
    tf = TimezoneFinder()
    return tf.timezone_at(lat=latitude, lng=longitude)

# === TEST RESULTS STORAGE ===
TEST_RESULTS_FILE = "test_results.json"

def load_test_results():
    if os.path.exists(TEST_RESULTS_FILE):
        with open(TEST_RESULTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_test_results(results):
    with open(TEST_RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    github_save_file(TEST_RESULTS_FILE, results)

def store_test_result(user_id, username, test_name, answers, score=None):
    """Store a test result for a user"""
    results = load_test_results()
    user_key = str(user_id)

    if user_key not in results:
        results[user_key] = {
            "username": username,
            "tests": []
        }

    # Update username in case it changed
    results[user_key]["username"] = username

    # Add new test result
    results[user_key]["tests"].append({
        "test": test_name,
        "date": datetime.now().isoformat(),
        "answers": answers,
        "score": score
    })

    save_test_results(results)

def get_user_test_history(user_id):
    """Get all test results for a user"""
    results = load_test_results()
    return results.get(str(user_id), {}).get("tests", [])

# === NOTIFICATIONS SETTINGS ===
NOTIFICATIONS_FILE = "user_notifications.json"

def load_notifications():
    if os.path.exists(NOTIFICATIONS_FILE):
        with open(NOTIFICATIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_notifications(data):
    with open(NOTIFICATIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    github_save_file(NOTIFICATIONS_FILE, data)

def is_notifications_enabled(user_id):
    data = load_notifications()
    return data.get(str(user_id), True)  # включены по умолчанию

def set_notifications(user_id, enabled):
    data = load_notifications()
    data[str(user_id)] = enabled
    save_notifications(data)

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
    github_save_file(SENT_FILE, sent)

def mark_sent(event_id, event_time, reminder_type):
    sent = load_sent()
    key = f"{event_id}_{event_time}_{reminder_type}"
    if key not in sent:
        sent.append(key)
        # Keep only last 200 entries
        if len(sent) > 200:
            sent = sent[-200:]
        save_sent(sent)
    return key

def was_sent(event_id, event_time, reminder_type):
    sent = load_sent()
    return f"{event_id}_{event_time}_{reminder_type}" in sent

# === GOOGLE CALENDAR ===
def get_calendar_service():
    creds = None

    # Try to load from environment variable first (for Railway/server deployment)
    token_json_env = os.environ.get('GOOGLE_TOKEN_JSON')
    if token_json_env:
        import base64
        token_data = base64.b64decode(token_json_env).decode('utf-8')
        creds = Credentials.from_authorized_user_info(json.loads(token_data), SCOPES)
    elif os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Update env token if running on server
            if token_json_env:
                print("Token refreshed. Update GOOGLE_TOKEN_JSON env variable with new token if needed.")
        else:
            # Local development: interactive auth
            flow = InstalledAppFlow.from_client_secrets_file('oauth-credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)

        # Save to file only if not using env variable
        if not token_json_env:
            with open('token.json', 'w') as token:
                token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)

def get_upcoming_events(minutes_before):
    """Get events happening around minutes_before from now"""
    try:
        service = get_calendar_service()
        now = datetime.utcnow()

        # Window: from (minutes_before - CHECK_INTERVAL) to (minutes_before + CHECK_INTERVAL)
        time_min = now + timedelta(minutes=minutes_before - CHECK_INTERVAL_MINUTES)
        time_max = now + timedelta(minutes=minutes_before + CHECK_INTERVAL_MINUTES)

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
    """Check calendar and send reminders for all configured reminder times"""
    print(f"[{datetime.now()}] Checking calendar...")

    for minutes_before, reminder_text in REMINDERS:
        events = get_upcoming_events(minutes_before)

        for event in events:
            event_id = event.get('id')
            event_start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
            event_summary = event.get('summary', 'Встреча')

            if was_sent(event_id, event_start, reminder_text):
                continue

            username = extract_username_from_event(event)
            if not username:
                print(f"  No username in event: {event_summary}")
                continue

            chat_id = get_chat_id(username)
            if not chat_id:
                print(f"  User @{username} not registered")
                continue

            if not is_notifications_enabled(chat_id):
                print(f"  Notifications disabled for @{username}, skipping")
                continue

            # Format date/time nicely
            try:
                start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
                user_tz_name = get_user_timezone(chat_id)
                if user_tz_name:
                    start_dt = start_dt.astimezone(ZoneInfo(user_tz_name))
                if minutes_before > 60 * 24:  # больше суток — показываем дату
                    time_str = start_dt.strftime('%d.%m в %H:%M')
                else:
                    time_str = start_dt.strftime('%H:%M')
            except:
                time_str = event_start

            # Send reminder
            try:
                message = f"Напоминание: Сессия с Аней Алашеевой {reminder_text} ({time_str})"
                await context.bot.send_message(chat_id=chat_id, text=message)
                mark_sent(event_id, event_start, reminder_text)
                print(f"  Sent '{reminder_text}' reminder to @{username} for {event_summary}")
            except Exception as e:
                print(f"  Error sending to @{username}: {e}")

# === MAIN MENU ===
def get_main_menu():
    keyboard = [
        [KeyboardButton("Тесты"), KeyboardButton("Мои встречи")],
        [KeyboardButton("📚 Материалы"), KeyboardButton("🔔 Уведомления")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, is_persistent=True)

def get_user_events(username):
    """Get upcoming events for a specific user"""
    try:
        service = get_calendar_service()
        now = datetime.utcnow()
        time_max = now + timedelta(days=14)  # следующие 2 недели

        events_result = service.events().list(
            calendarId=CALENDAR_ID,
            timeMin=now.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        user_events = []
        for event in events_result.get('items', []):
            event_username = extract_username_from_event(event)
            if event_username and event_username.lower() == username.lower():
                user_events.append(event)
        return user_events
    except Exception as e:
        print(f"Error fetching user events: {e}")
        return []

# === BOT HANDLERS ===
TEST, QUESTION, FINISH = range(3)
TZ_WAITING_LOCATION, TZ_WAITING_MANUAL, TZ_WAITING_CONFIRM = range(10, 13)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    # Register user for reminders
    if user.username:
        register_user(user.username, update.effective_chat.id)

    # Check if user has timezone set
    user_tz = get_user_timezone(user.id)
    if not user_tz:
        welcome = "Добро пожаловать! Вы зарегистрированы для получения напоминаний о встречах.\n\n"
        welcome += "Для корректного отображения времени встреч, пожалуйста, укажите ваш часовой пояс."
        await update.message.reply_text(welcome, reply_markup=get_main_menu())
        # Show timezone method selection
        keyboard = [
            [InlineKeyboardButton("📍 По геолокации", callback_data="tz_method_location")],
            [InlineKeyboardButton("🔧 Выбрать вручную", callback_data="tz_method_manual")],
        ]
        await update.message.reply_text(
            "Как вы хотите указать часовой пояс?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        # Show current timezone and option to change
        try:
            tz = ZoneInfo(user_tz)
            now = datetime.now(tz)
            tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == user_tz), user_tz)
            tz_info = f"\n\nВаш часовой пояс: {tz_label}\nТекущее время: {now.strftime('%H:%M')}"
        except:
            tz_info = f"\n\nВаш часовой пояс: {user_tz}"

        welcome = "Добро пожаловать! Вы зарегистрированы для получения напоминаний о встречах." + tz_info
        await update.message.reply_text(welcome, reply_markup=get_main_menu())
        # Option to change timezone
        keyboard = [[InlineKeyboardButton("🔄 Изменить часовой пояс", callback_data="tz_method_change")]]
        await update.message.reply_text(
            "Если хотите изменить часовой пояс:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    return ConversationHandler.END

async def ask_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for timezone via location"""
    keyboard = [
        [KeyboardButton("📍 Отправить геолокацию", request_location=True)],
        [KeyboardButton("⬅️ Назад")]
    ]
    markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    await update.message.reply_text(
        "Отправьте геолокацию для определения часового пояса.",
        reply_markup=markup
    )
    return TZ_WAITING_LOCATION

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle received location - detect timezone and ask for confirmation"""
    try:
        location = update.message.location
        tz_name = timezone_from_location(location.latitude, location.longitude)

        if tz_name:
            # Store detected timezone temporarily for confirmation
            context.user_data['detected_tz'] = tz_name

            try:
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)
                time_str = now.strftime('%H:%M')
                tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == tz_name), tz_name)
            except:
                time_str = None
                tz_label = tz_name

            # Ask for confirmation
            keyboard = [
                [
                    InlineKeyboardButton("✅ Да, верно", callback_data="tz_confirm_yes"),
                    InlineKeyboardButton("❌ Нет, выбрать вручную", callback_data="tz_confirm_no"),
                ]
            ]

            if time_str:
                msg = f"Определён часовой пояс: {tz_label}\nСейчас у вас {time_str}?\n\nЕсли время неверное — выберите пояс вручную."
            else:
                msg = f"Определён часовой пояс: {tz_label}\n\nВсё верно?"

            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
            return TZ_WAITING_CONFIRM
        else:
            await update.message.reply_text(
                "Не удалось определить таймзону автоматически. Выберите вручную:"
            )
            return await show_manual_tz(update, context)

    except Exception as e:
        print(f"Error in handle_location: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте ещё раз.", reply_markup=get_main_menu())
        return ConversationHandler.END


async def handle_tz_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone confirmation callback"""
    query = update.callback_query
    await query.answer()

    try:
        await query.message.delete()
    except:
        pass

    if query.data == "tz_confirm_yes":
        # User confirmed - save the detected timezone
        tz_name = context.user_data.get('detected_tz')
        if tz_name:
            set_user_timezone(update.effective_user.id, tz_name)
            tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == tz_name), tz_name)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ Часовой пояс установлен: {tz_label}",
                reply_markup=get_main_menu()
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Ошибка. Попробуйте ещё раз.",
                reply_markup=get_main_menu()
            )
        return ConversationHandler.END
    else:
        # User wants manual selection
        return await show_manual_tz_from_callback(update, context)

def build_tz_keyboard():
    """Build timezone selection keyboard"""
    keyboard = []
    for i in range(0, len(COMMON_TIMEZONES), 2):
        row = []
        row.append(InlineKeyboardButton(
            COMMON_TIMEZONES[i][1],
            callback_data=f"tz_{COMMON_TIMEZONES[i][0]}"
        ))
        if i + 1 < len(COMMON_TIMEZONES):
            row.append(InlineKeyboardButton(
                COMMON_TIMEZONES[i+1][1],
                callback_data=f"tz_{COMMON_TIMEZONES[i+1][0]}"
            ))
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="tz_cancel")])
    return InlineKeyboardMarkup(keyboard)


async def show_manual_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manual timezone selection (from message context)"""
    await update.message.reply_text(
        "Выберите ваш часовой пояс:",
        reply_markup=build_tz_keyboard()
    )
    return TZ_WAITING_MANUAL

async def show_manual_tz_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manual timezone selection (from callback context)"""
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Выберите ваш часовой пояс:",
        reply_markup=build_tz_keyboard()
    )
    return TZ_WAITING_MANUAL


async def handle_manual_tz_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Select manually' button"""
    return await show_manual_tz(update, context)

async def handle_tz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone selection callback"""
    query = update.callback_query
    await query.answer()

    # Remove inline keyboard
    try:
        await query.message.delete()
    except:
        pass

    if query.data == "tz_cancel":
        await query.message.reply_text("Выбор отменён.", reply_markup=get_main_menu())
        return ConversationHandler.END

    tz_name = query.data.replace("tz_", "")
    set_user_timezone(update.effective_user.id, tz_name)

    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == tz_name), tz_name)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Часовой пояс установлен: {tz_label}\n"
                 f"Текущее время у вас: {now.strftime('%H:%M')}",
            reply_markup=get_main_menu()
        )
    except:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ Часовой пояс установлен: {tz_name}",
            reply_markup=get_main_menu()
        )

    return ConversationHandler.END

async def handle_back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle back button"""
    await update.message.reply_text("Главное меню", reply_markup=get_main_menu())
    # Return END only if in conversation, otherwise just return
    try:
        return ConversationHandler.END
    except:
        return

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone menu button"""
    return await ask_timezone(update, context)

async def handle_tz_method_callback_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone method selection - global version for outside conversation"""
    query = update.callback_query
    await query.answer()

    if query.data == "tz_method_cancel":
        await query.message.reply_text("Отменено.", reply_markup=get_main_menu())
        return

    if query.data == "tz_method_change":
        # Show method selection for changing timezone
        keyboard = [
            [InlineKeyboardButton("📍 По геолокации", callback_data="tz_method_location")],
            [InlineKeyboardButton("🔧 Выбрать вручную", callback_data="tz_method_manual")],
            [InlineKeyboardButton("⬅️ Отмена", callback_data="tz_method_cancel")]
        ]
        await query.message.edit_text(
            "Как вы хотите указать часовой пояс?",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if query.data == "tz_method_manual":
        # Show manual selection
        await query.message.edit_text(
            "Выберите ваш часовой пояс:",
            reply_markup=build_tz_keyboard()
        )
        return

    if query.data == "tz_method_location":
        # Request location
        keyboard = [
            [KeyboardButton("📍 Отправить геолокацию", request_location=True)],
            [KeyboardButton("⬅️ Назад")]
        ]
        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
        await query.message.reply_text(
            "Нажмите кнопку ниже, чтобы отправить геолокацию.",
            reply_markup=markup
        )
        return

async def handle_tz_callback_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone selection - global version for outside conversation"""
    query = update.callback_query
    await query.answer()

    if query.data == "tz_cancel":
        await query.message.reply_text("Выбор отменён.", reply_markup=get_main_menu())
        return

    tz_name = query.data.replace("tz_", "")
    set_user_timezone(update.effective_user.id, tz_name)

    try:
        tz = ZoneInfo(tz_name)
        now = datetime.now(tz)
        tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == tz_name), tz_name)
        await query.message.reply_text(
            f"✅ Часовой пояс установлен: {tz_label}\n"
            f"Текущее время у вас: {now.strftime('%H:%M')}",
            reply_markup=get_main_menu()
        )
    except:
        await query.message.reply_text(
            f"✅ Часовой пояс установлен: {tz_name}",
            reply_markup=get_main_menu()
        )

async def handle_location_global(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle location - global version for outside conversation"""
    try:
        location = update.message.location
        tz_name = timezone_from_location(location.latitude, location.longitude)

        if tz_name:
            set_user_timezone(update.effective_user.id, tz_name)
            try:
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)
                tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == tz_name), tz_name)
                msg = f"✅ Часовой пояс установлен: {tz_label}\nТекущее время у вас: {now.strftime('%H:%M')}"
            except:
                msg = f"✅ Часовой пояс установлен: {tz_name}"

            await update.message.reply_text(msg, reply_markup=get_main_menu())
            # Add button to change if wrong
            keyboard = [[InlineKeyboardButton("🔧 Выбрать вручную", callback_data="tz_method_manual")]]
            await update.message.reply_text(
                "Если время неверное:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text(
                "Не удалось определить таймзону.",
                reply_markup=get_main_menu()
            )
            keyboard = [[InlineKeyboardButton("🔧 Выбрать вручную", callback_data="tz_method_manual")]]
            await update.message.reply_text(
                "Выберите часовой пояс вручную:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    except Exception as e:
        logger.error(f"Error in handle_location_global: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте ещё раз.", reply_markup=get_main_menu())

async def show_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info(f"User {update.effective_user.id} opened tests menu")
    keyboard = [[InlineKeyboardButton(f'{test_names[i]}', callback_data=f'test_{i}')] for i in tests.keys()]
    keyboard.append([InlineKeyboardButton("📊 История тестов", callback_data="test_history")])
    keyboard.append([InlineKeyboardButton("Отмена", callback_data="test_cancel")])
    keyboard_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите тест:", reply_markup=keyboard_markup)
    return TEST

async def show_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user.username:
        await update.message.reply_text("У вас не установлен username в Telegram. Установите его в настройках.")
        return

    events = get_user_events(user.username)
    if not events:
        await update.message.reply_text("У вас нет запланированных встреч на ближайшие 2 недели.")
        return

    # Get user's timezone
    user_tz_name = get_user_timezone(user.id)
    user_tz = ZoneInfo(user_tz_name) if user_tz_name else None

    message = "Ваши ближайшие встречи:\n\n"
    for event in events:
        start = event.get('start', {}).get('dateTime', event.get('start', {}).get('date'))
        try:
            start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))

            # Convert to user's timezone if set
            if user_tz:
                start_dt = start_dt.astimezone(user_tz)
                now = datetime.now(user_tz)
            else:
                now = datetime.now(start_dt.tzinfo)

            date_str = start_dt.strftime('%d.%m %H:%M')
            delta = start_dt - now

            days = delta.days
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60

            parts = []
            if days > 0:
                parts.append(f"{days} дн.")
            if hours > 0:
                parts.append(f"{hours} ч.")
            if minutes > 0 and days == 0:
                parts.append(f"{minutes} мин.")

            relative_str = "через " + " ".join(parts) if parts else "скоро"
            time_str = f"{date_str} ({relative_str})"
        except:
            time_str = start
        message += f"- Сессия с Аней Алашеевой, {time_str}\n"

    # Show timezone info
    if user_tz_name:
        tz_label = next((t[1] for t in COMMON_TIMEZONES if t[0] == user_tz_name), user_tz_name)
        message += f"\n⏰ Время в вашем поясе: {tz_label}\n"
    else:
        message += "\n⚠️ Часовой пояс не установлен.\n"

    message += "\nСсылки для подключения:\n"
    message += '<a href="https://us06web.zoom.us/j/8144618404?pwd=UENCWHRIWVcwSUtVV0hxUUNtMHlzdz09">Zoom</a>\n'
    message += '<a href="https://meet.jit.si/anyaalasheevaroom">Jitsi</a>\n\n'
    message += "Реквизиты:\n<blockquote expandable>Тинькофф: +79879494485\nPayPal: ann.alasheeva@gmail.com\nGeorgian: GE77CD0360000044863324</blockquote>"

    await update.message.reply_text(message, parse_mode="HTML")

    # Add button to change timezone
    keyboard = [[InlineKeyboardButton("🔄 Изменить часовой пояс", callback_data="tz_method_change")]]
    await update.message.reply_text(
        "Время отображается неверно?",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Universal cancel - works from any state"""
    logger.info(f"User {update.effective_user.id} cancelled conversation")
    # Clear any user data from tests
    context.user_data.clear()
    await update.message.reply_text("Отменено.", reply_markup=get_main_menu())
    return ConversationHandler.END

async def timeout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle conversation timeout"""
    logger.info(f"Conversation timeout for user {update.effective_user.id if update.effective_user else 'unknown'}")
    context.user_data.clear()
    if update.message:
        await update.message.reply_text("Время ожидания истекло. Начните заново.", reply_markup=get_main_menu())
    return ConversationHandler.END

async def show_materials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show materials link"""
    message = '📚 <a href="https://drive.google.com/drive/folders/1WjQWCBeefSIhAENrOJYWX3KyDua6hmYI">Здесь</a> лежат книги Unmasking Autism и мои материалы про нейроотличное выгорание и восстановление.'
    await update.message.reply_text(message, parse_mode="HTML")

# === BROADCAST FOR ADMIN ===
ADMIN_ID = 5999980147
BROADCAST_WAITING_MESSAGE = 42

async def admin_mute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: disable notifications for a specific user"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /mute @username")
        return
    username = context.args[0].lstrip('@').lower()
    chat_id = get_chat_id(username)
    if not chat_id:
        await update.message.reply_text(f"Пользователь @{username} не найден в базе.")
        return
    set_notifications(chat_id, False)
    await update.message.reply_text(f"Уведомления для @{username} выключены.")

async def admin_unmute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: enable notifications for a specific user"""
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args:
        await update.message.reply_text("Использование: /unmute @username")
        return
    username = context.args[0].lstrip('@').lower()
    chat_id = get_chat_id(username)
    if not chat_id:
        await update.message.reply_text(f"Пользователь @{username} не найден в базе.")
        return
    set_notifications(chat_id, True)
    await update.message.reply_text(f"Уведомления для @{username} включены.")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: list all users with notification status"""
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    if not users:
        await update.message.reply_text("Нет зарегистрированных пользователей.")
        return
    lines = []
    for username, chat_id in users.items():
        enabled = is_notifications_enabled(chat_id)
        status = "🔔" if enabled else "🔕"
        lines.append(f"{status} @{username}")
    await update.message.reply_text("Пользователи:\n" + "\n".join(lines))

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start broadcast - admin only"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📨 Введите сообщение для рассылки всем пользователям:\n\n"
        "(Команда /cancel для отмены)"
    )
    return BROADCAST_WAITING_MESSAGE

async def broadcast_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send broadcast message to all users"""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда доступна только администратору.")
        return ConversationHandler.END

    message_text = update.message.text

    # Load users
    try:
        with open('data/registered_users.json', 'r', encoding='utf-8') as f:
            users = json.load(f)
    except FileNotFoundError:
        await update.message.reply_text("❌ Файл с пользователями не найден.")
        return ConversationHandler.END

    if not users:
        await update.message.reply_text("❌ Нет пользователей в базе.")
        return ConversationHandler.END

    # Send confirmation
    await update.message.reply_text(
        f"⏳ Рассылаю сообщение {len(users)} пользователям...\n\n"
        f"Текст:\n{message_text}"
    )

    success_count = 0
    failed_count = 0

    for username, chat_id in users.items():
        try:
            await context.bot.send_message(chat_id=chat_id, text=message_text)
            success_count += 1
            await asyncio.sleep(0.05)  # Small delay to avoid rate limiting
        except Exception as e:
            logger.error(f"Failed to send to {username} ({chat_id}): {e}")
            failed_count += 1

    result_message = f"✅ Успешно отправлено: {success_count}/{len(users)}"
    if failed_count > 0:
        result_message += f"\n❌ Ошибок: {failed_count}"

    await update.message.reply_text(result_message, reply_markup=get_main_menu())
    return ConversationHandler.END

async def test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Handle cancel
    if query.data == "test_cancel":
        await query.message.reply_text("Выбор теста отменён.", reply_markup=get_main_menu())
        return ConversationHandler.END

    # Handle history
    if query.data == "test_history":
        history = get_user_test_history(update.effective_user.id)
        if not history:
            await query.message.reply_text("У вас пока нет пройденных тестов.", reply_markup=get_main_menu())
        else:
            keyboard = []
            for i, entry in enumerate(history[-10:]):  # Last 10 tests
                test_label = test_names.get(entry['test'], entry['test'])
                date = entry['date'][:10]
                if entry['score'] is not None:
                    btn_text = f"{date} — {test_label}: {entry['score']} б."
                else:
                    btn_text = f"{date} — {test_label}"
                # Index from the end of history
                real_idx = len(history) - 10 + i if len(history) > 10 else i
                keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"history_{real_idx}")])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="test_cancel")])
            await query.message.reply_text("📊 Нажмите на тест для просмотра результатов:", reply_markup=InlineKeyboardMarkup(keyboard))
        return TEST  # Stay in TEST state to handle history clicks

    # Handle history item click
    if query.data.startswith("history_"):
        idx = int(query.data.split("_")[1])
        history = get_user_test_history(update.effective_user.id)
        if idx < len(history):
            entry = history[idx]
            test_name = entry['test']
            answers = entry['answers']
            date = entry['date'][:10]

            if test_name in ['sensoryProfile', 'sensoryProfileRu']:
                is_russian = test_name == 'sensoryProfileRu'
                message = f"📅 {date}\n\n" + get_sensory_profile_results(answers, is_russian)
            elif test_name == 'mqRu':
                message = f"📅 {date}\n\n" + get_mq_results(answers)
            elif test_name == 'raadsRRu':
                message = f"📅 {date}\n\n" + get_raads_r_results(answers)
            elif test_name == 'raads14Ru':
                message = f"📅 {date}\n\n" + get_raads_14_results(answers)
            else:
                test_label = test_names.get(test_name, test_name)
                message = f"📅 {date} — {test_label}\n\nСумма баллов: {entry.get('score', 'N/A')}"

            await query.message.reply_text(message, reply_markup=get_main_menu())
        else:
            await query.message.reply_text("Результат не найден.", reply_markup=get_main_menu())
        return ConversationHandler.END

    test_name = query.data.split("_")[1]
    context.user_data['test'] = test_name
    context.user_data['block'] = 0
    context.user_data['question'] = 0
    context.user_data['answers'] = {'0': {}}
    await query.message.reply_text(tests[test_name][0]['name'] + '\n\n' + tests[test_name][0]['description'])

    question = tests[test_name][0]["questions"][0]
    answers = list(question['a'].keys())
    keyboard = [[InlineKeyboardButton(i+1, callback_data=f'answer_{i}_{0}_{0}') for i, text in enumerate(answers)]]
    keyboard.append([InlineKeyboardButton("Завершить", callback_data="answer_quit")])
    keyboard_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text(f'1/{len(tests[test_name][0]["questions"])}\n\n{question["q"]}\n\n' + '\n'.join([f'{i+1}: {n}' for i, n in enumerate(answers)]), reply_markup=keyboard_markup)
    context.user_data['question'] += 1
    return QUESTION

async def question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # Handle quit
    if query.data == "answer_quit":
        await query.message.reply_text("Тест прерван.", reply_markup=get_main_menu())
        return ConversationHandler.END

    test_name = context.user_data['test']
    question_number = context.user_data['question']
    block_number = context.user_data['block']
    total_questions = len(tests[test_name][block_number]["questions"])

    # Handle back button
    if query.data == "answer_back":
        if question_number > 1:
            # Go back to previous question
            context.user_data['question'] -= 1
            question_number -= 1
            # Remove the answer for current question if exists
            if str(question_number) in context.user_data['answers'].get(str(block_number), {}):
                del context.user_data['answers'][str(block_number)][str(question_number)]
            # Show previous question
            prev_q_num = question_number - 1
            question = tests[test_name][block_number]["questions"][prev_q_num]
            answers = list(question['a'].keys())
            keyboard = [[InlineKeyboardButton(i+1, callback_data=f'answer_{i}_{prev_q_num}_{block_number}') for i, text in enumerate(answers)]]
            nav_row = []
            if prev_q_num > 0:
                nav_row.append(InlineKeyboardButton("← Назад", callback_data="answer_back"))
            nav_row.append(InlineKeyboardButton("Завершить", callback_data="answer_quit"))
            keyboard.append(nav_row)
            keyboard_markup = InlineKeyboardMarkup(keyboard)
            await query.message.reply_text(f'{prev_q_num + 1}/{total_questions}\n\n{question["q"]}\n\n' + '\n'.join([f'{i+1}: {n}' for i, n in enumerate(answers)]), reply_markup=keyboard_markup)
        else:
            await query.message.reply_text("Это первый вопрос, назад нельзя.")
        return QUESTION

    # Save answer
    context.user_data['answers'][query.data.split("_")[3]][query.data.split("_")[2]] = tests[test_name][block_number]["questions"][int(query.data.split("_")[2])]['a'][list(tests[test_name][block_number]["questions"][int(query.data.split("_")[2])]['a'].keys())[int(query.data.split("_")[1])]]

    if question_number >= len(tests[test_name][block_number]["questions"]):
        context.user_data['block'] += 1
        context.user_data['question'] = 0
        question_number = 0
        block_number = context.user_data['block']
        if context.user_data['block'] >= len(tests[test_name]):
            # Check if it's a sensory profile test
            if test_name in ['sensoryProfile', 'sensoryProfileRu']:
                is_russian = test_name == 'sensoryProfileRu'
                message = get_sensory_profile_results(context.user_data['answers'], is_russian)
                # Store sensory profile results
                store_test_result(
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    test_name=test_name,
                    answers=context.user_data['answers'],
                    score=None  # Sensory profile doesn't have a single score
                )
            elif test_name == 'mqRu':
                message = get_mq_results(context.user_data['answers'])
                # Calculate average for history display
                scores = context.user_data['answers'].get('0', {})
                valid_scores = [int(s) for s in scores.values() if int(s) > 0]
                mq_avg = round(sum(valid_scores) / len(valid_scores), 2) if valid_scores else 0
                store_test_result(
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    test_name=test_name,
                    answers=context.user_data['answers'],
                    score=mq_avg
                )
            elif test_name in ('raadsRRu', 'raads14Ru'):
                if test_name == 'raadsRRu':
                    message = get_raads_r_results(context.user_data['answers'])
                else:
                    message = get_raads_14_results(context.user_data['answers'])
                # Total score for history display
                scores = context.user_data['answers'].get('0', {})
                total = sum(int(s) for s in scores.values())
                store_test_result(
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    test_name=test_name,
                    answers=context.user_data['answers'],
                    score=total
                )
            else:
                message = "Тест завершен\n\nРезультаты:\n"
                total_score = 0
                for block_number, block in context.user_data['answers'].items():
                    message += f"{tests[test_name][int(block_number)]['name']}:\n"
                    for question_number, answer in block.items():
                        message += f"  Вопрос {question_number}: {answer}\n"
                    block_sum = sum(block.values())
                    total_score += block_sum
                    message += f"Сумма баллов: {block_sum}\n\n"
                # Store Beck or other test results
                store_test_result(
                    user_id=update.effective_user.id,
                    username=update.effective_user.username,
                    test_name=test_name,
                    answers=context.user_data['answers'],
                    score=total_score
                )
            await query.message.reply_text(message, reply_markup=get_main_menu())
            return ConversationHandler.END
        await query.message.reply_text(tests[test_name][block_number]['name'] + '\n\n' + tests[test_name][block_number]['description'])
        context.user_data['answers'][str(context.user_data['block'])] = {}
        total_questions = len(tests[test_name][block_number]["questions"])

    question = tests[test_name][block_number]["questions"][question_number]
    if (question_number-1 == int(query.data.split("_")[2]) and block_number == int(query.data.split("_")[3])) or question_number == 0:
        answers = list(question['a'].keys())
        keyboard = [[InlineKeyboardButton(i+1, callback_data=f'answer_{i}_{question_number}_{block_number}') for i, text in enumerate(answers)]]
        nav_row = []
        if question_number > 0:
            nav_row.append(InlineKeyboardButton("← Назад", callback_data="answer_back"))
        nav_row.append(InlineKeyboardButton("Завершить", callback_data="answer_quit"))
        keyboard.append(nav_row)
        keyboard_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text(f'{question_number + 1}/{total_questions}\n\n{question["q"]}\n\n' + '\n'.join([f'{i+1}: {n}' for i, n in enumerate(answers)]), reply_markup=keyboard_markup)
        context.user_data['question'] += 1
    else:
        await query.message.reply_text("Выберите один из вариантов")

    return QUESTION

def get_sensory_profile_results(answers, is_russian=False):
    """Generate personalized sensory profile recommendations"""
    # answers is dict like {'0': {question_idx: score, ...}}
    scores = answers.get('0', {})

    # Map questions to categories (25 questions total)
    # Q0: loud noises avoid, Q1: background noise avoid, Q2: sound seeking
    # Q3: bright lights avoid, Q4: visual clutter avoid, Q5: visual seeking
    # Q6: texture clothing avoid, Q7: social touch avoid, Q8: tactile seeking
    # Q9: smells avoid, Q10: smell seeking
    # Q11: food textures avoid, Q12: taste seeking, Q13: oral seeking
    # Q14: movement seeking, Q15: motion sickness, Q16: gravitational insecurity
    # Q17: interoception body, Q18: interoception fatigue
    # Q19: temperature
    # Q20: fidgeting, Q21: deep pressure
    # Q22: clumsiness, Q23: low registration, Q24: sensory fatigue

    auditory_avoid = (int(scores.get('0', 0)) + int(scores.get('1', 0))) / 2
    auditory_seek = int(scores.get('2', 0))
    visual_avoid = int(scores.get('3', 0))
    visual_clutter = int(scores.get('4', 0))
    visual_seek = int(scores.get('5', 0))
    tactile_avoid = int(scores.get('6', 0))
    social_touch = int(scores.get('7', 0))
    tactile_seek = int(scores.get('8', 0))
    olfactory_avoid = int(scores.get('9', 0))
    olfactory_seek = int(scores.get('10', 0))
    food_texture = int(scores.get('11', 0))
    taste_seek = int(scores.get('12', 0))
    oral_seeking = int(scores.get('13', 0))
    vestibular_seeking = int(scores.get('14', 0))
    motion_sickness = int(scores.get('15', 0))
    gravitational_insecurity = int(scores.get('16', 0))
    intero_body = int(scores.get('17', 0))
    intero_fatigue = int(scores.get('18', 0))
    temperature = int(scores.get('19', 0))
    movement = int(scores.get('20', 0))
    deep_pressure = int(scores.get('21', 0))
    clumsiness = int(scores.get('22', 0))
    low_registration = int(scores.get('23', 0))
    sensory_fatigue = int(scores.get('24', 0))

    if is_russian:
        result = "🎯 Ваш сенсорный профиль\n\n"

        # Auditory
        result += "👂 Слух:\n"
        if auditory_avoid >= 2.5:
            result += "• Избегатель: наушники с шумоподавлением, беруши в шумных местах\n"
            result += "• Белый шум или спокойная музыка для концентрации\n"
        elif auditory_avoid >= 1.5:
            result += "• Умеренная чувствительность: беруши для шумных ситуаций\n"
        if auditory_seek >= 2:
            result += "• Искатель: вам нужен звуковой фон! Музыка, подкасты, шум кафе\n"
        if auditory_avoid < 1.5 and auditory_seek < 2:
            result += "• Слух в балансе\n"

        # Visual
        result += "\n👁 Зрение:\n"
        if visual_avoid >= 2:
            result += "• Избегатель света: солнцезащитные очки, кепка, настройка яркости экранов\n"
        if visual_clutter >= 2:
            result += "• Избегатель хаоса: минималистичные пространства, организация рабочего места\n"
        if visual_seek >= 2:
            result += "• Искатель: вам нужны яркие цвета, динамичные визуалы, лава-лампы\n"
        if visual_avoid < 2 and visual_clutter < 2 and visual_seek < 2:
            result += "• Зрение в балансе\n"

        # Touch
        result += "\n🤚 Прикосновения:\n"
        if tactile_avoid >= 2:
            result += "• Избегатель текстур: мягкая одежда без швов и бирок\n"
        if social_touch >= 2:
            result += "• Избегатель касаний: предупреждайте о границах, альтернативы рукопожатию\n"
        if tactile_seek >= 2:
            result += "• Искатель: вам нужны текстуры! Мех, песок, слаймы, мягкие пледы\n"
        if tactile_avoid < 2 and social_touch < 2 and tactile_seek < 2:
            result += "• Тактильность в балансе\n"

        # Olfactory
        result += "\n👃 Обоняние:\n"
        if olfactory_avoid >= 2:
            result += "• Избегатель: избегайте духов, хорошая вентиляция\n"
        if olfactory_seek >= 2:
            result += "• Искатель: используйте любимые ароматы, аромадиффузоры, свечи\n"
        if olfactory_avoid < 2 and olfactory_seek < 2:
            result += "• Обоняние в балансе\n"

        # Taste/Oral
        result += "\n👅 Вкус и рот:\n"
        if food_texture >= 2:
            result += "• Избегатель текстур: избегайте неприятных — это нормально\n"
        if taste_seek >= 2:
            result += "• Искатель вкусов: острое, кислое, интенсивные вкусы — ваши друзья\n"
        if oral_seeking >= 2:
            result += "• Оральный поиск: жвачка, жевательные украшения, хрустящие снэки\n"
            result += "• Пить через трубочку тоже помогает\n"
        if food_texture < 2 and taste_seek < 2 and oral_seeking < 2:
            result += "• Вкус и рот в балансе\n"

        # Vestibular
        result += "\n🎢 Вестибулярная система:\n"
        if vestibular_seeking >= 2:
            result += "• Искатель: качели, танцы, спорт, карусели — ваши друзья!\n"
        if motion_sickness >= 2:
            result += "• Укачивание: смотрите вперёд, свежий воздух, имбирь\n"
            result += "• Выбирайте места с меньшей тряской (середина транспорта)\n"
        if gravitational_insecurity >= 2:
            result += "• Гравитационная неуверенность: страх когда ноги не на земле\n"
            result += "• Это реальная сенсорная особенность, не «просто страх»\n"
            result += "• Постепенная десенсибилизация, начинайте с малого\n"
        if vestibular_seeking < 2 and motion_sickness < 2 and gravitational_insecurity < 2:
            result += "• Вестибулярная система в балансе\n"

        # Interoception
        result += "\n💓 Интероцепция (ощущение тела):\n"
        intero_avg = (intero_body + intero_fatigue) / 2
        if intero_avg >= 2:
            result += "• Ставьте напоминания поесть, попить, отдохнуть\n"
            result += "• Регулярный режим дня, сканирование тела\n"
            result += "• Важно: чувствительность может быть высокой, но дискомфорт такой сильный, что приходится диссоциировать\n"
            result += "• Диссоциация — защитный механизм от перегрузки\n"
            result += "• При крайней степени — дереализация, это пугает, но с помощью психотерапии процесс можно сделать управляемым\n"
        elif intero_avg >= 1:
            result += "• Иногда пропускаете сигналы — будьте внимательнее\n"
        else:
            result += "• Вы хорошо чувствуете своё тело\n"

        # Temperature
        result += "\n🌡 Температура:\n"
        if temperature >= 2:
            result += "• Одевайтесь слоями, имейте при себе кофту/веер\n"
        else:
            result += "• Температура вас не особо беспокоит\n"

        # Movement/Proprioception
        result += "\n🏃 Движение и проприоцепция:\n"
        if movement >= 2:
            result += "• Потребность двигаться: перерывы, фиджеты, работа стоя\n"
        if deep_pressure >= 2:
            result += "• Глубокое давление: утяжелённое одеяло, крепкие объятия, массаж\n"
            result += "• «Тяжёлая работа»: двигать мебель, толкать стену, носить тяжёлое\n"
            result += "• Гамак из бифлекса, попросить ребёнка посидеть на спине\n"
        if clumsiness >= 2:
            result += "• Моторная неуклюжесть: это связано с проприоцепцией\n"
            result += "• Помогает: спорт, плавание, йога для улучшения чувства тела\n"
        if movement < 2 and deep_pressure < 2 and clumsiness < 2:
            result += "• Вы легко сидите на месте\n"

        # Low registration
        if low_registration >= 2:
            result += "\n📡 Низкая регистрация:\n"
            result += "• Вы можете пропускать сигналы — когда к вам обращаются, детали вокруг\n"
            result += "• Это не невнимательность, а особенность сенсорной обработки\n"
            result += "• Попросите говорить громче, привлекать внимание жестом\n"
            result += "• Создайте напоминания для важных вещей\n"

        # Sensory fatigue
        if sensory_fatigue >= 2:
            result += "\n😴 Сенсорная утомляемость:\n"
            result += "• Вы быстро устаёте от сенсорно насыщенной среды\n"
            result += "• Планируйте перерывы и «тихое время» после нагрузки\n"
            result += "• Имейте при себе «аптечку»: наушники, очки, что помогает\n"
            result += "• Связь с тревогой: перегрузка часто повышает тревожность\n"

        result += "\n\n🥗 Сенсорная диета:\n"
        result += "• Это не еда. Это планирование сенсорного опыта в течение дня\n"
        result += "• Баланс между избеганием перегрузки и поиском нужной стимуляции\n"
        result += "• Подбирается индивидуально под ваш профиль\n"

        result += "\n⚡ Сенсорная перегрузка и психика:\n"
        result += "• При перегрузке по любому каналу могут присоединяться психические процессы\n"
        result += "• Диссоциация — способ «выключиться» из невыносимого момента\n"
        result += "• Флешбэки — прошлый опыт сенсорной небезопасности может всплывать\n"
        result += "• Страх от самих этих феноменов усиливает диссоциацию — порочный круг\n"
        result += "• Это нормальная реакция на ненормальную нагрузку. С терапией — управляемо\n"

        result += "\n🔄 Универсальные стратегии:\n"
        result += "• Глубокое дыхание и приглушённый свет\n"
        result += "• Можно быть избегателем в одном и искателем в другом — это нормально!\n"
        result += "• Сенсорная чувствительность часто связана с тревогой — работа с тревогой помогает и наоборот\n"

        result += "\n✨ Сенсорная чувствительность — суперсила:\n"
        result += "• Вы замечаете детали, которые другие упускают\n"
        result += "• Глубокая обработка информации = творчество и эмпатия\n"

        result += "\n💡 По модели Winnie Dunn и материалам 101autism.com, firststeps.nz"

    else:
        result = "🎯 Your Sensory Profile\n\n"

        # Auditory
        result += "👂 Auditory:\n"
        if auditory_avoid >= 2.5:
            result += "• Avoider: noise-canceling headphones, earplugs\n"
        if auditory_seek >= 2:
            result += "• Seeker: you need sound! Music, podcasts, cafe buzz\n"
        if auditory_avoid < 1.5 and auditory_seek < 2:
            result += "• Balanced\n"

        # Visual
        result += "\n👁 Visual:\n"
        if visual_avoid >= 2:
            result += "• Light avoider: sunglasses, adjust screen brightness\n"
        if visual_clutter >= 2:
            result += "• Clutter avoider: minimalist spaces, organized workspace\n"
        if visual_seek >= 2:
            result += "• Seeker: you need bright colors, lava lamps, dynamic visuals\n"
        if visual_avoid < 2 and visual_clutter < 2 and visual_seek < 2:
            result += "• Balanced\n"

        # Touch
        result += "\n🤚 Touch:\n"
        if tactile_avoid >= 2:
            result += "• Texture avoider: soft seamless clothes, no tags\n"
        if social_touch >= 2:
            result += "• Touch avoider: communicate boundaries\n"
        if tactile_seek >= 2:
            result += "• Seeker: you need textures! Fur, sand, slime, soft blankets\n"
        if tactile_avoid < 2 and social_touch < 2 and tactile_seek < 2:
            result += "• Balanced\n"

        # Olfactory
        result += "\n👃 Olfactory:\n"
        if olfactory_avoid >= 2:
            result += "• Avoider: avoid perfumes, good ventilation\n"
        if olfactory_seek >= 2:
            result += "• Seeker: use your favorite scents, diffusers, candles\n"
        if olfactory_avoid < 2 and olfactory_seek < 2:
            result += "• Balanced\n"

        # Taste/Oral
        result += "\n👅 Taste & Oral:\n"
        if food_texture >= 2:
            result += "• Texture avoider: avoiding unpleasant textures is okay\n"
        if taste_seek >= 2:
            result += "• Taste seeker: spicy, sour, intense flavors are your friends\n"
        if oral_seeking >= 2:
            result += "• Oral seeker: gum, chew jewelry, crunchy snacks, straws\n"
        if food_texture < 2 and taste_seek < 2 and oral_seeking < 2:
            result += "• Balanced\n"

        # Vestibular
        result += "\n🎢 Vestibular:\n"
        if vestibular_seeking >= 2:
            result += "• Seeker: swings, dancing, sports, roller coasters!\n"
        if motion_sickness >= 2:
            result += "• Motion sickness: look ahead, fresh air, ginger\n"
        if gravitational_insecurity >= 2:
            result += "• Gravitational insecurity: fear when feet leave the ground\n"
            result += "• This is a real sensory feature, not 'just fear'\n"
            result += "• Gradual desensitization, start small\n"
        if vestibular_seeking < 2 and motion_sickness < 2 and gravitational_insecurity < 2:
            result += "• Balanced\n"

        # Interoception
        result += "\n💓 Interoception:\n"
        intero_avg = (intero_body + intero_fatigue) / 2
        if intero_avg >= 2:
            result += "• Set reminders to eat, drink, rest. Body scanning helps.\n"
            result += "• Note: sensitivity may be high, but discomfort so intense you dissociate\n"
            result += "• Dissociation is a protective mechanism from overload\n"
            result += "• At extreme levels — derealization. Therapy can help make it manageable\n"
        else:
            result += "• You sense your body well\n"

        # Temperature
        result += "\n🌡 Temperature:\n"
        if temperature >= 2:
            result += "• Dress in layers, keep sweater/fan handy\n"
        else:
            result += "• Temperature doesn't bother you much\n"

        # Movement/Proprioception
        result += "\n🏃 Movement & Proprioception:\n"
        if movement >= 2:
            result += "• Need to move: breaks, fidgets, standing desk\n"
        if deep_pressure >= 2:
            result += "• Deep pressure: weighted blanket, hugs, massage\n"
            result += "• Heavy work: moving furniture, pushing walls\n"
        if clumsiness >= 2:
            result += "• Motor clumsiness: related to proprioception\n"
            result += "• Helps: sports, swimming, yoga to improve body awareness\n"
        if movement < 2 and deep_pressure < 2 and clumsiness < 2:
            result += "• You sit still easily\n"

        # Low registration
        if low_registration >= 2:
            result += "\n📡 Low Registration:\n"
            result += "• You may miss signals — when people talk to you, details around you\n"
            result += "• This isn't inattention, it's a sensory processing feature\n"
            result += "• Ask people to speak louder, get your attention with gesture\n"

        # Sensory fatigue
        if sensory_fatigue >= 2:
            result += "\n😴 Sensory Fatigue:\n"
            result += "• You tire quickly in sensory-rich environments\n"
            result += "• Plan breaks and 'quiet time' after sensory load\n"
            result += "• Carry a 'kit': headphones, glasses, whatever helps\n"
            result += "• Connection to anxiety: overload often increases anxiety\n"

        result += "\n\n🥗 Sensory Diet:\n"
        result += "• Not about food. It's planning sensory experiences throughout the day\n"
        result += "• Balance between avoiding overload and seeking needed stimulation\n"
        result += "• Tailored individually to your profile\n"

        result += "\n⚡ Sensory Overload & the Mind:\n"
        result += "• Overload in any channel can trigger psychological processes\n"
        result += "• Dissociation — a way to 'switch off' from unbearable moment\n"
        result += "• Flashbacks — past sensory unsafety experiences may resurface\n"
        result += "• Fear of these phenomena intensifies dissociation — a vicious cycle\n"
        result += "• This is a normal response to abnormal load. With therapy — manageable\n"

        result += "\n🔄 Universal strategies:\n"
        result += "• Deep breathing and dim lighting\n"
        result += "• You can be avoider in one area and seeker in another — that's normal!\n"
        result += "• Sensory sensitivity is often linked to anxiety — working on one helps the other\n"

        result += "\n✨ Sensory sensitivity is a superpower:\n"
        result += "• You notice details others miss\n"
        result += "• Deep information processing = creativity and empathy\n"

        result += "\n💡 Based on Winnie Dunn model, 101autism.com, firststeps.nz"

    return result


# === MQ (Monotropism Questionnaire) SCORING ===
# Garau et al. (2023). N=1110.
MQ_AUTISTIC_MEAN = 4.15
MQ_AUTISTIC_SD = 0.347
MQ_ALLISTIC_MEAN = 3.19
MQ_ALLISTIC_SD = 0.578

def _normal_cdf(x, mean, sd):
    """CDF of normal distribution using error function."""
    return 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))

def get_mq_results(answers):
    """Generate MQ results with percentile comparison.

    answers: dict like {'0': {question_idx_str: score_int, ...}}
    Score 0 = N/A (excluded from average). Scores 1-5 = valid answers.
    Reverse scoring is already baked into mq_ru.json.
    """
    scores = answers.get('0', {})

    total = 0
    valid = 0
    for _idx, score in scores.items():
        s = int(score)
        if s > 0:  # N/A = 0, excluded
            total += s
            valid += 1

    if valid == 0:
        return "Недостаточно ответов для подсчёта результата."

    avg = total / valid

    # Percentile calculation
    auto_pct = _normal_cdf(avg, MQ_AUTISTIC_MEAN, MQ_AUTISTIC_SD) * 100
    allo_pct = _normal_cdf(avg, MQ_ALLISTIC_MEAN, MQ_ALLISTIC_SD) * 100

    result = "📊 Результаты: Опросник монотропизма (MQ)\n\n"
    result += f"Средний балл: {avg:.2f} из 5.00\n"
    result += f"Учтено ответов: {valid} из 47\n\n"

    # Interpretation
    if avg >= 3.91:
        result += "🔴 Результат указывает на выраженную монотропность.\n"
        result += "Это означает сильный монотропный стиль внимания — "
        result += "глубокая фокусировка, трудности с переключением, "
        result += "интенсивные интересы.\n\n"
    elif avg >= 3.19:
        result += "🟡 Результат в промежуточной зоне — есть монотропные черты.\n\n"
    else:
        result += "🟢 Результат ниже среднего по монотропности.\n\n"

    result += f"Ваш результат выше, чем у {auto_pct:.0f}% аутичных людей "
    result += f"и {allo_pct:.0f}% не-аутичных людей "
    result += "(по данным Garau et al., 2023, N=1110).\n\n"

    # Reference ranges
    result += "Референсные значения:\n"
    result += f"• Аутичная выборка: M={MQ_AUTISTIC_MEAN}, SD={MQ_AUTISTIC_SD}\n"
    result += f"• Не-аутичная выборка: M={MQ_ALLISTIC_MEAN}, SD={MQ_ALLISTIC_SD}\n\n"

    result += "⚠️ Это не диагностический инструмент. "
    result += "Результат описывает стиль внимания, а не наличие/отсутствие диагноза."

    return result


# === RAADS-R SCORING ===
# Ritvo et al. (2011). N=779.
RAADS_R_NORM_MEAN = 25.95
RAADS_R_NORM_SD = 16.04
RAADS_R_ASD_MEAN = 133.81
RAADS_R_ASD_SD = 37.72

# Subscale definitions: 1-indexed question numbers
RAADS_R_SUBSCALES = {
    "Social Relatedness": {
        "items": [1, 3, 5, 6, 8, 11, 12, 14, 17, 18, 20, 21, 22, 23, 25, 26,
                  28, 31, 37, 38, 39, 43, 44, 45, 47, 48, 53, 54, 55, 60, 61,
                  64, 68, 69, 72, 76, 77, 79, 80],
        "max": 117,
        "cutoff": 31,
        "label_ru": "Социальные отношения",
    },
    "Circumscribed Interests": {
        "items": [9, 13, 24, 30, 32, 40, 41, 50, 52, 56, 63, 70, 75, 78],
        "max": 42,
        "cutoff": 15,
        "label_ru": "Ограниченные интересы",
    },
    "Language": {
        "items": [2, 7, 15, 27, 35, 58, 66],
        "max": 21,
        "cutoff": 4,
        "label_ru": "Язык",
    },
    "Sensory Motor": {
        "items": [4, 10, 16, 19, 29, 33, 34, 36, 42, 46, 49, 51, 57, 59,
                  62, 65, 67, 71, 73, 74],
        "max": 60,
        "cutoff": 16,
        "label_ru": "Сенсомоторная сфера",
    },
}

def get_raads_r_results(answers):
    """Generate RAADS-R results with subscales and percentiles."""
    scores = answers.get('0', {})
    if not scores:
        return "Недостаточно ответов для подсчёта результата."

    total = sum(int(s) for s in scores.values())

    # Percentiles
    norm_pct = _normal_cdf(total, RAADS_R_NORM_MEAN, RAADS_R_NORM_SD) * 100
    asd_pct = _normal_cdf(total, RAADS_R_ASD_MEAN, RAADS_R_ASD_SD) * 100

    result = "📊 Результаты: RAADS-R\n\n"
    result += f"Общий балл: {total} из 240\n"
    result += f"Ответов: {len(scores)} из 80\n\n"

    # Interpretation
    if total >= 65:
        result += "🔴 Результат на уровне или выше порога (65), что согласуется с аутистическим спектром.\n\n"
    else:
        result += "🟢 Результат ниже порога (65).\n\n"

    # Subscales
    result += "Субшкалы:\n"
    for name, info in RAADS_R_SUBSCALES.items():
        # items are 1-indexed, scores dict keys are 0-indexed strings
        sub_total = sum(int(scores.get(str(i - 1), 0)) for i in info["items"])
        above = "⬆" if sub_total >= info["cutoff"] else ""
        result += f"• {info['label_ru']}: {sub_total}/{info['max']} (порог: {info['cutoff']}) {above}\n"

    result += f"\nПерцентили:\n"
    result += f"• Выше {norm_pct:.0f}% нормативной выборки (M={RAADS_R_NORM_MEAN}, SD={RAADS_R_NORM_SD})\n"
    result += f"• Перцентиль в аутичной выборке: {asd_pct:.0f}% (M={RAADS_R_ASD_MEAN}, SD={RAADS_R_ASD_SD})\n"
    result += "(Ritvo et al., 2011, N=779)\n\n"

    result += "⚠️ Это не диагностический инструмент. "
    result += "Результат не заменяет профессиональную оценку."

    return result


# === RAADS-14 SCORING ===
# Eriksson et al. (2013).
RAADS_14_SUBSCALES = {
    "Mentalizing Deficits": {
        "items": [1, 4, 9, 11, 12, 13, 14],
        "label_ru": "Трудности ментализации",
    },
    "Sensory Reactivity": {
        "items": [2, 7, 10],
        "label_ru": "Сенсорная реактивность",
    },
    "Social Anxiety": {
        "items": [3, 5, 6, 8],
        "label_ru": "Социальная тревожность",
    },
}

def get_raads_14_results(answers):
    """Generate RAADS-14 results with subscales."""
    scores = answers.get('0', {})
    if not scores:
        return "Недостаточно ответов для подсчёта результата."

    total = sum(int(s) for s in scores.values())

    result = "📊 Результаты: RAADS-14 Screen\n\n"
    result += f"Общий балл: {total} из 42\n"
    result += f"Ответов: {len(scores)} из 14\n\n"

    # Interpretation
    if total >= 14:
        result += "🔴 Результат на уровне или выше порога (14) — положительный скрининг.\n"
        if total >= 25:
            result += "Балл значительно выше порога, что типично для людей с аутизмом "
            result += "(медиана аутичной выборки: 32).\n\n"
        else:
            result += "Для сравнения: медиана СДВГ-выборки 15, аутичной — 32.\n\n"
    else:
        result += "🟢 Результат ниже порога (14).\n\n"

    # Subscales
    result += "Субшкалы:\n"
    for name, info in RAADS_14_SUBSCALES.items():
        sub_total = sum(int(scores.get(str(i - 1), 0)) for i in info["items"])
        sub_max = len(info["items"]) * 3
        result += f"• {info['label_ru']}: {sub_total}/{sub_max}\n"

    result += "\n(Eriksson et al., 2013)\n\n"

    result += "⚠️ Это скрининговый инструмент, не диагностический. "
    result += "Результат не заменяет профессиональную оценку."

    return result


async def show_notifications_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show notification settings with toggle button"""
    user_id = update.effective_user.id
    enabled = is_notifications_enabled(user_id)
    status = "включены" if enabled else "выключены"
    toggle_text = "🔕 Выключить напоминания" if enabled else "🔔 Включить напоминания"
    keyboard = [[InlineKeyboardButton(toggle_text, callback_data="notif_toggle")]]
    await update.message.reply_text(
        f"Напоминания о сессиях: {status}.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_notif_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle notifications on/off"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    current = is_notifications_enabled(user_id)
    set_notifications(user_id, not current)
    new_status = "включены" if not current else "выключены"
    toggle_text = "🔕 Выключить напоминания" if not current else "🔔 Включить напоминания"
    keyboard = [[InlineKeyboardButton(toggle_text, callback_data="notif_toggle")]]
    await query.message.edit_text(
        f"Напоминания о сессиях: {new_status}.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pass

# === MAIN ===
def main():
    # Load data from GitHub on startup
    print("Loading data from GitHub...")
    init_data_from_github()

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

    # Main conversation handler for tests
    test_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Тесты$"), show_tests)],
        states={
            TEST: [
                CallbackQueryHandler(test, pattern="^test_"),
                CallbackQueryHandler(test, pattern="^history_"),
            ],
            QUESTION: [CallbackQueryHandler(question, pattern="^answer_")],
            FINISH: [CallbackQueryHandler(finish)],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
            MessageHandler(filters.Regex("^Тесты$"), show_tests),  # Allow restart
        ],
        per_message=False,
        conversation_timeout=600,  # 10 minutes timeout
    )

    # Timezone conversation handler
    tz_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🕐 Часовой пояс$"), timezone_command)],
        states={
            TZ_WAITING_LOCATION: [
                MessageHandler(filters.LOCATION, handle_location),
                MessageHandler(filters.Regex("Назад"), handle_back_to_menu),
            ],
            TZ_WAITING_CONFIRM: [
                CallbackQueryHandler(handle_tz_confirm, pattern="^tz_confirm_"),
            ],
            TZ_WAITING_MANUAL: [
                CallbackQueryHandler(handle_tz_callback, pattern="^tz_"),
            ],
            ConversationHandler.TIMEOUT: [MessageHandler(filters.ALL, timeout_handler)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        per_message=False,
        conversation_timeout=300,  # 5 minutes timeout
    )

    # Broadcast conversation handler (admin only)
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler("broadcast", broadcast_start)],
        states={
            BROADCAST_WAITING_MESSAGE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, broadcast_send),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start),
        ],
        per_message=False,
        conversation_timeout=300,
    )

    # Global cancel - works even outside conversations
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("mute", admin_mute))
    application.add_handler(CommandHandler("unmute", admin_unmute))
    application.add_handler(CommandHandler("users", admin_users))
    application.add_handler(broadcast_handler)
    application.add_handler(test_handler)
    application.add_handler(tz_handler)
    # Global timezone callbacks - for buttons shown outside conversation (e.g., after /start)
    application.add_handler(CallbackQueryHandler(handle_tz_method_callback_global, pattern="^tz_method_"))
    application.add_handler(CallbackQueryHandler(handle_tz_callback_global, pattern="^tz_"))
    # Global location handler - for location sent outside conversation
    application.add_handler(MessageHandler(filters.LOCATION, handle_location_global))
    application.add_handler(MessageHandler(filters.Regex("^Мои встречи$"), show_events))
    application.add_handler(MessageHandler(filters.Regex("^📚 Материалы$"), show_materials))
    application.add_handler(MessageHandler(filters.Regex("^🔔 Уведомления$"), show_notifications_settings))
    application.add_handler(CallbackQueryHandler(handle_notif_toggle, pattern="^notif_toggle$"))
    application.add_handler(MessageHandler(filters.Regex("Назад"), handle_back_to_menu))

    print("Bot started! Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
