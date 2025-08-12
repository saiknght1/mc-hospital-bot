import threading
import subprocess

def run_bot():
    subprocess.run(["python", "bot.py"])

def run_payment_server():
    subprocess.run(["python", "payment_server.py"])

def run_doctor_dashboard():
    subprocess.run(["python", "doctor_dashboard.py"])

if __name__ == "__main__":
    t1 = threading.Thread(target=run_bot)
    t2 = threading.Thread(target=run_payment_server)
    t3 = threading.Thread(target=run_doctor_dashboard)

    t1.start()
    t2.start()
    t3.start()

    t1.join()
    t2.join()
    t3.join()
