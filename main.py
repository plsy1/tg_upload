import os
import asyncio
import re
import json
import time
import sqlite3
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, UploadFile, File, Form, Depends, HTTPException, status
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse, JSONResponse
from telethon import TelegramClient, events, utils
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError
import logging
import platform
import tempfile
from pathlib import Path
from PIL import Image

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Mount static files and templates
os.makedirs("static", exist_ok=True)
os.makedirs("templates", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Database & Persistence ---
DB_FILE = "data.db"

class DBManager:
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path
        self._init_db()
        self._migrate_json()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS history (path TEXT PRIMARY KEY, upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()

    def _migrate_json(self):
        """Migrate legacy JSON files to SQLite"""
        # Migrate Config
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self.set_config(k, v)
                os.rename("config.json", "config.json.bak")
                logger.info("Migrated config.json to SQLite")
            except Exception as e:
                logger.error(f"Migration error (config): {e}")

        # Migrate History
        if os.path.exists("upload_history.json"):
            try:
                with open("upload_history.json", "r") as f:
                    paths = json.load(f)
                    for p in paths:
                        self.add_history(p)
                os.rename("upload_history.json", "upload_history.json.bak")
                logger.info("Migrated upload_history.json to SQLite")
            except Exception as e:
                logger.error(f"Migration error (history): {e}")

    def get_all_config(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT key, value FROM config")
            res = {}
            for k, v in cursor.fetchall():
                try: res[k] = json.loads(v)
                except: res[k] = v
            # Default values
            if "tool_password" not in res: res["tool_password"] = "admin"
            return res

    def set_config(self, key: str, value: Any):
        with sqlite3.connect(self.db_path) as conn:
            val_str = json.dumps(value)
            conn.execute("INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)", (key, val_str))
            conn.commit()

    def get_history(self) -> set:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT path FROM history")
            return {row[0] for row in cursor.fetchall()}

    def add_history(self, path: str):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT OR IGNORE INTO history (path) VALUES (?)", (path,))
            conn.commit()

db = DBManager()

# Global state
class TGState:
    client: Optional[TelegramClient] = None
    config: Dict[str, Any] = db.get_all_config()
    session_file: str = "tg_session"
    is_tool_authenticated: bool = False

    def update_config(self, key: str, value: Any):
        self.config[key] = value
        db.set_config(key, value)

state = TGState()

# Compatibility shims for existing code
def load_config(): return state.config
def save_config(config): pass # Already saved via state.update_config
def load_history(): return db.get_history()
def save_history(history): pass # We use db.add_history incrementally now

# --- Tool Auth ---
@app.post("/api/tool/login")
async def tool_login(password: str = Form(...)):
    if password == state.config.get("tool_password", "admin"):
        state.is_tool_authenticated = True
        return {"status": "success"}
    return JSONResponse(status_code=401, content={"status": "error", "message": "密码错误"})

@app.post("/api/config/auto-upload")
async def update_auto_upload(enabled: bool = Form(...)):
    state.update_config("auto_upload_enabled", enabled)
    return {"status": "success", "enabled": enabled}

async def check_auth(request: Request):
    if not state.is_tool_authenticated:
        # For simple local use, we'll use this global flag. 
        # In a real app, this should be session/cookie based.
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Routes ---

@app.get("/")
async def index(request: Request):
    if not state.is_tool_authenticated:
        return templates.TemplateResponse("login.html", {"request": request})
    
    # Check if TG is already auth'd
    is_tg_auth = False
    if state.config.get("api_id") and state.config.get("api_hash"):
        if not state.client:
            state.client = TelegramClient(state.session_file, state.config["api_id"], state.config["api_hash"])
            await state.client.connect()
        is_tg_auth = await state.client.is_user_authorized()

    return templates.TemplateResponse("index.html", {
        "request": request, 
        "is_tg_auth": is_tg_auth,
        "config": state.config
    })

@app.post("/api/setup")
async def setup(api_id: int = Form(...), api_hash: str = Form(...), phone: str = Form(...)):
    logger.info(f"Received setup request for phone: {phone}")
    state.update_config("api_id", api_id)
    state.update_config("api_hash", api_hash)
    state.update_config("phone", phone)
    
    if state.client:
        try: await state.client.disconnect()
        except: pass

    state.client = TelegramClient(state.session_file, api_id, api_hash)
    await state.client.connect()
    
    is_auth = await state.client.is_user_authorized()
    if not is_auth:
        await state.client.send_code_request(phone)
        return {"status": "needs_code"}
    
    return {"status": "authorized"}

@app.post("/api/verify")
async def verify(code: str = Form(...), password: Optional[str] = Form(None)):
    if not state.client:
        return {"status": "error", "message": "Client not initialized"}
    
    try:
        try:
            await state.client.sign_in(state.config["phone"], code)
        except SessionPasswordNeededError:
            if not password:
                return {"status": "error", "message": "Password required"}
            await state.client.sign_in(password=password)
        
        return {"status": "authorized"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/browse")
async def browse_directory(path: str = ""):
    """API for the custom file browser"""
    if not path:
        # Default to user home
        path = str(Path.home())
    
    try:
        p = Path(path)
        if not p.exists():
            return {"status": "error", "message": "路径不存在"}
        
        items = []
        # Add parent directory
        if p.parent != p:
            items.append({"name": "..", "path": str(p.parent), "is_dir": True})
            
        for child in p.iterdir():
            try:
                # We only care about directories for picking, but show files as reference?
                # Let's just show directories for easier picking.
                if child.is_dir() and not child.name.startswith('.'):
                    items.append({
                        "name": child.name,
                        "path": str(child.absolute()),
                        "is_dir": True
                    })
            except: continue
            
        # Sort items: dirs first
        items.sort(key=lambda x: x["name"].lower())
            
        return {
            "status": "success",
            "current_path": str(p.absolute()),
            "items": items
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/chats")
async def get_chats():
    if not state.client or not await state.client.is_user_authorized():
        return {"status": "unauthorized"}
    
    chats = []
    # Include Saved Messages as a priority
    chats.append({"id": "me", "name": "⭐ Saved Messages (收藏夹)", "type": "user"})
    
    async for dialog in state.client.iter_dialogs(limit=200):
        entity = dialog.entity
        type_str = "user"
        if dialog.is_channel: type_str = "channel"
        elif dialog.is_group: type_str = "group"
        
        # Check if it's a bot
        is_bot = getattr(entity, 'bot', False)
        display_name = dialog.name
        if is_bot:
            display_name = f"🤖 {display_name}"
            type_str = "bot"
            
        username = getattr(entity, 'username', '')
        if username:
            display_name = f"{display_name} (@{username})"

        chats.append({
            "id": dialog.id,
            "name": display_name,
            "type": type_str
        })
    return {"chats": chats}

# --- Helpers ---
def natural_sort_key(s):
    """Sort strings containing numbers in human order (1, 2, 10 instead of 1, 10, 2)"""
    return [int(text) if text.isdigit() else text.lower()
            for text in re.split('([0-9]+)', str(s))]

def load_history():
    return db.get_history()

def save_history(history):
    pass

def parse_douyin_info(dir_path):
    """Parse Douyin structure: .../User_ID/(notes|videos)/[NoteTitle]"""
    p = Path(dir_path)
    parts = p.parts
    user_name = "Unknown"
    user_id = "Unknown"
    content_type = ""
    note_title = ""
    
    for i, part in enumerate(parts):
        if "_" in part and i + 1 < len(parts):
            if parts[i+1] in ["notes", "videos"]:
                name_parts = part.split("_")
                user_name = name_parts[0]
                if len(name_parts) > 1: user_id = name_parts[1]
                content_type = "图文" if parts[i+1] == "notes" else "视频"
                if parts[i+1] == "notes" and i + 2 < len(parts):
                    note_title = parts[i+2]
                break
    return user_name, user_id, content_type, note_title

def convert_webp_to_jpg(file_path, temp_list):
    if not file_path.lower().endswith('.webp'):
        return file_path
    try:
        with Image.open(file_path) as img:
            rgb_img = img.convert('RGB')
            tmp = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
            tmp_path = tmp.name
            tmp.close()
            rgb_img.save(tmp_path, 'JPEG', quality=95)
            temp_list.append(tmp_path)
            return tmp_path
    except Exception as e:
        logger.error(f"Failed to convert {file_path}: {e}")
        return file_path

async def auto_upload_task():
    """Background task for automatic uploads"""
    logger.info("Auto-upload background task started.")
    while True:
        try:
            config = load_config()
            # Priority: Env Var > SQLite Config
            enabled_env = os.environ.get("AUTO_UPLOAD", "").lower() == "true"
            enabled = enabled_env or config.get("auto_upload_enabled", False)
            folder = os.environ.get("SCAN_FOLDER") or config.get("last_folder")
            chat = os.environ.get("TARGET_CHAT") or config.get("last_chat")
            
            if enabled and folder and chat and state.client and await state.client.is_user_authorized():
                logger.info(f"Auto-scan started for: {folder}")
                history = load_history()
                
                try: # Resolve entity
                    try: peer = int(chat)
                    except: peer = chat
                    resolved_id = await state.client.get_entity(peer)
                except Exception as e:
                    logger.error(f"Auto-upload entity error: {e}")
                    await asyncio.sleep(60)
                    continue

                folder_groups = {}
                for root, _, filenames in os.walk(folder):
                    valid_files = [os.path.join(root, f) for f in filenames if not f.startswith('.')]
                    if valid_files: folder_groups[root] = sorted(valid_files, key=natural_sort_key)

                for root_dir, file_paths in folder_groups.items():
                    if root_dir in history: continue
                    
                    # --- Stability Check ---
                    # Skip if any file was modified within the last 60 seconds (likely still downloading)
                    now = time.time()
                    STABILITY_THRESHOLD = 60 
                    is_stable = True
                    for f_path in file_paths:
                        try:
                            if now - os.path.getmtime(f_path) < STABILITY_THRESHOLD:
                                is_stable = False
                                break
                        except: pass
                    
                    if not is_stable:
                        logger.info(f"Skipping {root_dir} - still being modified, waiting for stability...")
                        continue
                    # -----------------------
                    if c_type:
                        cap = [f"#{user_name} #id_{user_id}"]
                        if n_title: cap.append(n_title)
                        display_name = f"{user_name} - {n_title or c_type}"
                    else:
                        display_name = os.path.basename(root_dir)
                        cap = [f"📁 文件夹: {display_name}"]

                    total_batches = (len(file_paths) + 9) // 10
                    success = True
                    temps = []
                    
                    for batch_idx, i in enumerate(range(0, len(file_paths), 10)):
                        batch = file_paths[i:i+10]
                        proc_batch = [convert_webp_to_jpg(f, temps) for f in batch]
                        
                        final_cap = cap.copy()
                        if total_batches > 1:
                            final_cap.insert(1, f"📦 Part {batch_idx + 1}/{total_batches}")
                        
                        try:
                            if len(proc_batch) > 1:
                                await state.client.send_file(resolved_id, proc_batch, caption="\n".join(final_cap), force_document=False)
                            else:
                                await state.client.send_file(resolved_id, proc_batch[0], caption="\n".join(final_cap), force_document=False)
                        except Exception as e:
                            logger.error(f"Auto-upload fail: {e}")
                            success = False
                            break
                        await asyncio.sleep(2)

                    for t in temps: 
                        try: os.remove(t)
                        except: pass
                        
                    if success:
                        db.add_history(root_dir)
                        logger.info(f"Auto-uploaded: {display_name}")

            await asyncio.sleep(config.get("auto_upload_interval", 300))
        except Exception as e:
            logger.error(f"Auto-upload loop error: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(auto_upload_task())

@app.websocket("/ws/upload")
async def websocket_upload(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            
            if msg["action"] == "start_upload":
                target_chat_id = msg["chat_id"]
                folder_path = msg["folder_path"]
                
                # Persist settings
                state.update_config("last_folder", folder_path)
                state.update_config("last_chat", target_chat_id)
                
                # Resolve Entity first
                try:
                    # If it's a digit ID, Telethon might need it as int
                    try:
                        peer = int(target_chat_id)
                    except:
                        peer = target_chat_id # Probably @username
                    
                    # get_entity is more robust than get_input_entity as it can fetch from network
                    resolved_id = await state.client.get_entity(peer)
                    logger.info(f"Resolved entity: {resolved_id.id if hasattr(resolved_id, 'id') else resolved_id}")
                except Exception as e:
                    await websocket.send_json({"type": "error", "message": f"无法解析目标对话: {str(e)}。建议输入 @用户名 或 确保您已加入该频道。"})
                    continue

                # Group files by directory
                folder_groups = {}
                for root, _, filenames in os.walk(folder_path):
                    valid_files = [os.path.join(root, f) for f in filenames if not f.startswith('.')]
                    if valid_files:
                        folder_groups[root] = sorted(valid_files, key=natural_sort_key)
                
                total_files = sum(len(files) for files in folder_groups.values())
                await websocket.send_json({"type": "info", "message": f"找到 {total_files} 个文件，将按文件夹分组打包上传..."})
                
                processed_count = 0
                temp_files = [] # Track temp files to clean up

                for root_dir, file_paths in folder_groups.items():
                    user_name, user_id, c_type, n_title = parse_douyin_info(root_dir)
                    
                    # Base caption logic
                    if c_type:
                        base_caption_parts = [f"#{user_name} #id_{user_id}"]
                        if n_title:
                            base_caption_parts.append(n_title)
                        folder_display = f"{user_name} - {n_title or c_type}"
                    else:
                        folder_display = os.path.basename(root_dir)
                        if root_dir == folder_path: folder_display = "根目录"
                        base_caption_parts = [f"📁 文件夹: {folder_display}"]
                    
                    # Telegram media group limit is 10
                    total_batches = (len(file_paths) + 9) // 10
                    for batch_idx, i in enumerate(range(0, len(file_paths), 10)):
                        batch = file_paths[i:i+10]
                        processed_batch = [convert_webp_to_jpg(f, temp_files) for f in batch]
                        
                        current_caption_parts = base_caption_parts.copy()
                        if total_batches > 1:
                            current_caption_parts.insert(1, f"📦 Part {batch_idx + 1}/{total_batches}")
                        
                        display_caption = "\n".join(current_caption_parts)
                        
                        await websocket.send_json({
                            "type": "info", 
                            "message": f"正在处理 {folder_display} 批次 {batch_idx+1}/{total_batches}..."
                        })

                        async def progress_callback(current, total):
                            try:
                                percent = (current / total) * 100
                                await websocket.send_json({
                                    "type": "progress", 
                                    "file": folder_display, 
                                    "index": processed_count + len(batch), 
                                    "total": total_files,
                                    "percent": round(percent, 2)
                                })
                            except: pass

                        try:
                            if len(processed_batch) > 1:
                                await state.client.send_file(resolved_id, processed_batch, caption=display_caption, force_document=False, progress_callback=progress_callback)
                            else:
                                await state.client.send_file(resolved_id, processed_batch[0], caption=display_caption, force_document=False, progress_callback=progress_callback)
                            
                            processed_count += len(batch)
                            await websocket.send_json({
                                "type": "progress", 
                                "file": f"完成: {folder_display}", 
                                "index": processed_count, 
                                "total": total_files, 
                                "status": "completed"
                            })
                        except Exception as e:
                            await websocket.send_json({"type": "error", "message": f"上传失败: {str(e)}"})
                        
                        await asyncio.sleep(1)

                # Manual upload also saves to history
                for root_dir in folder_groups.keys():
                    db.add_history(root_dir)

                for tmp_f in temp_files:
                    try: os.remove(tmp_f)
                    except: pass

                await websocket.send_json({"type": "done", "message": "所有内容已完成！"})

    except WebSocketDisconnect:
        pass

if __name__ == "__main__":
    import uvicorn
    # Use PORT environment variable if available, otherwise default to 8000
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
