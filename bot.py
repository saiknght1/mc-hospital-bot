#telegrambot logic
import telebot
import pymysql
from datetime import datetime
import os
from langchain.chat_models import ChatOpenAI
from langchain.chains import RetrievalQA
from langchain.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

PAYMENT_SERVER_URL = "https://mc-hospital-bot.up.railway.app"  # Your Flask server URL

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))  # default to 3306 if not set
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
TEMP_BOOKING = {}  # chat_id -> booking info
valid_options = {} # chat_id -> list of valid inputs for current step
KEYWORDS = ["reschedule", "cancel", "refund", "money back"]
# ====== HELPER: Simple FAQ detector ======

def contains_keywords(text):
    text = text.lower()  # make case-insensitive
    KEYWORDS = ["reschedule", "cancel", "refund", "money back"]
    return any(keyword in text for keyword in KEYWORDS)





def is_faq_question(text):
    text = text.lower()
    faq_keywords = ["?", "how", "what", "when", "where", "help", "faq", "contact", "email", "phone", "number", "can","question", "query", "doubt" ]
    return any(k in text for k in faq_keywords)

# ====== RAG SETUP: Load FAQ from txt dynamically ======
FAQ_DOC_PATH = "mc_hospital_faq.txt"  # your FAQ document file path

try:
    loader = TextLoader(FAQ_DOC_PATH, encoding="utf-8")
    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    docs_split = splitter.split_documents(docs)

    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    vectorstore = FAISS.from_documents(docs_split, embeddings)

    llm = ChatOpenAI(openai_api_key=OPENAI_API_KEY, model_name="gpt-3.5-turbo", temperature=0)
    qa_chain = RetrievalQA.from_chain_type(llm=llm, retriever=vectorstore.as_retriever())

except Exception as e:
    print("Error setting up RAG FAQ system:", e)
    qa_chain = None

def get_rag_answer(question):
    if not qa_chain:
        return "Sorry, FAQ system is not available right now."
    try:
        answer = qa_chain.run(question)
        return answer
    except Exception as e:
        print("Error in RAG answer:", e)
        return "Sorry, I could not find an answer to your question."


@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(message, "Welcome to Hospital Booking Bot!\nType /book to start with Appointment Booking Process or Type your Questions to get answers to any of your queries")


@bot.message_handler(
    func=lambda m: (
        not user_state.get(m.chat.id)
        and m.text.strip().lower() != "/book"
        and all(keyword not in m.text.strip().lower() for keyword in KEYWORDS)
    )
)
def handle_faq(message):
    chat_id = message.chat.id
    answer = get_rag_answer(message.text)
    bot.reply_to(message, answer)
    bot.reply_to(message, "You can type /book to start with Appointment Booking Process")


@bot.message_handler(commands=["book"])
def start_booking(message):
    chat_id = message.chat.id

    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT  id, name FROM specialties")
            specialities = cur.fetchall()
            print(specialities)
        conn.close()

        reply = "Please choose a speciality by typing its name:\n"
        speciality_list = []
        for sp in specialities:
            speciality_name = sp['id']
            reply += f"{sp['id']}. {sp['name']}\n"
            speciality_list.append(str(speciality_name))

        bot.reply_to(message, reply)
        user_state[chat_id] = "choosing_speciality"
        TEMP_BOOKING[chat_id] = {}
        valid_options[chat_id] = speciality_list
        print(valid_options[chat_id])

    except Exception as e:
        print(f"Error in start_booking: {e}")
        bot.reply_to(message, "Sorry, something went wrong. Please try again later.")


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_speciality")
def handle_choosing_speciality(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["speciality"] = text

        # Fetch doctors of that speciality
        try:
            conn = get_db_connection()
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM doctors WHERE specialty_id = %s", (text,))
                doctors = cur.fetchall()
            conn.close()

            if not doctors:
                bot.reply_to(message, "No doctors found for this speciality. Please enter a valid speciality.")
                return

            reply = "Please choose a doctor by typing their ID:\n"
            doctor_ids = []
            for doc in doctors:
                reply += f"{doc['id']}. {doc['name']}\n"
                doctor_ids.append(str(doc['id']))

            bot.reply_to(message, reply)
            user_state[chat_id] = "choosing_doctor"
            valid_options[chat_id] = doctor_ids
            print(valid_options[chat_id])

        except Exception as e:
            print(f"Error fetching doctors by speciality: {e}")
            bot.reply_to(message, "Sorry, something went wrong. Please try again later.")
    else:
        if text.isalnum:
            bot.reply_to(message, "Please enter a valid speciality from the list to move forward.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(message, f"💡 {answer}\n Please enter a valid speciality from the list to move forward.")





@bot.message_handler(func=lambda m: is_faq_question(m.text))
def handle_faq(message):
    answer = get_rag_answer(message.text)
    bot.reply_to(message, answer)
    # Keep state and options intact for booking continuation


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_doctor")
def handle_choosing_doctor(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["doctor_id"] = int(text)
        bot.reply_to(message, "Please enter the date you want to book (YYYY-MM-DD):")
        user_state[chat_id] = "choosing_date"
        valid_options.pop(chat_id, None)  # clear options since free text next
    else:
        if text.isalnum() or text.replace(".", "").isalnum() :
            bot.reply_to(message, "Please enter a valid Doctor ID from the list to move forward.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(
                message,
                f"💡 {answer}\nPlease enter a valid Doctor ID to continue with Appointment Booking"
            )


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_date")
def handle_choosing_date(message):
    chat_id = message.chat.id
    text = message.text.strip()

    try:
        booking_date = datetime.strptime(text, "%Y-%m-%d").date()
        TEMP_BOOKING[chat_id]["slot_date"] = booking_date

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, slot_time 
                FROM doctor_slots 
                WHERE doctor_id=%s AND slot_date=%s AND is_booked=0
            """, (TEMP_BOOKING[chat_id]["doctor_id"], booking_date))
            slots = cur.fetchall()
        conn.close()

        if not slots:
            bot.reply_to(message,
                         "No available slots for this doctor on that date. Please enter a different date (YYYY-MM-DD):")
            return

        reply = "Select a slot by typing its ID:\n"
        slot_ids = []
        for s in slots:
            reply += f"{s['id']}. {s['slot_time']}\n"
            slot_ids.append(str(s['id']))

        bot.reply_to(message, reply)
        user_state[chat_id] = "choosing_slot"
        valid_options[chat_id] = slot_ids  # store valid slot IDs

    except ValueError:
        if text.isalnum() or text.replace(".", "").isalnum():
            bot.reply_to(message,  "Invalid date format. Please use YYYY-MM-DD.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(message, f"💡 {answer} \n Please provide date in YYYY-MM-DD format to move ahead with booking process")
            bot.reply_to(message, "Invalid date format. Please use YYYY-MM-DD.")





@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_slot")
def handle_choosing_slot(message):
    chat_id = message.chat.id
    text = message.text.strip()

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["slot_id"] = int(text)

        # Ask for name first
        bot.reply_to(message, "Please enter your full name:")
        user_state[chat_id] = "entering_name"
    else:

        answer = get_rag_answer(text)
        bot.reply_to(message, f"💡 {answer} \nPlease enter a valid slot ID to move ahead with booking")



@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "entering_name")
def handle_entering_name(message):
    chat_id = message.chat.id
    TEMP_BOOKING[chat_id]["name"] = message.text.strip()
    bot.reply_to(message, "Please enter your 10 digit phone number :")
    user_state[chat_id] = "entering_phone"


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "entering_phone")
def handle_entering_phone(message):

    chat_id = message.chat.id
    text = message.text.strip()

    if text.isdigit() and len(text) == 10:
        TEMP_BOOKING[chat_id]["phone_no"] = message.text.strip()
        doctor_id = TEMP_BOOKING[chat_id]["doctor_id"]
        slot_id = TEMP_BOOKING[chat_id]["slot_id"]

        payment_url = f"{PAYMENT_SERVER_URL}/pay/{chat_id}/{doctor_id}/{slot_id}?name={TEMP_BOOKING[chat_id]['name']}&phone={TEMP_BOOKING[chat_id]['phone_no']}"
        bot.reply_to(message, f"💳 Please complete your payment here: {payment_url}")

        # Clear state so they don't get stuck
        user_state.pop(chat_id, None)
        valid_options.pop(chat_id, None)
        return

    elif text.isalnum() or text.replace(".", "").isalnum():
        bot.reply_to(message, "Please enter valid 10 digit number")

    else:
        answer = get_rag_answer(text)
        bot.reply_to(message, f"💡 {answer} \nPlease enter a valid 10 digit number to move ahead with booking")







@bot.message_handler(func=lambda m: contains_keywords(m.text))
def handle_keywords(message):
    bot.send_message(message.chat.id, "📞 Please enter your registered phone number:")

    # Switch to next step handler to capture the phone number
    bot.register_next_step_handler(message, process_phone_number)


def process_phone_number(message):
    phone_verify = message.text.strip()

    conn = get_db_connection()
    with conn.cursor() as cur:
        # Step 1: Check if bookings exist for that phone number
        cur.execute("SELECT id FROM bookings WHERE phone_no = %s", (phone_verify,))
        bookings_found = cur.fetchall()

        if bookings_found:
            # Step 2: Update payment_status
            cur.execute("""
                UPDATE bookings
                SET payment_status = %s
                WHERE phone_no = %s
            """, ("contact_request", phone_verify))
            conn.commit()
            bot.send_message(message.chat.id, "Our team will call you soon to help you with your request.")
        else:
            bot.send_message(message.chat.id, "No Bookings found for the number, you can call our helpline +917259356897 for help ")

    conn.close()

@bot.message_handler(func=lambda m: True)
def handle_fallback(message):
    chat_id = message.chat.id
    state = user_state.get(chat_id)

    if state:
        # They are in the middle of booking but typed something unexpected
        bot.reply_to(message, "Sorry, I didn't understand that. Please follow the instructions above.")
    else:
        # No active booking — guide them
        bot.reply_to(
            message,
            "I’m not sure how to respond to that.\n"
            "💡 You can type:\n"
            "• /book to start appointment booking\n"
            "• /Query to ask questions"
        )






if __name__ == "__main__":
    print("Bot started...")
    bot.infinity_polling()




