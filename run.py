import threading
import subprocess

def run_bot():
    subprocess.run(["python", "bot.py"])

def run_server():
    subprocess.run(["python", "server.py"])

if __name__ == "__main__":
    t1 = threading.Thread(target=run_bot)
    t2 = threading.Thread(target=run_server)

    t1.start()
    t2.start()

    t1.join()
    t2.join()
