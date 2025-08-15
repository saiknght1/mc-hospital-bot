# telegrambot logic with RAG memory + better retrieval
import telebot
import pymysql
from datetime import datetime
import os
from collections import defaultdict
from dotenv import load_dotenv

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

PAYMENT_SERVER_URL = "https://mc-hospital-bot.up.railway.app"  # Your Flask server URL

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
TEMP_BOOKING = {}  # chat_id -> booking info
valid_options = {} # chat_id -> list of valid inputs for current step
KEYWORDS = ["reschedule", "cancel", "refund", "money back", "Cancel", "Reschedule"]


# ====== RAG SETUP with per-user memory ======
FAQ_DOC_PATH = "mc_hospital_faq.txt"

try:
    loader = TextLoader(FAQ_DOC_PATH, encoding="utf-8")
    docs = loader.load()

    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    docs_split = splitter.split_documents(docs)

    embeddings = OpenAIEmbeddings(openai_api_key=OPENAI_API_KEY)
    vectorstore = FAISS.from_documents(docs_split, embeddings)

    retriever = vectorstore.as_retriever(
        search_type="similarity",
        search_kwargs={"k": 6, "fetch_k": 10}
    )

    # Store separate memories for each user
    user_memories = defaultdict(lambda: ConversationBufferMemory(
        memory_key="chat_history",
        return_messages=True
    ))

    # Friendly hospital assistant prompt
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

    def get_rag_answer(chat_id, question):
        try:
            chain = get_user_qa_chain(chat_id)
            result = chain({"question": question})
            answer = result["answer"]

            # Fallback if answer is empty or generic
            if not answer.strip() or "I am not sure" in answer:
                llm_fallback = ChatOpenAI(
                    openai_api_key=OPENAI_API_KEY,
                    model_name="gpt-3.5-turbo",
                    temperature=0.5
                )
                answer = llm_fallback.predict(
                    f"You are a hospital assistant. The patient asked: '{question}'. "
                    "Answer politely and concisely."
                )
            return answer
        except Exception as e:
            print("Error in RAG answer:", e)
            return "Sorry, I could not find an answer to your question."

except Exception as e:
    print("Error setting up RAG FAQ system:", e)
    def get_rag_answer(*_):
        return "Sorry, FAQ system is not available right now."


# ====== Keyword helper ======
def contains_keywords(text):
    text = text.lower()
    return any(keyword in text for keyword in ["reschedule", "cancel", "refund", "money back"])


@bot.message_handler(commands=["start"])
def send_welcome(message):
    bot.reply_to(
        message,
        "Welcome to Hospital Booking Bot!\nType /book to start with Appointment Booking Process "
        "or Type your Questions to get answers to any of your queries"
    )


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
    if booking_done.get(chat_id, False):
        # User already booked, don't append /book
        bot.reply_to(message, answer)
    else:
        bot.reply_to(message, answer)
        bot.reply_to(message, "You can type /book to start with Appointment Booking Process")




@bot.message_handler(commands=["book"])
def start_booking(message):
    chat_id = message.chat.id
    booking_done[chat_id] = False
    user_state = {}  # chat_id -> state
    TEMP_BOOKING = {}  # chat_id -> booking info
    valid_options = {}


    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM specialties")
            specialities = cur.fetchall()
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

    except Exception as e:
        print(f"Error in start_booking: {e}")
        bot.reply_to(message, "Sorry, something went wrong. Please try again later.")


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_speciality")
def handle_choosing_speciality(message):
    chat_id = message.chat.id
    text = message.text.strip().rstrip(".")

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["speciality"] = text

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

        except Exception as e:
            print(f"Error fetching doctors by speciality: {e}")
            bot.reply_to(message, "Sorry, something went wrong. Please try again later.")
    else:
        if any(ch.isdigit() for ch in text):
            bot.reply_to(message, "Please enter a valid speciality from the list to move forward.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(message, f"💡 {answer}\n\n Please enter a valid speciality from the list to move forward.")


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_doctor")
def handle_choosing_doctor(message):
    chat_id = message.chat.id
    text = message.text.strip().rstrip(".")

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["doctor_id"] = int(text)
        bot.reply_to(message, "Please enter the date you want to book (YYYY-MM-DD):")
        user_state[chat_id] = "choosing_date"
        valid_options.pop(chat_id, None)
    else:
        if any(ch.isdigit() for ch in text):
            bot.reply_to(message, "Please enter a valid Doctor ID from the list to move forward.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(
                message,
                f"💡 {answer}\n\nPlease enter a valid Doctor ID to continue with Appointment Booking"
            )


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_date")
def handle_choosing_date(message):
    chat_id = message.chat.id
    text = message.text.strip().rstrip(".")

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
        valid_options[chat_id] = slot_ids

    except ValueError:
        if any(ch.isdigit() for ch in text):
            bot.reply_to(message, "Invalid date format. Please use YYYY-MM-DD.")
        else:
            answer = get_rag_answer(text)
            bot.reply_to(message, f"💡 {answer} \n Please provide date in YYYY-MM-DD format to move ahead with booking process")


@bot.message_handler(func=lambda m: user_state.get(m.chat.id) == "choosing_slot")
def handle_choosing_slot(message):
    chat_id = message.chat.id
    text = message.text.strip().rstrip(".")

    if text in valid_options.get(chat_id, []):
        TEMP_BOOKING[chat_id]["slot_id"] = int(text)
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
    text = message.text.strip().rstrip(".")

    if text.isdigit() and len(text) == 10:
        TEMP_BOOKING[chat_id]["phone_no"] = message.text.strip()
        doctor_id = TEMP_BOOKING[chat_id]["doctor_id"]
        slot_id = TEMP_BOOKING[chat_id]["slot_id"]

        payment_url = f"{PAYMENT_SERVER_URL}/pay/{chat_id}/{doctor_id}/{slot_id}?name={TEMP_BOOKING[chat_id]['name']}&phone={TEMP_BOOKING[chat_id]['phone_no']}"
        bot.reply_to(message, f"💳 Please complete your payment here: {payment_url}")

        user_state.pop(chat_id, None)
        valid_options.pop(chat_id, None)
        return

    elif any(ch.isdigit() for ch in text):
        bot.reply_to(message, "Please enter valid 10 digit number")
    else:
        answer = get_rag_answer(text)
        bot.reply_to(message, f"💡 {answer} \nPlease enter a valid 10 digit number to move ahead with booking")


@bot.message_handler(func=lambda m: contains_keywords(m.text))
def handle_keywords(message):
    bot.send_message(message.chat.id, "📞 Please enter your registered phone number:")
    bot.register_next_step_handler(message, process_phone_number)


def process_phone_number(message):
    phone_verify = message.text.strip()

    if not phone_verify.isdigit() or len(phone_verify) != 10:
        bot.send_message(message.chat.id, "❌ Please enter a valid 10-digit number:")
        bot.register_next_step_handler(message, process_phone_number)
        return

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
                bot.send_message(message.chat.id, "✅ Our team will call you soon to help you with your request.")
            else:
                bot.send_message(message.chat.id, "❌ No bookings found for this number. You can call our helpline at +91 7259356897 for help.")
    except Exception as e:
        bot.send_message(message.chat.id, f"⚠️ An error occurred: {e}")
    finally:
        conn.close()


@bot.message_handler(func=lambda m: True)
def handle_fallback(message):
    chat_id = message.chat.id
    state = user_state.get(chat_id)

    if state:
        bot.reply_to(message, "Sorry, I didn't understand that. Please follow the instructions above.")
    else:
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
