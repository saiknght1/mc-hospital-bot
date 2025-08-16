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
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Page</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0; 
            padding: 0;
            background: #f9f9f9;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .card {
            background: #fff;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            width: 90%;
            max-width: 420px;
            text-align: center;
        }
        h2 {
            color: #333;
            margin-bottom: 15px;
        }
        .info {
            text-align: left;
            margin: 10px 0;
            font-size: 16px;
        }
        .info p {
            margin: 6px 0;
        }
        .btn {
            background: #007BFF;
            color: white;
            padding: 12px 20px;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            cursor: pointer;
            width: 100%;
            margin-top: 15px;
        }
        .btn:hover {
            background: #0056b3;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>Pay for Your Appointment</h2>
        <div class="info">
            <p><strong>Patient Name:</strong> {{ name }}</p>
            <p><strong>Phone:</strong> {{ phone }}</p>
            <p><strong>Doctor:</strong> {{ doctor }}</p>
            <p><strong>Date:</strong> {{ slot_date }}</p>
            <p><strong>Time:</strong> {{ slot_time }}</p>
            <p><strong>Consultation Fee:</strong> ₹{{ fees }}</p>
        </div>
        <form method="POST" action="/confirm_payment">
            <input type="hidden" name="chat_id" value="{{ chat_id }}">
            <input type="hidden" name="doctor_id" value="{{ doctor_id }}">
            <input type="hidden" name="slot_id" value="{{ slot_id }}">
            <input type="hidden" name="name" value="{{ name }}">
            <input type="hidden" name="phone" value="{{ phone }}">
            <input type="hidden" name="fees" value="{{ fees }}">
            <button type="submit" class="btn">💳 Pay Now</button>
        </form>
    </div>
</body>
</html>
"""

SUCCESS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Payment Success</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0; 
            padding: 0;
            background: #f9f9f9;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
        }
        .card {
            background: #fff;
            padding: 25px;
            border-radius: 12px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            width: 90%;
            max-width: 420px;
            text-align: center;
        }
        h2 {
            color: #28a745;
            margin-bottom: 15px;
        }
        p {
            font-size: 16px;
            margin: 8px 0;
            color: #333;
        }
        .btn {
            display: inline-block;
            margin-top: 15px;
            padding: 12px 20px;
            background: #007BFF;
            color: white;
            border-radius: 8px;
            text-decoration: none;
            font-size: 16px;
        }
        .btn:hover {
            background: #0056b3;
        }
    </style>
</head>
<body>
    <div class="card">
        <h2>✅ Payment Successful!</h2>
        <p>Your appointment with <strong>Dr. {{ doctor }}</strong> is confirmed.</p>
        <p><strong>Date:</strong> {{ slot_date }}</p>
        <p><strong>Time:</strong> {{ slot_time }}</p>
        <p>Paid: ₹{{ fees }}</p>
        <a href="https://t.me/{{ bot_username }}" class="btn">Back to Bot</a>
    </div>
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

        return render_template_string(
            SUCCESS_HTML,
            doctor=doctor_name,
            slot_date=slot_date,
            slot_time=slot_time,
            fees=fees,
            bot_username=os.getenv("BOT_USERNAME")
        )

    except Exception as e:
        print("[ERROR] /confirm_payment route failed:", e)
        traceback.print_exc()
        return f"Internal Server Error: {e}", 500


@app.route("/")
def home():
    return "<h2>Payment Server is Running</h2>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
