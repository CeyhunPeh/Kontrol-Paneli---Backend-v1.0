import os
import json
import psycopg2
from fastapi import FastAPI, WebSocket, Request

app = FastAPI()
active_connections = []

# Neon Connection String'i Render'ın "Environment Variables" kısmından alacağız
DATABASE_URL = os.environ.get('DATABASE_URL')

def save_to_neon(service, level, message):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO app_logs (service_name, log_level, message) VALUES (%s, %s, %s)",
            (service, level, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}")

@app.post("/render-webhook")
async def handle_render_log(request: Request):
    raw_data = await request.body()
    log_content = raw_data.decode("utf-8")
    
    # Basit Sınıflandırma
    level = "INFO"
    if "500" in log_content or "503" in log_content: level = "CRITICAL"
    elif "Warning" in log_content: level = "WARNING"
    
    # 1. Neon'a Kaydet
    save_to_neon("Render-App", level, log_content)
    
    # 2. Flutter'a WebSocket ile Gönder
    payload = {"type": "log", "level": level, "message": log_content}
    for connection in active_connections:
        await connection.send_text(json.dumps(payload))
        
    return {"status": "ok"}

@app.get("/")
async def health_check():
    return {"status": "alive", "message": "Sistem ayakta, Ceyhun!"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True: await websocket.receive_text()
    except: active_connections.remove(websocket)