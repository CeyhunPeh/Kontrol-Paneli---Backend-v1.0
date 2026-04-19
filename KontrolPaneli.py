import os
import asyncio
import httpx # 'pip install httpx' eklemeyi unutma
import json
import psycopg2
from fastapi import FastAPI, WebSocket, Request
from contextlib import asynccontextmanager

# --- KONFİGÜRASYON ---
DATABASE_URL = os.environ.get('DATABASE_URL')
RENDER_API_KEY = os.environ.get('RENDER_API_KEY')
SERVICE_IDS = os.environ.get('SERVICE_IDS', "").split(",")

# Yeni eklenen Neon API konfigürasyonları
NEON_API_KEY = os.environ.get('NEON_API_KEY')
NEON_PROJECT_IDS = os.environ.get('NEON_PROJECT_ID', "").split(",")

active_connections = []

# Arka plan görevi için lifespan (FastAPI modern yöntemi)
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uygulama başlarken döngüleri başlat
    task_logs = asyncio.create_task(log_polling_loop())
    task_metrics = asyncio.create_task(neon_metrics_loop())
    yield
    # Uygulama kapanırken görevleri iptal et
    task_logs.cancel()
    task_metrics.cancel()

app = FastAPI(lifespan=lifespan)

# --- YARDIMCI FONKSİYONLAR ---
def save_to_neon(service_name, level, message):
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()
        # Aynı logun tekrar yazılmaması için basit bir kontrol eklenebilir
        cur.execute(
            "INSERT INTO app_logs (service_name, log_level, message) VALUES (%s, %s, %s)",
            (service_name, level, message)
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Neon DB Hatası: {e}")

async def log_polling_loop():
    """Her 60 saniyede bir Render API'den logları çeker."""
    async with httpx.AsyncClient() as client:
        while True:
            for s_id in SERVICE_IDS:
                if not s_id: continue
                
                try:
                    url = f"https://api.render.com/v1/services/{s_id}/logs"
                    headers = {"Authorization": f"Bearer {RENDER_API_KEY}"}
                    response = await client.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        logs = response.json() # Render list of logs döner
                        for log_entry in logs:
                            msg = log_entry.get("text", "")
                            
                            # Sınıflandırma
                            level = "SUCCESS"
                            if any(x in msg for x in ["500", "503", "error", "Fatal"]): level = "CRITICAL"
                            elif any(x in msg for x in ["404", "warn", "timeout"]): level = "WARNING"
                            
                            # 1. Kaydet
                            save_to_neon(s_id, level, msg)
                            
                            # 2. WebSocket ile Flutter'a gönder
                            payload = {"type": "log", "service": s_id, "level": level, "message": msg}
                            for ws in active_connections:
                                await ws.send_text(json.dumps(payload))
                                
                except Exception as e:
                    print(f"Polling Hatası ({s_id}): {e}")
            
            await asyncio.sleep(60) # 1 dakika bekle

async def neon_metrics_loop():
    """Neon API'den belirli aralıklarla (örn: 5 dk) sistem metriklerini çeker."""
    async with httpx.AsyncClient() as client:
        while True:
            # API anahtarı tanımlıysa çalıştır
            if NEON_API_KEY:
                for p_id in NEON_PROJECT_IDS:
                    p_id = p_id.strip() # Boşluk kalmışsa temizle
                    if not p_id: continue
                    
                    try:
                        url = f"https://console.neon.tech/api/v2/projects/{p_id}"
                        headers = {
                            "Authorization": f"Bearer {NEON_API_KEY}",
                            "Accept": "application/json"
                        }
                        response = await client.get(url, headers=headers)
                        
                        if response.status_code == 200:
                            data = response.json()
                            project_data = data.get("project", {})
                            
                            # Flutter'a hangi projenin verisi olduğunu da ('project_id') ekleyerek gönderiyoruz
                            payload = {
                                "type": "metric",
                                "source": "neon",
                                "project_id": p_id,
                                "data": project_data
                            }
                            
                            for ws in active_connections:
                                await ws.send_text(json.dumps(payload))
                                
                    except Exception as e:
                        print(f"Neon Metrik Hatası ({p_id}): {e}")
            
            # Tüm projeleri sorguladıktan sonra 5 dk bekle
            await asyncio.sleep(300)

@app.get("/")
async def health():
    return {"status": "alive"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True: await websocket.receive_text()
    except: active_connections.remove(websocket)