# -*- coding: utf-8 -*-
"""SQLite 存取层：一张长表 series，一行一个指标一天。只用标准库。"""
import datetime
import os
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    series_id   TEXT NOT NULL,
    date        TEXT NOT NULL,
    value       REAL NOT NULL,
    unit        TEXT DEFAULT '',
    freq        TEXT DEFAULT 'D',
    source      TEXT DEFAULT '',
    sample      TEXT DEFAULT '',
    quality     TEXT DEFAULT 'ok',
    ingest_time TEXT DEFAULT '',
    note        TEXT DEFAULT '',
    PRIMARY KEY (series_id, date)
);
CREATE INDEX IF NOT EXISTS idx_series_date ON series (series_id, date);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS news (
    uid        TEXT PRIMARY KEY,
    kind       TEXT,                          -- flash / news / calendar
    source     TEXT,
    pub_time   TEXT,
    title      TEXT,
    content    TEXT,
    url        TEXT,
    keywords   TEXT,
    level      TEXT DEFAULT 'normal',
    sector     TEXT DEFAULT '',               -- 板块：宏观/黑色/有色/能化/农产品/贵金属/股指/汇率
    fetched_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_news_time ON news (pub_time);
"""


def connect(path):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    """给老库补新增的列（SQLite 不支持 ADD COLUMN IF NOT EXISTS）。"""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(news)").fetchall()}
    if "sector" not in cols:
        conn.execute("ALTER TABLE news ADD COLUMN sector TEXT DEFAULT ''")
        conn.commit()


def upsert(conn, series_id, date, value, unit='', freq='D',
           source='', sample='', quality='ok', note=''):
    now = datetime.datetime.now().isoformat(timespec='seconds')
    conn.execute(
        """INSERT INTO series
               (series_id, date, value, unit, freq, source, sample, quality, ingest_time, note)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(series_id, date) DO UPDATE SET
               value=excluded.value, unit=excluded.unit, freq=excluded.freq,
               source=excluded.source, sample=excluded.sample, quality=excluded.quality,
               ingest_time=excluded.ingest_time, note=excluded.note""",
        (series_id, date, float(value), unit, freq, source, sample, quality, now, note),
    )


def latest(conn, series_id):
    cur = conn.execute(
        "SELECT * FROM series WHERE series_id = ? ORDER BY date DESC LIMIT 1",
        (series_id,),
    )
    return cur.fetchone()


def latest_before(conn, series_id, date):
    cur = conn.execute(
        "SELECT * FROM series WHERE series_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (series_id, date),
    )
    return cur.fetchone()


def history(conn, series_id, limit=None):
    """按日期升序返回；limit 表示取最近 N 条（仍然升序）。"""
    if limit:
        rows = conn.execute(
            "SELECT * FROM series WHERE series_id = ? ORDER BY date DESC LIMIT ?",
            (series_id, limit),
        ).fetchall()
        return list(reversed(rows))
    return conn.execute(
        "SELECT * FROM series WHERE series_id = ? ORDER BY date ASC", (series_id,)
    ).fetchall()


def series_ids(conn):
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT series_id FROM series ORDER BY series_id")]


def count(conn, series_id):
    return conn.execute(
        "SELECT COUNT(*) FROM series WHERE series_id = ?", (series_id,)).fetchone()[0]
