"""Application Tracking System (Local Storage)."""
import os
import json
from datetime import datetime
from typing import Dict, Any, List


class ApplicationTracker:
    def __init__(self, storage_path: str = "data/applications.json"):
        self.storage_path = storage_path
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        if not os.path.exists(self.storage_path):
            with open(self.storage_path, "w") as f:
                json.dump([], f)

    def load_applications(self) -> List[Dict[str, Any]]:
        try:
            with open(self.storage_path, "r") as f:
                return json.load(f)
        except Exception:
            return []

    def record_application(
        self,
        url: str,
        company: str,
        title: str,
        status: str,
        fields_filled: int,
        notes: str = "",
    ):
        apps = self.load_applications()
        entry = {
            "id": len(apps) + 1,
            "url": url,
            "company": company or "Unknown Company",
            "title": title or "Unknown Title",
            "status": status,
            "fields_filled": fields_filled,
            "notes": notes,
            "timestamp": datetime.now().isoformat(),
        }
        apps.append(entry)
        with open(self.storage_path, "w") as f:
            json.dump(apps, f, indent=2)
        return entry
