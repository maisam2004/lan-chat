from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, Form, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
import random
import json
import time
import string
import os
import shutil
from typing import Dict

app = FastAPI()

PASSWORD = os.environ.get("LAN_CHAT_PASSWORD", "changeme123")

UPLOAD_DIR = "uploads"
IMAGES_FILE = os.path.join(UPLOAD_DIR, "images.json")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# ---------------- Image board storage (simple JSON file) ----------------
def load_images() -> dict:
    if not os.path.exists(IMAGES_FILE):
        return {"images": [], "featured_id": None}
    try:
        with open(IMAGES_FILE, "r") as f:
            data = json.load(f)
            data.setdefault("images", [])
            data.setdefault("featured_id", None)
            return data
    except Exception:
        return {"images": [], "featured_id": None}


def save_images(data: dict):
    with open(IMAGES_FILE, "w") as f:
        json.dump(data, f, indent=2)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.usernames: Dict[str, str] = {}

    def generate_id(self) -> str:
        return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

    async def connect(self, websocket: WebSocket) -> str:
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
        return user_id

    def disconnect(self, user_id: str):
        username = self.usernames.get(user_id, "Unknown")
        self.active_connections.pop(user_id, None)
        self.usernames.pop(user_id, None)
        return username

    async def send_to_user(self, user_id: str, data: dict):
        ws = self.active_connections.get(user_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
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
        for ws in list(self.active_connections.values()):
            try:
                await ws.send_text(message)
            except Exception:
                pass


manager = ConnectionManager()


@app.get("/")
async def get():
    with open("static/index.html") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)


@app.get("/images")
async def get_images():
    """Return the full image board state."""
    return load_images()


@app.post("/feature")
async def feature_image(image_id: str = Form(...), password: str = Form(...)):
    """Toggle the featured image. Same password as everything else."""
    if password != PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    store = load_images()
    # Toggle: if same image was featured, unfeature it. Otherwise feature it.
    if store["featured_id"] == image_id:
        store["featured_id"] = None
    else:
        # Make sure the id actually exists
        if not any(img["id"] == image_id for img in store["images"]):
            raise HTTPException(status_code=404, detail="Image not found")
        store["featured_id"] = image_id

    save_images(store)
    await manager.broadcast({"type": "image_featured", "featured_id": store["featured_id"]})
    return {"featured_id": store["featured_id"]}


@app.post("/delete")
async def delete_image(image_id: str = Form(...), password: str = Form(...)):
    """Delete a board image: file on disk + entry in images.json + broadcast."""
    if password != PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    store = load_images()
    if not any(img["id"] == image_id for img in store["images"]):
        raise HTTPException(status_code=404, detail="Image not found")

    # Remove the file from disk (best-effort)
    file_path = os.path.join(UPLOAD_DIR, image_id)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            print("Failed to delete file:", e)

    # Remove from the store
    store["images"] = [i for i in store["images"] if i["id"] != image_id]
    if store["featured_id"] == image_id:
        store["featured_id"] = None
    save_images(store)

    await manager.broadcast({
        "type": "image_deleted",
        "image_id": image_id,
        "featured_id": store["featured_id"],
    })
    return {"ok": True}

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: str = Form(None),
    password: str = Form(None),
    target_id: str = Form(None),
):
    if password != PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")

    ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_name = f"{int(time.time())}_{random.randint(1000, 9999)}{ext}"
    file_path = os.path.join(UPLOAD_DIR, unique_name)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    content_type = file.content_type or "application/octet-stream"
    file_url = f"/uploads/{unique_name}"
    sender_name = manager.usernames.get(user_id, "Unknown") if user_id else "Unknown"

    file_message = {
        "type": "file",
        "username": sender_name,
        "filename": file.filename,
        "url": file_url,
        "content_type": content_type,
        "timestamp": int(time.time() * 1000),
        "private": False,
    }

    if target_id:
        # ---- Private send: to the target and back to the sender only ----
        file_message["private"] = True
        if target_id in manager.active_connections:
            await manager.send_to_user(target_id, file_message)
        if user_id and user_id in manager.active_connections:
            await manager.send_to_user(user_id, file_message)
        # Note: private files do NOT go to the board.
    else:
        # ---- Public broadcast + board ----
        if user_id and user_id in manager.active_connections:
            await manager.broadcast_file(user_id, file.filename, file_url, content_type)

        if content_type.startswith("image/"):
            store = load_images()
            image_entry = {
                "id": unique_name,
                "filename": file.filename,
                "url": file_url,
                "uploader": sender_name,
                "timestamp": int(time.time() * 1000),
            }
            store["images"].insert(0, image_entry)
            save_images(store)
            await manager.broadcast({"type": "image_added", "image": image_entry})

    return {"url": file_url, "filename": file.filename}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # ---- Auth: first message must be {"type":"auth","password":"..."} ----
    try:
        raw = await websocket.receive_text()
        first = json.loads(raw)
    except Exception:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if first.get("type") != "auth" or first.get("password") != PASSWORD:
        await websocket.close(code=1008, reason="Unauthorized")
        return

    user_id = await manager.connect(websocket)

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
                        await manager.send_to_user(target_id, {"type": "call-accept", "from": user_id})

                elif msg_type == "call-reject":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {"type": "call-reject", "from": user_id})

                elif msg_type == "call-end":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {"type": "call-end", "from": user_id})

                elif msg_type == "offer":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "offer", "from": user_id, "sdp": msg.get("sdp")
                        })

                elif msg_type == "answer":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "answer", "from": user_id, "sdp": msg.get("sdp")
                        })

                elif msg_type == "ice-candidate":
                    target_id = msg.get("target")
                    if target_id:
                        await manager.send_to_user(target_id, {
                            "type": "ice-candidate", "from": user_id, "candidate": msg.get("candidate")
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