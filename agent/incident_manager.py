"""Serialize simulation changes and deduplicate acknowledged incident IDs."""

import asyncio
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from core.audit import read_report
from core.schemas import AlertPayload


class IncidentConflict(ValueError):
    pass


class IncidentManager:
    def __init__(self, database: Path):
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS incidents (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            connection.commit()
        self.registration = asyncio.Lock()
        self.execution = asyncio.Lock()
        self.jobs = {}

    def _claim(self, alert: AlertPayload, allow_new: bool = True) -> bool:
        payload = alert.model_dump(mode="json", exclude={"timestamp"})
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with closing(sqlite3.connect(self.database)) as connection:
            if not allow_new and connection.execute("SELECT 1 FROM incidents WHERE id=?", (alert.alert_id,)).fetchone() is None:
                raise IncidentConflict("Existing report has no registered alert fingerprint; review before reusing its ID")
            connection.execute("INSERT OR IGNORE INTO incidents VALUES (?, ?)", (alert.alert_id, fingerprint))
            created = connection.total_changes == 1
            stored = connection.execute("SELECT fingerprint FROM incidents WHERE id=?", (alert.alert_id,)).fetchone()[0]
            connection.commit()
        if stored != fingerprint:
            raise IncidentConflict("Incident ID already belongs to a different alert")
        return created

    async def submit(self, alert: AlertPayload, runner):
        async with self.registration:
            existing = read_report(alert.alert_id)
            created = self._claim(alert, allow_new=existing is None)
            report = existing or read_report(alert.alert_id)
            if report is not None:
                return report
            task = self.jobs.get(alert.alert_id)
            if task is None:
                if not created:
                    raise IncidentConflict("Interrupted incident requires review before another execution")

                async def run():
                    async with self.execution:
                        return await runner(alert)

                task = asyncio.create_task(run())
                self.jobs[alert.alert_id] = task

                def finished(job, identifier=alert.alert_id):
                    self.jobs.pop(identifier, None)
                    if not job.cancelled():
                        job.exception()

                task.add_done_callback(finished)
        return await asyncio.shield(task)
