import torch
import os

WSL_DRIVE_MAP = {
    "D:": "/mnt/d",
    "C:": "/mnt/c",
}

def to_wsl_path(win_path: str) -> str:
    win_path = win_path.replace("\\", "/")
    drive, rest = win_path.split(":", 1)
    base = WSL_DRIVE_MAP.get(f"{drive}:", f"/mnt/{drive.lower()}")
    return base + rest

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

BATCH_SIZE = 32
EMBEDDING_DIM = 512
DB_PATH = os.path.join(os.path.dirname(__file__), "db", "database.sqlite")
