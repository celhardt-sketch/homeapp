from .db import get_conn

DEFAULT_ROOMS = [
    {
        "slug": "kitchen",
        "name": "Kitchen",
        "icon": "utensils",
        "tasks": [
            ("Wipe down counters", "", 1),
            ("Clean out refrigerator", "Toss expired food, wipe shelves", 7),
            ("Replace water filter", "", 180),
            ("Clean range hood filter", "", 90),
            ("Run dishwasher cleaning cycle", "", 30),
        ],
    },
    {
        "slug": "bathroom",
        "name": "Bathroom",
        "icon": "bath",
        "tasks": [
            ("Clean toilet", "", 7),
            ("Scrub shower & tub", "", 7),
            ("Replace toothbrush heads", "", 90),
            ("Check for leaks under sink", "", 30),
        ],
    },
    {
        "slug": "laundry",
        "name": "Laundry Room",
        "icon": "washing-machine",
        "tasks": [
            ("Clean dryer lint trap", "", 1),
            ("Clean dryer vent hose", "", 180),
            ("Wipe washer door gasket", "", 30),
            ("Check hoses for cracks", "", 90),
        ],
    },
    {
        "slug": "garage",
        "name": "Garage",
        "icon": "car",
        "tasks": [
            ("Test garage door safety sensors", "", 30),
            ("Change furnace filter", "", 90),
            ("Check smoke/CO detector batteries", "", 180),
        ],
    },
]

DEFAULT_UPKEEP = [
    # (name, category, interval_days, notes)
    ("Change HVAC / furnace filter", "HVAC", 90, "Check filter size on the old one before buying"),
    ("Check car oil level & tire pressure", "Car", 30, ""),
    ("Car oil change", "Car", 180, ""),
    ("Clean window screens", "Exterior", 365, ""),
    ("Clean gutters", "Exterior", 180, ""),
    ("Test smoke & CO detectors", "Safety", 180, ""),
    ("Flush water heater", "Plumbing", 365, ""),
    ("Replace refrigerator water filter", "Appliances", 180, ""),
]


def seed_if_empty() -> None:
    with get_conn() as conn:
        if not conn.execute("SELECT 1 FROM settings WHERE key = 'upkeep_seeded'").fetchone():
            conn.executemany(
                "INSERT INTO upkeep_items (name, category, interval_days, notes) VALUES (?, ?, ?, ?)",
                DEFAULT_UPKEEP,
            )
            conn.execute("INSERT INTO settings (key, value) VALUES ('upkeep_seeded', '1')")
        count = conn.execute("SELECT COUNT(*) FROM rooms").fetchone()[0]
        if count:
            return
        for i, room in enumerate(DEFAULT_ROOMS):
            cur = conn.execute(
                "INSERT INTO rooms (slug, name, icon, sort_order) VALUES (?, ?, ?, ?)",
                (room["slug"], room["name"], room["icon"], i),
            )
            room_id = cur.lastrowid
            for j, (title, desc, freq) in enumerate(room["tasks"]):
                conn.execute(
                    "INSERT INTO tasks (room_id, title, description, frequency_days, sort_order) VALUES (?, ?, ?, ?, ?)",
                    (room_id, title, desc, freq, j),
                )
