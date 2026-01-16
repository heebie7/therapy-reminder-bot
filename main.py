from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters
import json
import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from dotenv import load_dotenv

# Load .env file
load_dotenv()

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
    # English versions (hidden for now):
    # "beckEn": "Beck Depression Inventory (English)",
    # "sensoryProfile": "Sensory Profile",
}

with open("beck_ru.json", "r", encoding="utf-8") as f:
    tests["beckRu"] = json.load(f)
with open("sensory_profile_ru.json", "r", encoding="utf-8") as f:
    tests["sensoryProfileRu"] = json.load(f)
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

            # Format date/time nicely
            try:
                start_dt = datetime.fromisoformat(event_start.replace('Z', '+00:00'))
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
        [KeyboardButton("📚 Материалы"), KeyboardButton("🕐 Часовой пояс")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

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
TZ_WAITING_LOCATION, TZ_WAITING_MANUAL = range(10, 12)

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
        # Prompt for timezone
        await ask_timezone(update, context)
    else:
        welcome = "Добро пожаловать! Вы зарегистрированы для получения напоминаний о встречах."
        await update.message.reply_text(welcome, reply_markup=get_main_menu())
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
    """Handle received location"""
    try:
        location = update.message.location
        tz_name = timezone_from_location(location.latitude, location.longitude)

        if tz_name:
            set_user_timezone(update.effective_user.id, tz_name)
            try:
                tz = ZoneInfo(tz_name)
                now = datetime.now(tz)
                msg = f"✅ Часовой пояс установлен: {tz_name}\nТекущее время у вас: {now.strftime('%H:%M')}"
            except:
                msg = f"✅ Часовой пояс установлен: {tz_name}"
        else:
            msg = "Не удалось определить таймзону. Попробуйте ещё раз."

        await update.message.reply_text(msg, reply_markup=get_main_menu())
    except Exception as e:
        print(f"Error in handle_location: {e}")
        await update.message.reply_text("Произошла ошибка. Попробуйте ещё раз.", reply_markup=get_main_menu())

    return ConversationHandler.END

async def show_manual_tz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show manual timezone selection"""
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

    await update.message.reply_text(
        "Выберите ваш часовой пояс:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return TZ_WAITING_MANUAL

async def handle_manual_tz_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Select manually' button"""
    return await show_manual_tz(update, context)

async def handle_tz_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timezone selection callback"""
    query = update.callback_query
    await query.answer()

    if query.data == "tz_cancel":
        await query.message.reply_text("Выбор отменён.", reply_markup=get_main_menu())
        return ConversationHandler.END

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

async def show_tests(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        message += "\n⚠️ Часовой пояс не установлен. Нажмите «🕐 Часовой пояс» для настройки.\n"

    message += "\nСсылки для подключения:\n"
    message += '<a href="https://us06web.zoom.us/j/8144618404?pwd=UENCWHRIWVcwSUtVV0hxUUNtMHlzdz09">Zoom</a>\n'
    message += '<a href="https://meet.jit.si/anyaalasheevaroom">Jitsi</a>\n\n'
    message += "Реквизиты:\n<blockquote expandable>Тинькофф: +79879494485\nPayPal: ann.alasheeva@gmail.com\nGeorgian: GE77CD0360000044863324</blockquote>"

    await update.message.reply_text(message, parse_mode="HTML")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled")
    return ConversationHandler.END

async def show_materials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show materials link"""
    message = '📚 <a href="https://drive.google.com/drive/folders/1WjQWCBeefSIhAENrOJYWX3KyDua6hmYI">Здесь</a> лежат книги Unmasking Autism и мои материалы про нейроотличное выгорание и восстановление.'
    await update.message.reply_text(message, parse_mode="HTML")

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
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        per_message=False,
    )

    # Timezone conversation handler
    tz_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🕐 Часовой пояс$"), timezone_command)],
        states={
            TZ_WAITING_LOCATION: [
                MessageHandler(filters.LOCATION, handle_location),
                MessageHandler(filters.Regex("Назад"), handle_back_to_menu),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
        per_message=False,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(test_handler)
    application.add_handler(tz_handler)
    application.add_handler(MessageHandler(filters.Regex("^Мои встречи$"), show_events))
    application.add_handler(MessageHandler(filters.Regex("^📚 Материалы$"), show_materials))
    application.add_handler(MessageHandler(filters.Regex("Назад"), handle_back_to_menu))

    print("Bot started! Press Ctrl+C to stop.")
    application.run_polling()

if __name__ == "__main__":
    main()
