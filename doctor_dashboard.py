#Doctor Dashboard
from flask import Flask, render_template, request
import pymysql
import os

app = Flask(__name__)

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

@app.route("/", methods=["GET", "POST"])
def doctor_dashboard():
    bookings = []
    selected_date = None
    doctor_id = None
    doctors = []

    conn = get_db_connection()
    with conn.cursor() as cur:
        # Get all doctors for dropdown
        cur.execute("SELECT id, name FROM doctors ORDER BY name ASC")
        doctors = cur.fetchall()

    if request.method == "POST":
        doctor_id = request.form.get("doctor_id")
        selected_date = request.form.get("slot_date")

        with conn.cursor() as cur:
            sql = """
                SELECT 
                    b.id,
                    b.name AS patient_name,
                    b.phone_no,
                    b.slot_date,
                    b.slot_time,
                    d.name AS doctor_name
                FROM bookings b
                JOIN doctors d ON b.doctor_id = d.id
                WHERE b.doctor_id = %s
            """
            params = [doctor_id]

            # Optional filter by date
            if selected_date:
                sql += " AND b.slot_date = %s"
                params.append(selected_date)

            sql += " ORDER BY b.slot_date, b.slot_time"

            cur.execute(sql, params)
            bookings = cur.fetchall()

    conn.close()

    return render_template(
        "doctor_dashboard.html",
        bookings=bookings,
        selected_date=selected_date,
        doctor_id=doctor_id,
        doctors=doctors
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=True)

