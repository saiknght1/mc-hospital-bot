#payment server
from flask import Flask, request, render_template_string
import pymysql
import telebot

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))  # default to 3306 if not set
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_NAME = os.getenv("DB_NAME")



def get_db_connection():
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        cursorclass=pymysql.cursors.DictCursor
    )

app = Flask(__name__)

PAYMENT_HTML = """
<!DOCTYPE html>
<html>
<head><title>Payment Page</title></head>
<body style="font-family:Arial;text-align:center;">
    <h2>Pay for Your Appointment</h2>
    <p>Patient Name: {{ name }}</p>
    <p>Phone: {{ phone }}</p>
    <p>Doctor: {{ doctor }}</p>
    <p>Date: {{ slot_date }}</p>
    <p>Time: {{ slot_time }}</p>
    <form method="POST" action="/confirm_payment">
        <input type="hidden" name="chat_id" value="{{ chat_id }}">
        <input type="hidden" name="doctor_id" value="{{ doctor_id }}">
        <input type="hidden" name="slot_id" value="{{ slot_id }}">
        <input type="hidden" name="name" value="{{ name }}">
        <input type="hidden" name="phone" value="{{ phone }}">
        <button type="submit" style="padding:10px 20px;">Pay Now</button>
    </form>
</body>
</html>
"""

@app.route("/pay/<chat_id>/<doctor_id>/<slot_id>")
def pay(chat_id, doctor_id, slot_id):
    name = request.args.get("name", "")
    phone = request.args.get("phone", "")

    conn = get_db_connection()
    with conn.cursor() as cur:
        cur.execute("SELECT slot_time, slot_date FROM doctor_slots WHERE id=%s", (slot_id,))
        slot_data = cur.fetchone()
        slot_time = slot_data["slot_time"]
        slot_date = slot_data["slot_date"]

        cur.execute("SELECT name FROM doctors WHERE id=%s", (doctor_id,))
        doctor = cur.fetchone()["name"]
    conn.close()

    return render_template_string(
        PAYMENT_HTML,
        chat_id=chat_id,
        doctor_id=doctor_id,
        slot_id=slot_id,
        slot_time=slot_time,
        slot_date=slot_date,
        doctor=doctor,
        name=name,
        phone=phone
    )

@app.route("/confirm_payment", methods=["POST"])
def confirm_payment():
    chat_id = request.form["chat_id"]
    doctor_id = request.form["doctor_id"]
    slot_id = request.form["slot_id"]
    name = request.form["name"]
    phone = request.form["phone"]

    conn = get_db_connection()
    with conn.cursor() as cur:
        # Get slot details
        cur.execute("SELECT slot_time, slot_date FROM doctor_slots WHERE id=%s", (slot_id,))
        slot_data = cur.fetchone()
        slot_time = slot_data["slot_time"]
        slot_date = slot_data["slot_date"]

        # Get doctor name
        cur.execute("SELECT name FROM doctors WHERE id=%s", (doctor_id,))
        doctor_name = cur.fetchone()["name"]

        # Save booking
        cur.execute("""
            INSERT INTO bookings (user_id, doctor_id, slot_time, slot_date, payment_status, name, phone_no) 
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (chat_id, doctor_id, slot_time, slot_date, "paid", name, phone))

        # Mark slot as booked
        cur.execute("UPDATE doctor_slots SET is_booked=1 WHERE id=%s", (slot_id,))
    conn.commit()
    conn.close()

    # Send Telegram confirmation with doctor name
    bot.send_message(
        chat_id,
        f"✅ Payment received!\n"
        f"Booking confirmed with Dr. {doctor_name} on {slot_date} at {slot_time}."
    )

    return "<h3>Payment successful! You can close this page.</h3>"


@app.route("/")
def home():
    return "<h2>Payment Server is Running</h2>"

if __name__ == "__main__":
     print("Flask server started...")
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)))
