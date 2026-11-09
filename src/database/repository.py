"""Repository pattern for database CRUD operations."""

import logging
import json
from typing import Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


class Repository:
    """Base repository providing common database operations."""
    
    def __init__(self, db_manager):
        self.db = db_manager
    
    def _execute(self, query: str, params: dict = None) -> list:
        """Execute a query and return results."""
        try:
            return self.db.execute_raw(query, params or {})
        except Exception as e:
            # Only log if it's not a "no such table" error (expected during startup)
            if "no such table" not in str(e):
                logger.error(f"Query failed: {e}")
            return []
    
    def insert(self, table: str, data: dict) -> int:
        """Insert a record and return the new ID."""
        columns = ", ".join(data.keys())
        placeholders = ", ".join([f":{k}" for k in data.keys()])
        query = f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"
        
        cursor = self.db.execute_raw(query, data)
        # Get last inserted row id
        result = self._execute("SELECT last_insert_rowid() as id")
        return result[0]["id"] if result else -1
    
    def update(self, table: str, data: dict, where_clause: str, where_params: dict = None) -> bool:
        """Update records matching a condition."""
        set_clause = ", ".join([f"{k} = :{k}" for k in data.keys()])
        query = f"UPDATE {table} SET {set_clause} WHERE {where_clause}"
        
        try:
            combined_params = {}
            if where_params:
                combined_params.update(where_params)
            combined_params.update(data)
            self.db.execute_raw(query, combined_params)
            return True
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False
    
    def delete(self, table: str, where_clause: str, params: dict = None) -> bool:
        """Delete records matching a condition."""
        query = f"DELETE FROM {table} WHERE {where_clause}"
        
        try:
            self.db.execute_raw(query, params or {})
            return True
        except Exception as e:
            logger.error(f"Delete failed: {e}")
            return False
    
    def get_by_id(self, table: str, id_value: Any) -> Optional[dict]:
        """Get a record by its ID."""
        query = f"SELECT * FROM {table} WHERE id = :id"
        result = self._execute(query, {"id": id_value})
        return result[0] if result else None
    
    def get_all(self, table: str, limit: int | None = None) -> list:
        """Get records from a table, optionally limiting the result."""
        query = f"SELECT * FROM {table}"
        params = {}
        if limit is not None:
            query += " LIMIT :limit"
            params["limit"] = limit
        return self._execute(query, params)


class AccountRepository(Repository):
    """Repository for account operations."""

    def get_by_account_id(self, account_id: str) -> Optional[dict]:
        """Find an account by the generated account_id string."""
        query = "SELECT * FROM accounts WHERE account_id = :account_id"
        result = self._execute(query, {"account_id": account_id})
        return result[0] if result else None

    def get_by_username(self, username: str) -> Optional[dict]:
        """Find an account by username."""
        query = "SELECT * FROM accounts WHERE username = :username"
        result = self._execute(query, {"username": username})
        return result[0] if result else None
    
    def get_by_world_and_server(self, world: str, server: str) -> Optional[dict]:
        """Find an account by world and server."""
        query = "SELECT * FROM accounts WHERE world = :world AND server = :server"
        result = self._execute(query, {"world": world, "server": server})
        return result[0] if result else None
    
    def update_last_login(self, account_id: str) -> bool:
        """Update the last login timestamp."""
        return self.update(
            "accounts",
            {"last_login": datetime.now().isoformat()},
            "id = :id",
            {"id": account_id}
        )


class VillageRepository(Repository):
    """Repository for village operations."""

    def save_dashboard_snapshot(
        self, village_id: str, snapshot_type: str, payload: Any
    ) -> None:
        """Replace one village's latest dashboard snapshot of a given type."""
        query = """
            INSERT INTO dashboard_snapshots (village_id, snapshot_type, payload, recorded_at)
            VALUES (:village_id, :snapshot_type, :payload, :recorded_at)
            ON CONFLICT(village_id, snapshot_type) DO UPDATE SET
                payload = excluded.payload,
                recorded_at = excluded.recorded_at
        """
        self.db.execute_raw(query, {
            "village_id": village_id,
            "snapshot_type": snapshot_type,
            "payload": json.dumps(payload, ensure_ascii=False),
            "recorded_at": datetime.now().isoformat(),
        })

    def get_dashboard_snapshots(self, village_id: str) -> dict[str, Any]:
        """Return the latest saved dashboard details for a village."""
        rows = self._execute(
            "SELECT snapshot_type, payload FROM dashboard_snapshots WHERE village_id = :village_id",
            {"village_id": village_id},
        )
        return {
            row["snapshot_type"]: json.loads(row["payload"])
            for row in rows
        }

    def get_resource_history(self, village_id: str, limit: int = 120) -> list[dict]:
        """Return recent resource observations in chronological order."""
        query = """
            SELECT resource_type, current_amount, recorded_at
            FROM (
                SELECT resource_type, current_amount, recorded_at, id
                FROM resources
                WHERE village_id = :village_id
                ORDER BY recorded_at DESC, id DESC
                LIMIT :limit
            )
            ORDER BY recorded_at ASC, id ASC
        """
        return self._execute(query, {"village_id": village_id, "limit": limit})

    def get_latest_resources(self) -> list:
        """Get the most recent saved resource values for every village and type."""
        query = """
            SELECT id, village_id, resource_type, current_amount, max_capacity,
                   production_rate, recorded_at
            FROM (
                SELECT resources.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY village_id, resource_type
                           ORDER BY recorded_at DESC, id DESC
                       ) AS resource_rank
                FROM resources
            )
            WHERE resource_rank = 1
        """
        return self._execute(query)

    def upsert_village(self, village_id: str, account_id: str, name: str, coordinates: tuple) -> None:
        """Insert or update a village record by its unique village identifier."""
        query = """
            INSERT INTO villages (account_id, village_id, name, coordinates, population, farm_level, chief_level, temple_level, created_at, updated_at)
            VALUES (:account_id, :village_id, :name, :coordinates, 0, 0, 0, 0, :now, :now)
            ON CONFLICT(village_id) DO UPDATE SET
                account_id = excluded.account_id,
                name = excluded.name,
                coordinates = excluded.coordinates,
                updated_at = excluded.updated_at
        """
        self._execute(query, {
            "account_id": account_id,
            "village_id": village_id,
            "name": name,
            "coordinates": str(coordinates),
            "now": datetime.now().isoformat(),
        })

    def get_by_id(self, village_id: str) -> Optional[dict]:
        """Get a village by its ID."""
        query = "SELECT * FROM villages WHERE village_id = :village_id"
        result = self._execute(query, {"village_id": village_id})
        return result[0] if result else None
    
    def get_all_for_account(self, account_id: str) -> list:
        """Get all villages for an account."""
        query = "SELECT * FROM villages WHERE account_id = :account_id"
        return self._execute(query, {"account_id": account_id})
    
    def upsert_resources(self, village_id: str, resources: dict) -> None:
        """Insert or update resource data for a village."""
        for res_type, data in resources.items():
            query = """
                INSERT OR REPLACE INTO resources 
                (village_id, resource_type, current_amount, max_capacity, production_rate, recorded_at)
                VALUES (:village_id, :res_type, :current, :max_cap, :rate, :timestamp)
            """
            self._execute(query, {
                "village_id": village_id,
                "res_type": res_type,
                "current": data.get("current_amount", 0),
                "max_cap": data.get("max_capacity", 0),
                "rate": data.get("production_rate", 0.0),
                "timestamp": datetime.now().isoformat(),
            })


class AttackLogRepository(Repository):
    """Repository for attack log operations."""
    
    def add_attack(self, attacker: str, target: str, outcome: str, loot: int = 0) -> int:
        """Add an attack log entry."""
        return self.insert("attack_logs", {
            "attacker_village_id": attacker,
            "target_village_id": target,
            "outcome": outcome,
            "resources_looted": loot,
            "timestamp": datetime.now().isoformat(),
        })
    
    def get_recent_attacks(self, limit: int = 50) -> list:
        """Get recent attack logs."""
        query = """
            SELECT * FROM attack_logs 
            ORDER BY timestamp DESC 
            LIMIT :limit
        """
        return self._execute(query, {"limit": limit})


class IncomingAttackRepository(Repository):
    """Persist incoming attack observations and their final state."""

    @staticmethod
    def _decode_row(row: dict) -> dict:
        return {
            **row,
            "details": json.loads(row["details"]),
            "event_log": json.loads(row["event_log"]),
        }

    def record_attack(self, attack: dict, observed_at: datetime) -> int:
        """Insert or refresh one live movement without losing its first-seen time."""
        target_village_id = str(attack["village_id"])
        movement_id = str(attack["movement_id"])
        movement_key = f"{target_village_id}:{movement_id}"
        now = observed_at.isoformat()
        details = dict(attack)
        details.pop("seconds_remaining", None)
        details_json = json.dumps(details, ensure_ascii=False)
        existing_rows = self.db.execute_raw(
            """
                SELECT id, status, details, event_log
                FROM incoming_attack_logs
                WHERE movement_key = :movement_key
            """,
            {"movement_key": movement_key},
        )

        if existing_rows:
            existing = existing_rows[0]
            events = json.loads(existing["event_log"])
            previous_details = json.loads(existing["details"])
            for key in ("tournament_square", "detected_live", "tribe_id"):
                # Made once at the first detection; later snapshots (e.g.
                # after a reconnect, when the attack is already on its way)
                # must not drop or overwrite them.
                if key in previous_details and previous_details[key] not in (None, ""):
                    details[key] = previous_details[key]
            details_json = json.dumps(details, ensure_ascii=False)
            status = "incoming"
            if existing["status"] != status:
                events.append({
                    "event": "detected_again",
                    "timestamp": now,
                    "status": status,
                })
            elif previous_details != details:
                events.append({
                    "event": "details_updated",
                    "timestamp": now,
                    "status": status,
                })
            self.db.execute_raw(
                """
                    UPDATE incoming_attack_logs SET
                        movement_id = :movement_id,
                        target_village_name = :target_village_name,
                        attacker_name = :attacker_name,
                        source_village_id = :source_village_id,
                        source_village_name = :source_village_name,
                        movement_type = :movement_type,
                        arrival_time = :arrival_time,
                        masked = :masked,
                        status = :status,
                        last_seen_at = :last_seen_at,
                        resolved_at = NULL,
                        details = :details,
                        event_log = :event_log
                    WHERE id = :id
                """,
                {
                    "id": existing["id"],
                    "movement_id": movement_id,
                    "target_village_name": attack["village_name"],
                    "attacker_name": attack["attacker_name"],
                    "source_village_id": attack.get("source_village_id"),
                    "source_village_name": attack["source_village"],
                    "movement_type": attack["movement_type"],
                    "arrival_time": float(attack["arrival_time"]),
                    "masked": int(bool(attack["masked"])),
                    "status": status,
                    "last_seen_at": now,
                    "details": details_json,
                    "event_log": json.dumps(events, ensure_ascii=False),
                },
            )
            return int(existing["id"])

        events = [{
            "event": "detected",
            "timestamp": now,
            "status": "incoming",
        }]
        self.db.execute_raw(
            """
                INSERT INTO incoming_attack_logs (
                    movement_key, movement_id, target_village_id,
                    target_village_name, attacker_name, source_village_id,
                    source_village_name, movement_type, arrival_time, masked,
                    status, first_detected_at, last_seen_at, resolved_at,
                    details, event_log
                ) VALUES (
                    :movement_key, :movement_id, :target_village_id,
                    :target_village_name, :attacker_name, :source_village_id,
                    :source_village_name, :movement_type, :arrival_time, :masked,
                    :status, :first_detected_at, :last_seen_at, :resolved_at,
                    :details, :event_log
                )
            """,
            {
                "movement_key": movement_key,
                "movement_id": movement_id,
                "target_village_id": target_village_id,
                "target_village_name": attack["village_name"],
                "attacker_name": attack["attacker_name"],
                "source_village_id": attack.get("source_village_id"),
                "source_village_name": attack["source_village"],
                "movement_type": attack["movement_type"],
                "arrival_time": float(attack["arrival_time"]),
                "masked": int(bool(attack["masked"])),
                "status": "incoming",
                "first_detected_at": now,
                "last_seen_at": now,
                "resolved_at": None,
                "details": details_json,
                "event_log": json.dumps(events, ensure_ascii=False),
            },
        )
        return int(self.db.execute_raw(
            """
                SELECT id FROM incoming_attack_logs
                WHERE movement_key = :movement_key
            """,
            {"movement_key": movement_key},
        )[0]["id"])

    def reconcile_village(
        self,
        village_id: str,
        active_movement_keys: set[str],
        server_time: float,
        observed_at: datetime,
    ) -> None:
        """Mark previously live movements that disappeared from a village snapshot."""
        rows = self.db.execute_raw(
            """
                SELECT id, movement_key, arrival_time, event_log
                FROM incoming_attack_logs
                WHERE target_village_id = :village_id AND status = 'incoming'
            """,
            {"village_id": village_id},
        )
        now = observed_at.isoformat()
        for row in rows:
            if row["movement_key"] in active_movement_keys:
                continue
            status = (
                "withdrawn"
                if float(server_time) < float(row["arrival_time"])
                else "arrived"
            )
            events = json.loads(row["event_log"])
            events.append({
                "event": status,
                "timestamp": now,
                "status": status,
            })
            self.db.execute_raw(
                """
                    UPDATE incoming_attack_logs SET
                        status = :status,
                        resolved_at = :resolved_at,
                        event_log = :event_log
                    WHERE id = :id
                """,
                {
                    "id": row["id"],
                    "status": status,
                    "resolved_at": now,
                    "event_log": json.dumps(events, ensure_ascii=False),
                },
            )

    def get_recent_attacks(self) -> list[dict]:
        """Return the complete incoming movement history, newest first."""
        rows = self.db.execute_raw(
            """
                SELECT * FROM incoming_attack_logs
                ORDER BY first_detected_at DESC, id DESC
            """
        )
        return [self._decode_row(row) for row in rows]

    def resolve_source_village_names(
        self,
        village_names: dict[str, str],
        observed_at: datetime,
    ) -> int:
        """Fill in source names for previously recorded movements."""
        updated_count = 0
        now = observed_at.isoformat()
        unresolved_rows = self.db.execute_raw(
            """
                SELECT DISTINCT source_village_id
                FROM incoming_attack_logs
                WHERE source_village_name = ''
                    OR source_village_name = 'Nieznana wioska'
                    OR source_village_name = 'Wioska ' || source_village_id
            """
        )
        unresolved_ids = {
            str(row["source_village_id"])
            for row in unresolved_rows
            if row["source_village_id"]
        }
        for village_id, village_name in village_names.items():
            village_id = str(village_id)
            village_name = str(village_name).strip()
            if (
                village_id not in unresolved_ids
                or not village_id.isdigit()
                or not village_name
            ):
                continue
            rows = self.db.execute_raw(
                """
                    SELECT id, source_village_name, details, event_log
                    FROM incoming_attack_logs
                    WHERE source_village_id = :village_id
                """,
                {"village_id": village_id},
            )
            for row in rows:
                expected_fallback = f"Wioska {village_id}"
                if row["source_village_name"] not in (
                    "",
                    "Nieznana wioska",
                    expected_fallback,
                ):
                    continue
                details = json.loads(row["details"])
                details["source_village"] = village_name
                events = json.loads(row["event_log"])
                events.append({
                    "event": "source_village_resolved",
                    "timestamp": now,
                    "village_id": village_id,
                    "village_name": village_name,
                })
                self.db.execute_raw(
                    """
                        UPDATE incoming_attack_logs SET
                            source_village_name = :village_name,
                            details = :details,
                            event_log = :event_log
                        WHERE id = :id
                    """,
                    {
                        "id": row["id"],
                        "village_name": village_name,
                        "details": json.dumps(details, ensure_ascii=False),
                        "event_log": json.dumps(events, ensure_ascii=False),
                    },
                )
                updated_count += 1
        return updated_count

    def get_attack(self, record_id: int) -> dict | None:
        """Return one history record by its local database ID."""
        rows = self.db.execute_raw(
            "SELECT * FROM incoming_attack_logs WHERE id = :id",
            {"id": record_id},
        )
        return self._decode_row(rows[0]) if rows else None

    def set_attack_detail(self, record_id: int, key: str, value) -> None:
        """Store one extra field in a record's details (e.g. a late estimate)."""
        rows = self.db.execute_raw(
            "SELECT details FROM incoming_attack_logs WHERE id = :id",
            {"id": record_id},
        )
        if not rows:
            return
        details = json.loads(rows[0]["details"] or "{}")
        details[key] = value
        self.db.execute_raw(
            "UPDATE incoming_attack_logs SET details = :details WHERE id = :id",
            {"id": record_id, "details": json.dumps(details, ensure_ascii=False)},
        )

    def delete_attack(self, record_id: int) -> bool:
        """Delete exactly one history record."""
        self.db.execute_raw(
            "DELETE FROM incoming_attack_logs WHERE id = :id",
            {"id": record_id},
        )
        return True
