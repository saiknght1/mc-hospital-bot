import os
import telebot
import pymysql
from flask import Flask, render_template_string, request
from dotenv import load_dotenv
import traceback

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

DB_HOST = os.getenv("DB_HOST")
DB_PORT = int(os.getenv("DB_PORT", 3306))
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
    <p>Consultation Fee: ₹{{ fees }}</p>
    <form method="POST" action="/confirm_payment">
        <input type="hidden" name="chat_id" value="{{ chat_id }}">
        <input type="hidden" name="doctor_id" value="{{ doctor_id }}">
        <input type="hidden" name="slot_id" value="{{ slot_id }}">
        <input type="hidden" name="name" value="{{ name }}">
        <input type="hidden" name="phone" value="{{ phone }}">
        <input type="hidden" name="fees" value="{{ fees }}">
        <button type="submit" style="padding:10px 20px;">Pay Now</button>
    </form>
</body>
</html>
"""

@app.route("/pay/<chat_id>/<doctor_id>/<slot_id>")
def pay(chat_id, doctor_id, slot_id):
    try:
        name = request.args.get("name", "")
        phone = request.args.get("phone", "")
        fee_override = request.args.get("fee")

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT slot_time, slot_date FROM doctor_slots WHERE id=%s", (slot_id,))
            slot_data = cur.fetchone()
            if not slot_data:
                return "❌ Slot not found", 404
            slot_time = slot_data["slot_time"]
            slot_date = slot_data["slot_date"]

            cur.execute("SELECT name, fees FROM doctors WHERE id=%s", (doctor_id,))
            doctor_row = cur.fetchone()
            if not doctor_row:
                return "❌ Doctor not found", 404
            doctor = doctor_row["name"]
            fees = fee_override if fee_override else doctor_row["fees"]
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
            phone=phone,
            fees=fees
        )

    except Exception as e:
        print("[ERROR] /pay route failed:", e)
        traceback.print_exc()
        return f"Internal Server Error: {e}", 500


@app.route("/confirm_payment", methods=["POST"])
def confirm_payment():
    try:
        chat_id = request.form["chat_id"]
        doctor_id = request.form["doctor_id"]
        slot_id = request.form["slot_id"]
        name = request.form["name"]
        phone = request.form["phone"]
        fees = request.form.get("fees", 0)

        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT slot_time, slot_date FROM doctor_slots WHERE id=%s", (slot_id,))
            slot_data = cur.fetchone()
            if not slot_data:
                return "❌ Slot not found", 404
            slot_time = slot_data["slot_time"]
            slot_date = slot_data["slot_date"]

            cur.execute("SELECT name FROM doctors WHERE id=%s", (doctor_id,))
            doctor_row = cur.fetchone()
            if not doctor_row:
                return "❌ Doctor not found", 404
            doctor_name = doctor_row["name"]

            # FIX: Correct placeholder count (7 columns = 7 placeholders)
            cur.execute("""
                INSERT INTO bookings (user_id, doctor_id, slot_time, slot_date, payment_status, name, phone_no) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (chat_id, doctor_id, slot_time, slot_date, "paid", name, phone))

            cur.execute("UPDATE doctor_slots SET is_booked=1 WHERE id=%s", (slot_id,))
        conn.commit()
        conn.close()

        bot.send_message(
            chat_id,
            f"✅ Payment received!\n"
            f"Booking confirmed with Dr. {doctor_name} on {slot_date} at {slot_time}.\n"
            f"Paid: ₹{fees}"
        )

        return "<h3>Payment successful! You can close this page.</h3>"

    except Exception as e:
        print("[ERROR] /confirm_payment route failed:", e)
        traceback.print_exc()
        return f"Internal Server Error: {e}", 500


@app.route("/")
def home():
    return "<h2>Payment Server is Running</h2>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
