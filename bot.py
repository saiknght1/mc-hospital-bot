# telegrambot logic with RAG memory + better retrieval + back/stop navigation
import telebot
import pymysql
from datetime import datetime
import os
from collections import defaultdict
from dotenv import load_dotenv
from urllib.parse import quote_plus

# LangChain imports
from langchain.chat_models import ChatOpenAI
from langchain.chains import ConversationalRetrievalChain
from langchain.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate

load_dotenv()

booking_done = {}
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

PAYMENT_SERVER_URL = os.getenv("PAYMENT_SERVER_URL", "https://mc-hospital-bot.up.railway.app")

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )





# ====== STATE MANAGEMENT ======
user_state = {}    # chat_id -> state
TEMP_BOOKING = {}  # chat_id -> booking info across steps
valid_options = {} # chat_id -> list of valid inputs for current step
KEYWORDS = ["reschedule", "cancel", "refund", "money back", "Reschedule"]

stop_MSG = "❌ Booking process stopped. You can type /book to start again."
NAV_HINT = "\n💡 You can type 'back' to go to the previous step or type 'stop' to stop the Appointment Booking process."

# ====== RAG SETUP with per-user memory ======
FAQ_DOC_PATH = "mc_hospital_faq.txt"

def setup_rag():
    loader = TextLoader(FAQ_DOC_PATH, encoding="utf-8")
    docs = loader.load()
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    docs_split = splitter.split_documents(docs)
    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    vectorstore = FAISS.from_documents(docs_split, embeddings)
    retriever = vectorstore.as_retriever(search_type="similarity", search_kwargs={"k": 6, "fetch_k": 10})
    user_memories = defaultdict(lambda: ConversationBufferMemory(memory_key="chat_history", return_messages=True))

    prompt_template = """
You are a helpful hospital booking assistant.
Always answer politely and clearly.
Use only the context below to answer questions.
If unsure, say you are not certain and suggest contacting support.

Context:
{context}

Question: {question}
Answer:
"""
    QA_PROMPT = PromptTemplate.from_template(prompt_template)

    def get_user_qa_chain(chat_id):
        return ConversationalRetrievalChain.from_llm(
            llm=ChatOpenAI(
                openai_api_key=OPENAI_API_KEY,
                model_name="gpt-4o",
                temperature=0,
                max_tokens=400
            ),
            retriever=retriever,
            memory=user_memories[chat_id],
            combine_docs_chain_kwargs={"prompt": QA_PROMPT}
        )

    def get_rag_answer(chat_id, question_text):
        try:
            chain = get_user_qa_chain(chat_id)
            result = chain({"question": question_text})
            answer = result.get("answer", "").strip()
            # Correct fallback check
            if (not answer) or ("i am not sure" in answer.lower()) or ("not certain" in answer.lower()):
                return ("For such queries please connect with our customer support at the helpline "
                        "number +91-72-5938-6897, email: contact@mchospital.in.")
            return answer
        except Exception as e:
            print("Error in RAG answer:", e)
            return "Sorry, I could not find an answer to your question."

    return get_rag_answer

try:
    get_rag_answer = setup_rag()
except Exception as e:
    print("Error setting up RAG FAQ system:", e)
    def get_rag_answer(*_):
        return "Sorry, FAQ system is not available right now."

# ====== Keyword helper ======
def contains_keywords(text):
    return any(keyword in text.lower() for keyword in ["reschedule", "cancel", "refund", "money back"])

# ====== General stop utility ======
def do_stop(chat_id):
    user_state.pop(chat_id, None)
    TEMP_BOOKING.pop(chat_id, None)
    valid_options.pop(chat_id, None)

# ====== BOT HANDLERS ======
@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Welcome to Hospital Booking Bot!\n"
        "Type /book to start appointment booking, or just ask your questions."
    )

@bot.message_handler(
    func=lambda m: (
        not user_state.get(m.chat.id)
        and m.text
        and m.text.strip().lower() != "/book"
        and all(keyword not in m.text.strip().lower() for keyword in KEYWORDS)
    )
)
def handle_faq(message):
    chat_id = message.chat.id
    answer = get_rag_answer(chat_id, message.text)
    bot.reply_to(message, answer)
    if not booking_done.get(chat_id, False):
        bot.reply_to(message, "You can type /book to start with Appointment Booking Process")

# ==========================
#      BOOKING FLOW
# ==========================


def phone_is_blocked(phone_no: str) -> bool:
    """Returns True if the phone number has any booking row with payment_status='contact_request'."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bookings WHERE phone_no=%s AND payment_status='contact_request' LIMIT 1",
                (phone_no,)
            )
            return cur.fetchone() is not None
    except Exception as e:
        print(f"phone_is_blocked error: {e}")
        return False
    finally:
        if conn:
            conn.close()


def list_specialties(chat_id: int, message):
    """Lists specialties and moves state forward."""
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM specialties")
            specialties = cur.fetchall()
        if not specialties:
            bot.reply_to(message, "No specialties found. Please try again later.")
            return

        reply = "Please choose a speciality by typing its ID:\n"
        ids = []
        for sp in specialties:
            reply += f"{sp['id']}. {sp['name']}\n"
            ids.append(str(sp['id']))

        bot.reply_to(message, reply + "\n💡 Type 'stop' to stop the booking process.")
        user_state[chat_id] = "choosing_speciality"
        valid_options[chat_id] = ids
    except Exception as e:
        print(f"list_specialties error: {e}")
        bot.reply_to(message, "Sorry, something went wrong. Please try again later.")
    finally:
        if conn:
            conn.close()


@bot.message_handler(commands=["book"])
def start_booking(message):
    chat_id = message.chat.id
    booking_done[chat_id] = False
    TEMP_BOOKING[chat_id] = {}

    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT phone_no, payment_status FROM bookings WHERE user_id=%s",
                (chat_id,)
            )
            user_bookings = cur.fetchall()

        # Filter invalid numbers
        user_bookings = [b for b in user_bookings if b["phone_no"] and b["phone_no"].strip()]

        if not user_bookings:
            # No prior numbers → ask for a new one directly
            bot.reply_to(
                message,
                "📱 You don't have a saved phone number.\n"
                "Please type the 10-digit phone number you want to use for this booking."
            )
            user_state[chat_id] = "choosing_phone"
            valid_options[chat_id] = []  # no saved options
            return

        blocked_numbers = {b["phone_no"] for b in user_bookings if b["payment_status"] == "contact_request"}
        allowed_numbers = sorted({b["phone_no"] for b in user_bookings if b["phone_no"] not in blocked_numbers})

        # Show all (labeling blocked), but only allow picking from allowed or entering a new number
        lines = []
        seen = set()
        for b in user_bookings:
            num = b["phone_no"]
            if num in seen:
                continue
            seen.add(num)
            tag = " (Contact Request)" if num in blocked_numbers else ""
            lines.append(f"• {num}{tag}")

        bot.reply_to(
            message,
            "These phone numbers are linked to your account:\n\n"
            + "\n".join(lines) +
            "\n\n👉 Type one of the numbers from above which doesnt have a pending contact request, or enter a NEW 10-digit phone number."
        )

        user_state[chat_id] = "choosing_phone"
        valid_options[chat_id] = allowed_numbers  # only allowed as quick picks

    except Exception as e:
        print(f"Error in start_booking: {e}")
        bot.reply_to(message, "Sorry, something went wrong. Please try again later.")
    finally:
        if conn:
            conn.close()
@bot.message_handler(func=lambda msg: user_state.get(msg.chat.id) == "choosing_phone")
def handle_phone_choice(message):
    chat_id = message.chat.id
    raw = (message.text or "").strip()

    if raw.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    # If user typed one of the allowed saved numbers → use it
    allowed = set(valid_options.get(chat_id, []))
    if raw in allowed:
        # still double-check that it isn't blocked in DB (defensive)
        if phone_is_blocked(raw):
            bot.reply_to(message, f"❌ {raw} is currently blocked due to a pending request. Please use another number.")
            return
        TEMP_BOOKING[chat_id]["phone_no"] = raw
        bot.reply_to(message, f"✅ Using {raw} for this booking.")
        return list_specialties(chat_id, message)

    # Otherwise treat as a NEW number candidate
    if not raw.isdigit() or len(raw) != 10:
        bot.reply_to(message, "⚠️ Please enter a valid 10-digit phone number (digits only).")
        return

    # Check if this number itself is blocked (even if not linked before)
    if phone_is_blocked(raw):
        bot.reply_to(message, f"❌ {raw} is currently blocked due to a pending reschedule/cancellation request. Please use another number.")
        return

    # Accept new number (do NOT insert into DB here; payment success should create the row)
    TEMP_BOOKING[chat_id]["phone_no"] = raw
    bot.reply_to(message, f"✅ New phone number {raw} selected for this booking.")
    return list_specialties(chat_id, message)



def is_paid_user(message):
    chat_id = message.chat.id
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT payment_status FROM bookings WHERE user_id = %s", (chat_id,))
            row = cur.fetchone()
            return row and row["payment_status"] == "paid"
    finally:
        if conn:
            conn.close()

@bot.message_handler(func=lambda m: is_paid_user(m) and contains_keywords(m.text or ""))
def handle_keywords(message):
    bot.send_message(message.chat.id, "📞 Please enter your registered phone number:")
    bot.register_next_step_handler(message, process_phone_number)

def process_phone_number(message):
    chat_id = message.chat.id
    phone_verify = (message.text or "").strip()
    if not phone_verify.isdigit() or len(phone_verify) != 10:
        bot.send_message(message.chat.id, "❌ Please enter a valid 10-digit number:")
        bot.register_next_step_handler(message, process_phone_number)
        return
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM bookings WHERE phone_no = %s", (phone_verify,))
            bookings_found = cur.fetchall()
            if bookings_found:
                cur.execute("""
                    UPDATE bookings
                    SET payment_status = %s
                    WHERE phone_no = %s
                """, ("contact_request", phone_verify))
                conn.commit()
                bot.send_message(message.chat.id, "✅ Our team will call you soon.")
                user_state[chat_id] = "contact_request"
            else:
                bot.send_message(message.chat.id, "❌ No bookings found. Call +91 7259356897 for help ot type 'cancel' again in case you entered wrong Phone Number or continue with Booking process ")
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ An error occurred: {e}")
    finally:
        if conn:
            conn.close()





# --- choosing_speciality ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_speciality")
def handle_choosing_speciality(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().rstrip(".")

    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["speciality"] = text
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, fees FROM doctors WHERE specialty_id = %s", (text,))
                doctors = cur.fetchall()
            conn.close()

            if not doctors:
                bot.reply_to(message, "No doctors found for this speciality. Type 'back' to reselect or 'stop'.")
                return

            reply = "Please choose a doctor by typing their ID:\n"
            doctor_ids = []
            for doc in doctors:
                reply += f"{doc['id']}. {doc['name']} — Fee: ₹{doc['fees']}\n"
                doctor_ids.append(str(doc['id']))

            bot.reply_to(message, reply + NAV_HINT)
            user_state[chat_id] = "choosing_doctor"
            valid_options[chat_id] = doctor_ids
        except Exception as e:
            print(f"Error fetching doctors: {e}")
            bot.reply_to(message, "Sorry, something went wrong.")
    else:
        # Back not applicable here; give RAG assist
        answer = get_rag_answer(chat_id, text)
        bot.reply_to(message, f"💡 {answer}\n\nPlease enter a valid speciality ID to move ahead or type 'stop' to stop the Booking Process.")

# --- choosing_doctor ---
# --- choosing_doctor ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_doctor")
def handle_choosing_doctor(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().rstrip(".")

    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    if text.lower() == "back":
        # (existing back logic unchanged...)
        return

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["doctor_id"] = int(text)
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT fees, name FROM doctors WHERE id=%s", (text,))
                result = cur.fetchone()
            conn.close()
            if result:
                TEMP_BOOKING[chat_id]["fees"] = result["fees"]
                TEMP_BOOKING[chat_id]["doc_name"] = result["name"]
                bot.reply_to(message, f"Consultation Fee for this doctor: ₹{result['fees']}")
        except Exception as e:
            print(f"Error fetching doctor fees: {e}")

        # 🔹 Modified line with doctor roster availability notice
        bot.reply_to(
            message,
            "Please enter the date you want to book (YYYY-MM-DD):\n"
            "📅 Doctor roster available for booking until 31-Aug-2025."
            + NAV_HINT
        )
        user_state[chat_id] = "choosing_date"
        valid_options.pop(chat_id, None)
    else:
        answer = get_rag_answer(chat_id, text)
        bot.reply_to(message, f"💡 {answer}\n\nPlease enter a valid Doctor ID or type 'back'/'stop'.")


# --- choosing_date ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_date")
def handle_choosing_date(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().rstrip(".")

    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    if text.lower() == "back":
        # Re-list doctors for selected speciality
        speciality = TEMP_BOOKING[chat_id].get("speciality")
        if not speciality:
            start_booking(message)
            return
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, fees FROM doctors WHERE specialty_id = %s", (speciality,))
                doctors = cur.fetchall()
            conn.close()

            reply = "Please choose a doctor by typing its ID:\n"
            doctor_ids = []
            for doc in doctors:
                reply += f"{doc['id']}. {doc['name']} — Fee: ₹{doc['fees']}\n"
                doctor_ids.append(str(doc['id']))
            user_state[chat_id] = "choosing_doctor"
            valid_options[chat_id] = doctor_ids
            TEMP_BOOKING[chat_id].pop("doctor_id", None)
            TEMP_BOOKING[chat_id].pop("fees", None)
            TEMP_BOOKING[chat_id].pop("doc_name", None)
            bot.reply_to(message, reply + NAV_HINT)
        except Exception as e:
            print(f"Error fetching doctors on back: {e}")
            bot.reply_to(message, "Sorry, something went wrong.")
        return


    try:
        booking_date = datetime.strptime(text, "%Y-%m-%d").date()
        today = datetime.now().date()
        now_time = datetime.now().time()

        # 🚫 Reject past dates
        if booking_date < today:
            bot.reply_to(message, "⚠️ You cannot book an appointment in the past.\n\nPlease enter today’s date or a future date (YYYY-MM-DD).")
            return

        TEMP_BOOKING[chat_id]["slot_date"] = booking_date

        conn = get_db_connection()
        with conn.cursor() as cur:
            if booking_date == today:
                # Only allow slots >= current time
                cur.execute("""
                    SELECT id, slot_time
                    FROM doctor_slots
                    WHERE doctor_id=%s AND slot_date=%s AND is_booked=0 AND slot_time >= %s
                """, (TEMP_BOOKING[chat_id]["doctor_id"], booking_date, now_time))
            else:
                # Future date → allow all free slots
                cur.execute("""
                    SELECT id, slot_time
                    FROM doctor_slots
                    WHERE doctor_id=%s AND slot_date=%s AND is_booked=0
                """, (TEMP_BOOKING[chat_id]["doctor_id"], booking_date))
            slots = cur.fetchall()
        conn.close()

        if not slots:
            bot.reply_to(message, "No available slots on this date. Try another date or type 'back' or 'stop'.")
            return

        reply = "Select a slot by typing its ID:\n"
        slot_ids = []
        for s in slots:
            reply += f"{s['id']}. {s['slot_time']}\n"
            slot_ids.append(str(s['id']))
        bot.reply_to(message, reply + NAV_HINT)
        user_state[chat_id] = "choosing_slot"
        valid_options[chat_id] = slot_ids

    except ValueError:
        answer = get_rag_answer(chat_id, text)
        bot.reply_to(message, f"💡 {answer}\n\nPlease use YYYY-MM-DD format to continue booking or type 'back'/'stop'.")


# --- choosing_slot ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_slot")
def handle_choosing_slot(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().rstrip(".")

    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    if text.lower() == "back":
        # Ask for date again
        user_state[chat_id] = "choosing_date"
        valid_options.pop(chat_id, None)
        TEMP_BOOKING[chat_id].pop("slot_id", None)
        bot.reply_to(message, "Please enter the date you want to book (YYYY-MM-DD):" + NAV_HINT)
        return

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["slot_id"] = int(text)
        bot.reply_to(message, "Please enter your full name:\n" + NAV_HINT)
        user_state[chat_id] = "entering_name"
        valid_options.pop(chat_id, None)
    else:
        answer = get_rag_answer(chat_id, text)
        bot.reply_to(message, f"💡 {answer}\n\nPlease enter a valid Slot ID or type 'back'/'stop'.")

# --- entering_name ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "entering_name")
def handle_entering_name(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return
    if text.lower() == "back":
        # Re-list slots for doctor/date
        doctor_id = TEMP_BOOKING[chat_id].get("doctor_id")
        booking_date = TEMP_BOOKING[chat_id].get("slot_date")
        if not (doctor_id and booking_date):
            user_state[chat_id] = "choosing_date"
            bot.reply_to(message, "Please enter the date you want to book (YYYY-MM-DD)\n(Note: Doctors Roster available for booking from 2025-08-19 to 2025-08-31)\n" + NAV_HINT)
            return
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, slot_time
                    FROM doctor_slots
                    WHERE doctor_id=%s AND slot_date=%s AND is_booked=0
                """, (doctor_id, booking_date))
                slots = cur.fetchall()
            conn.close()
            if not slots:
                user_state[chat_id] = "choosing_date"
                bot.reply_to(message, "No available slots on this date. Enter another date (YYYY-MM-DD):" + NAV_HINT)
                return
            reply = "Select a slot by typing its ID:\n"
            slot_ids = []
            for s in slots:
                reply += f"{s['id']}. {s['slot_time']}\n"
                slot_ids.append(str(s['id']))
            user_state[chat_id] = "choosing_slot"
            valid_options[chat_id] = slot_ids
            TEMP_BOOKING[chat_id].pop("slot_id", None)
            bot.reply_to(message, reply + NAV_HINT)
        except Exception as e:
            print(f"Error fetching slots on back: {e}")
            bot.reply_to(message, "Sorry, something went wrong.")
        return

    # Save name
    TEMP_BOOKING[chat_id]["name"] = text

    # --------- PATCH BEGINS HERE ---------
    # If phone_no is already saved in TEMP_BOOKING (from earlier selection), SKIP asking again
    phone_already_chosen = TEMP_BOOKING[chat_id].get("phone_no")
    if phone_already_chosen:
        doctor_id = TEMP_BOOKING[chat_id]["doctor_id"]
        slot_id = TEMP_BOOKING[chat_id]["slot_id"]
        fee = TEMP_BOOKING[chat_id].get("fees", 0)
        name_q = quote_plus(TEMP_BOOKING[chat_id].get("name", ""))
        phone_q = quote_plus(phone_already_chosen)
        fee_q = quote_plus(str(fee))
        payment_url = f"{PAYMENT_SERVER_URL}/pay/{chat_id}/{doctor_id}/{slot_id}?name={name_q}&phone={phone_q}&fee={fee_q}"
        bot.reply_to(
            message,
            f"Doctor: {TEMP_BOOKING[chat_id].get('doc_name')}\n"
            f"Consultation Fee: ₹{fee}\n"
            f"💳 Please complete your payment here: {payment_url}"
        )
        user_state[chat_id] = "paid"
        valid_options.pop(chat_id, None)
        TEMP_BOOKING.pop(chat_id, None)
        return
    # --------- PATCH ENDS HERE ---------

    # Otherwise, ask for 10-digit phone number as usual
    bot.reply_to(message, "Please enter your 10 digit phone number:" + NAV_HINT)
    user_state[chat_id] = "entering_phone"


# --- entering_phone ---
@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "entering_phone")
def handle_entering_phone(message):
    chat_id = message.chat.id
    text = (message.text or "").strip().rstrip(".")

    if text.lower() == "stop":
        do_stop(chat_id)
        bot.reply_to(message, stop_MSG)
        return

    if text.lower() == "back":
        user_state[chat_id] = "entering_name"
        bot.reply_to(message, "Please enter your full name:" + NAV_HINT)
        return

    if text.isdigit() and len(text) == 10:
        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                # 🚫 Check if this number is blocked
                cur.execute(
                    "SELECT payment_status FROM bookings WHERE phone_no=%s",
                    (text,)
                )
                existing = cur.fetchone()

            if existing and existing["payment_status"] == "contact_request":
                bot.reply_to(
                    message,
                    f"⚠️ This phone number ({text}) is currently blocked due to a pending reschedule/cancellation request.\n\n Type '\book' to book an appointment for different Number"
                    "Please wait for our support team to contact you or use a different phone number."
                )
                do_stop(chat_id)  # reset flow
                return

            # ✅ Continue normal flow
            TEMP_BOOKING[chat_id]["phone_no"] = text
            doctor_id = TEMP_BOOKING[chat_id]["doctor_id"]
            slot_id = TEMP_BOOKING[chat_id]["slot_id"]

            fee = TEMP_BOOKING[chat_id].get("fees", 0)
            name_q = quote_plus(TEMP_BOOKING[chat_id].get("name", ""))
            phone_q = quote_plus(text)
            fee_q = quote_plus(str(fee))

            payment_url = f"{PAYMENT_SERVER_URL}/pay/{chat_id}/{doctor_id}/{slot_id}?name={name_q}&phone={phone_q}&fee={fee_q}"
            bot.reply_to(
                message,
                f"Doctor: {TEMP_BOOKING[chat_id].get('doc_name')}\n"
                f"Consultation Fee: ₹{fee}\n"
                f"💳 Please complete your payment here: {payment_url}"
            )
            user_state[chat_id] = "paid"
            valid_options.pop(chat_id, None)
            TEMP_BOOKING.pop(chat_id, None)

        except Exception as e:
            print(f"Error checking phone number: {e}")
            bot.reply_to(message, "Sorry, something went wrong. Please try again later.")
        finally:
            if conn:
                conn.close()

    else:
        answer = get_rag_answer(chat_id, text)
        bot.reply_to(message, f"💡 {answer}\n\nPlease enter a valid 10 digit number or type 'back'/'stop'.")



@bot.message_handler(func=lambda m: is_paid_user(m) and not contains_keywords(m.text or ""))
def handle_entering_name(message):
    chat_id = message.chat.id
    text = (message.text or "").strip()
    answer = get_rag_answer(chat_id, text)
    bot.reply_to(
        message,
        f"💡 {answer}\n\nPlease type /book to book another appointment or type 'help' to cancel/reschedule appointments"
    )


# ==========================
#   KEYWORD: contact flow
# ==========================



# ==========================
#       FALLBACK
# ==========================
@bot.message_handler(func=lambda m: True)
def handle_fallback(message):
    chat_id = message.chat.id
    if user_state.get(chat_id):
        bot.reply_to(message, "Sorry, I didn't understand that. Please follow the instructions above." + NAV_HINT)
    else:
        bot.reply_to(message, "I’m not sure how to respond.\n💡 Type /book to book an appointment or ask your questions.")

if __name__ == "__main__":
    print("Bot started...")
    bot.infinity_polling()
