from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import uvicorn
import random
import json
from typing import List
import time

app = FastAPI()

# Serve static files from the "static" directory
app.mount("/static", StaticFiles(directory="static"), name="static")

# Connection manager to keep track of all WebSocket connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.usernames = {}

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # Assign a random username
        username = f"User-{random.randint(1000, 9999)}"
        self.usernames[websocket] = username
        self.active_connections.append(websocket)
        # Send welcome message with assigned username
        await websocket.send_text(json.dumps({"type": "welcome", "username": username}))
        # Broadcast updated user list and system message
        await self.broadcast_user_list()
        await self.broadcast_system(f"{username} joined the chat")

    def disconnect(self, websocket: WebSocket):
        username = self.usernames.get(websocket, "Unknown")
        self.active_connections.remove(websocket)
        del self.usernames[websocket]
        return username

    async def broadcast_user_list(self):
        users = list(self.usernames.values())
        await self.broadcast({"type": "userlist", "users": users})

    async def broadcast_system(self, text: str):
        await self.broadcast({"type": "system", "text": text})

    async def broadcast_chat(self, username: str, text: str):
        await self.broadcast({
            "type": "chat",
            "username": username,
            "text": text,
            "timestamp": int(time.time() * 1000)
        })

    async def broadcast(self, data: dict):
        message = json.dumps(data)
        for connection in self.active_connections:
            try:
                await connection.send_text(message)
            except:
                # If sending fails, the connection is likely dead
                pass

manager = ConnectionManager()

@app.get("/")
async def get():
    # Serve the HTML file directly (no templating engine needed)
    with open("static/index.html") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content, status_code=200)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "chat" and msg.get("text"):
                    username = manager.usernames[websocket]
                    await manager.broadcast_chat(username, msg["text"])
            except:
                pass
    except WebSocketDisconnect:
        username = manager.disconnect(websocket)
        await manager.broadcast_user_list()
        await manager.broadcast_system(f"{username} left the chat")

if __name__ == "__main__":
    # Run on all interfaces so LAN devices can connect
    uvicorn.run(app, host="0.0.0.0", port=8000)