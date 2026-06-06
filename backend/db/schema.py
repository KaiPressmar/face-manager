import sqlite3
from pathlib import Path
from ..config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS person (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS cluster (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        label TEXT,
        person_id INTEGER,
        FOREIGN KEY(person_id) REFERENCES person(id)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS face (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT NOT NULL,
        bbox_x REAL,
        bbox_y REAL,
        bbox_w REAL,
        bbox_h REAL,
        cluster_id INTEGER,
        embedding BLOB,
        FOREIGN KEY(cluster_id) REFERENCES cluster(id)
    );
    """)

    conn.commit()
    conn.close()
