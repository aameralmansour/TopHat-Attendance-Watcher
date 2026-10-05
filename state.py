import sqlite3

class State:
    def __init__(self, path='state.sqlite3'):
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS alerts (course TEXT, key TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(course,key))')
        self.db.commit()

    def seen(self, course, key):
        return self.db.execute('SELECT 1 FROM alerts WHERE course=? AND key=?', (course, key)).fetchone() is not None

    def mark(self, course, key):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO alerts(course,key) VALUES (?,?)', (course, key))

    def close(self):
        self.db.close()
