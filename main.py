from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
import random
import json
import time
import string
import os
import shutil
from typing import List, Dict

app = FastAPI()

# Ensure uploads directory exists
UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# Serve static files and uploaded files
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.usernames: Dict[str, str] = {}

    def generate_id(self) -> str:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        user_id = self.generate_id()
        username = f"User-{random.randint(1000, 9999)}"
        self.active_connections[user_id] = websocket
        self.usernames[user_id] = username

        await websocket.send_text(json.dumps({
            "type": "welcome",
            "id": user_id,
            "username": username
        }))
        await self.broadcast_user_list()
        await self.broadcast_system(f"{username} joined the chat")

    def disconnect(self, user_id: str):
        username = self.usernames.get(user_id, "Unknown")
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        if user_id in self.usernames:
            del self.usernames[user_id]
        return username

    async def send_to_user(self, user_id: str, data: dict):
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
            except:
                pass

    async def broadcast_user_list(self):
        users = [{"id": uid, "username": uname} for uid, uname in self.usernames.items()]
        await self.broadcast({"type": "userlist", "users": users})

    async def broadcast_system(self, text: str):
        await self.broadcast({"type": "system", "text": text})

    async def broadcast_chat(self, sender_id: str, text: str):
        username = self.usernames.get(sender_id, "Unknown")
        await self.broadcast({
            "type": "chat",
            "username": username,
            "text": text,
            "timestamp": int(time.time() * 1000)
        })

    async def broadcast_file(self, sender_id: str, filename: str, url: str, content_type: str):
        username = self.usernames.get(sender_id, "Unknown")
        await self.broadcast({
            "type": "file",
            "username": username,
            "filename": filename,
            "url": url,
            "content_type": content_type,
            "timestamp": int(time.time() * 1000)
        })

    async def broadcast(self, data: dict):
        message = json.dumps(data)
        for ws in self.active_connections.values():
            try:
                await ws.send_text(message)
            except:
                pass

manager = ConnectionManager()

@app.get("/")
async def get():
    with open("static/index.html") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), user_id: str = None):
    # user_id is passed as form data from the client
    # Generate unique filename
    ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_name = f"{int(time.time())}_{random.randint(1000, 9999)}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    # Save the file
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # Determine content type
    content_type = file.content_type or "application/octet-stream"

    # Create URL for the file
    # The client will access it via /uploads/{unique_name}
    file_url = f"/uploads/{unique_name}"

    # Broadcast file message if user_id is provided and valid
    if user_id and user_id in manager.active_connections:
        await manager.broadcast_file(user_id, file.filename, file_url, content_type)

    return {"url": file_url, "filename": file.filename}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    user_id = None
    for uid, ws in manager.active_connections.items():
        if ws == websocket:
            user_id = uid
            break

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                msg_type = msg.get("type")

                if msg_type == "chat" and msg.get("text"):
                    await manager.broadcast_chat(user_id, msg["text"])

                elif msg_type == "call-request":
                    target_id = msg.get("target")
                    if target_id and target_id in manager.active_connections:
                        await manager.send_to_user(target_id, {
                            "type": "call-request",
                            "from": user_id,
                            "fromUsername": manager.usernames.get(user_id)
                        })

                elif msg_type == "call-accept":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "call-accept",
                            "from": user_id
                        })

                elif msg_type == "call-reject":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "call-reject",
                            "from": user_id
                        })

                elif msg_type == "call-end":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "call-end",
                            "from": user_id
                        })

                elif msg_type == "offer":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "offer",
                            "from": user_id,
                            "sdp": msg.get("sdp")
                        })

                elif msg_type == "answer":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "answer",
                            "from": user_id,
                            "sdp": msg.get("sdp")
                        })

                elif msg_type == "ice-candidate":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "ice-candidate",
                            "from": user_id,
                            "candidate": msg.get("candidate")
                        })

            except json.JSONDecodeError:
                pass
            except Exception as e:
                print("Error handling message:", e)

    except WebSocketDisconnect:
        username = manager.disconnect(user_id)
        await manager.broadcast_user_list()
        await manager.broadcast_system(f"{username} left the chat")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)