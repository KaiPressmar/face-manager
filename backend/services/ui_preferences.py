import json
import sqlite3
from contextlib import closing, contextmanager

from ..config import get_data_root


UiPreferences = dict[str, str | bool]

_PREFERENCE_CHOICES = {
    "map_style": ("standard", "landscape", "satellite"),
    "map_country_names": (False, True),
    "image_grid_size": ("xsmall", "small", "medium", "large"),
    "help_introduction_seen": (False, True),
}


def validate_ui_preferences(values: object) -> UiPreferences:
    if not isinstance(values, dict):
        raise ValueError("Die Anzeigeeinstellungen müssen als Objekt angegeben werden.")
    for name, value in values.items():
        if type(value) not in (str, bool) or value not in _PREFERENCE_CHOICES.get(name, ()):
            raise ValueError("Eine Anzeigeeinstellung ist unbekannt oder ungültig.")
    return values


@contextmanager
def _connect():
    root = get_data_root()
    root.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(root / "ui-preferences.sqlite", timeout=1)) as connection, connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS ui_preferences (
                name TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL
            )
        """)
        yield connection


def _read(connection: sqlite3.Connection) -> UiPreferences:
    rows = connection.execute("SELECT name, value FROM ui_preferences")
    return {name: json.loads(value) for name, value in rows}


def load_ui_preferences() -> UiPreferences:
    with _connect() as connection:
        return _read(connection)


def update_ui_preferences(values: UiPreferences, *, only_if_absent: bool = False) -> UiPreferences:
    conflict_action = "DO NOTHING" if only_if_absent else "DO UPDATE SET value = excluded.value"
    with _connect() as connection:
        connection.executemany(
            f"INSERT INTO ui_preferences (name, value) VALUES (?, ?) ON CONFLICT(name) {conflict_action}",
            ((name, json.dumps(value)) for name, value in values.items()),
        )
        return _read(connection)
