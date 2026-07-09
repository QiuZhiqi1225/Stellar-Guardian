from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

from app.models import utc_now_iso


LEGACY_DEMO_EXTERNAL_KEY = "147852369"
LEGACY_DEMO_APP_USER_IDS = ("qiu_father_001", "qiu_mother_001")


class DatabaseRepository:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.migrate_legacy_demo_data()
        self.cleanup_integrity()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    secret_salt TEXT NOT NULL,
                    secret_hash TEXT NOT NULL,
                    current_device_token TEXT NOT NULL DEFAULT '',
                    current_platform TEXT NOT NULL DEFAULT 'web',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_contact_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_user_id INTEGER NOT NULL,
                    contact_user_id INTEGER NOT NULL,
                    relationship_label TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, contact_user_id),
                    FOREIGN KEY(owner_user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(contact_user_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    external_key TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS app_recipients (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    recipient_name TEXT NOT NULL,
                    app_user_id TEXT NOT NULL,
                    device_token TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'android',
                    severity_scope TEXT NOT NULL DEFAULT 'all',
                    priority INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS event_logs (
                    event_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    target_external_key TEXT,
                    target_label TEXT,
                    provider TEXT NOT NULL,
                    message TEXT NOT NULL,
                    raw_payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS call_sessions (
                    session_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    profile_id INTEGER,
                    recipient_id INTEGER NOT NULL,
                    recipient_name TEXT NOT NULL,
                    app_user_id TEXT NOT NULL,
                    device_token TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    join_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    accepted_at TEXT,
                    ended_at TEXT,
                    FOREIGN KEY(event_id) REFERENCES event_logs(event_id) ON DELETE CASCADE,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id) ON DELETE SET NULL,
                    FOREIGN KEY(recipient_id) REFERENCES app_recipients(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS call_session_participants (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    participant_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    label TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE(session_id, participant_id),
                    FOREIGN KEY(session_id) REFERENCES call_sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS call_session_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sender_participant_id TEXT NOT NULL,
                    sender_role TEXT NOT NULL,
                    target_participant_id TEXT,
                    signal_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(session_id) REFERENCES call_sessions(session_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS mini_program_devices (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    app_user_id TEXT NOT NULL UNIQUE,
                    recipient_name TEXT NOT NULL,
                    device_token TEXT NOT NULL,
                    platform TEXT NOT NULL DEFAULT 'wechat_miniprogram',
                    external_key TEXT,
                    wechat_openid TEXT,
                    wechat_unionid TEXT,
                    notification_enabled INTEGER NOT NULL DEFAULT 0,
                    granted_template_ids_json TEXT NOT NULL DEFAULT '[]',
                    last_permission_result_json TEXT NOT NULL DEFAULT '{}',
                    last_login_at TEXT,
                    subscription_updated_at TEXT,
                    last_notification_at TEXT,
                    last_notification_status TEXT NOT NULL DEFAULT '',
                    last_notification_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            self._ensure_column(conn, "profiles", "owner_user_id", "INTEGER")
            self._ensure_column(conn, "app_recipients", "source_type", "TEXT NOT NULL DEFAULT 'manual'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_profiles_owner_user_id ON profiles(owner_user_id)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_recipients_profile_app_user_source "
                "ON app_recipients(profile_id, app_user_id, source_type)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_app_recipients_app_user ON app_recipients(app_user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_contact_links_owner ON user_contact_links(owner_user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mini_program_devices_token ON mini_program_devices(device_token)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mini_program_devices_openid ON mini_program_devices(wechat_openid)")

    def _ensure_column(self, conn: sqlite3.Connection, table_name: str, column_name: str, column_definition: str) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if column_name in columns:
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")

    def cleanup_integrity(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM app_recipients
                WHERE profile_id NOT IN (SELECT id FROM profiles)
                """
            )
            conn.execute(
                """
                DELETE FROM call_sessions
                WHERE recipient_id NOT IN (SELECT id FROM app_recipients)
                """
            )
            conn.execute(
                """
                DELETE FROM call_session_participants
                WHERE session_id NOT IN (SELECT session_id FROM call_sessions)
                """
            )
            conn.execute(
                """
                DELETE FROM call_session_signals
                WHERE session_id NOT IN (SELECT session_id FROM call_sessions)
                """
            )
            owner_rows = conn.execute(
                """
                SELECT DISTINCT owner_user_id
                FROM profiles
                WHERE owner_user_id IS NOT NULL
                """
            ).fetchall()
        for row in owner_rows:
            self._sync_linked_recipients_for_owner(int(row["owner_user_id"]))

    def migrate_legacy_demo_data(self) -> None:
        now = utc_now_iso()
        assigned_owner_internal_id: int | None = None

        with self._connect() as conn:
            legacy_profile = conn.execute(
                """
                SELECT id, display_name, owner_user_id
                FROM profiles
                WHERE external_key = ?
                """,
                (LEGACY_DEMO_EXTERNAL_KEY,),
            ).fetchone()
            if legacy_profile is None:
                return

            profile_id = int(legacy_profile["id"])
            if legacy_profile["owner_user_id"] is None:
                matching_users = conn.execute(
                    """
                    SELECT id
                    FROM users
                    WHERE display_name = ?
                    ORDER BY id ASC
                    """,
                    (legacy_profile["display_name"],),
                ).fetchall()
                if len(matching_users) == 1:
                    assigned_owner_internal_id = int(matching_users[0]["id"])
                    conn.execute(
                        """
                        UPDATE profiles
                        SET owner_user_id = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (assigned_owner_internal_id, now, profile_id),
                    )
            else:
                assigned_owner_internal_id = int(legacy_profile["owner_user_id"])

            placeholders = ",".join("?" for _ in LEGACY_DEMO_APP_USER_IDS)
            conn.execute(
                f"""
                DELETE FROM app_recipients
                WHERE profile_id = ?
                  AND source_type = 'manual'
                  AND app_user_id IN ({placeholders})
                """,
                (profile_id, *LEGACY_DEMO_APP_USER_IDS),
            )

        if assigned_owner_internal_id is not None:
            self._sync_linked_recipients_for_owner(assigned_owner_internal_id)

    def _hash_secret(self, secret: str, salt: str) -> str:
        digest = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt.encode("utf-8"), 120000)
        return digest.hex()

    def _create_secret_payload(self, secret: str) -> tuple[str, str]:
        salt = secrets.token_hex(16)
        return salt, self._hash_secret(secret, salt)

    def _user_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "user_id": row["user_id"],
            "display_name": row["display_name"],
            "notes": row["notes"],
            "current_platform": row["current_platform"],
            "has_device_token": bool(str(row["current_device_token"] or "").strip()),
        }

    def _profile_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "external_key": row["external_key"],
            "display_name": row["display_name"],
            "notes": row["notes"],
            "owner_user_id": row["owner_public_id"] if "owner_public_id" in row.keys() else None,
            "owner_display_name": row["owner_display_name"] if "owner_display_name" in row.keys() else None,
        }

    def _recipient_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "profile_id": int(row["profile_id"]),
            "recipient_name": row["recipient_name"],
            "app_user_id": row["app_user_id"],
            "device_token": row["device_token"],
            "platform": row["platform"],
            "severity_scope": row["severity_scope"],
            "priority": int(row["priority"]),
            "source_type": row["source_type"] if "source_type" in row.keys() else "manual",
        }

    def _participant_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "participant_id": row["participant_id"],
            "role": row["role"],
            "label": row["label"],
            "joined_at": row["joined_at"],
            "last_seen_at": row["last_seen_at"],
        }

    def _mini_program_device_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        granted_template_ids = [str(item) for item in json.loads(row["granted_template_ids_json"] or "[]")]
        granted_template_counts: dict[str, int] = {}
        for template_id in granted_template_ids:
            granted_template_counts[template_id] = granted_template_counts.get(template_id, 0) + 1
        permission_result = json.loads(row["last_permission_result_json"] or "{}")
        openid = str(row["wechat_openid"] or "").strip()
        return {
            "app_user_id": row["app_user_id"],
            "recipient_name": row["recipient_name"],
            "device_token": row["device_token"],
            "platform": row["platform"],
            "external_key": row["external_key"],
            "has_wechat_openid": bool(openid),
            "wechat_openid": openid,
            "wechat_unionid": str(row["wechat_unionid"] or "").strip(),
            "notification_enabled": bool(int(row["notification_enabled"] or 0)),
            "granted_template_ids": granted_template_ids,
            "granted_template_count": len(granted_template_ids),
            "granted_template_counts": granted_template_counts,
            "last_permission_result": {
                str(key): str(value)
                for key, value in permission_result.items()
            },
            "last_login_at": row["last_login_at"],
            "subscription_updated_at": row["subscription_updated_at"],
            "last_notification_at": row["last_notification_at"],
            "last_notification_status": row["last_notification_status"],
            "last_notification_error": row["last_notification_error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _session_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        raw_payload = {}
        if "event_raw_payload_json" in row.keys() and row["event_raw_payload_json"]:
            raw_payload = json.loads(row["event_raw_payload_json"])
        location = self._extract_location_from_event_payload(raw_payload)
        event_payload = None
        if "event_title" in row.keys():
            event_payload = {
                "severity": row["event_severity"],
                "title": row["event_title"],
                "body": row["event_body"],
                "occurred_at": row["event_occurred_at"],
                "target_external_key": row["target_external_key"],
                "target_label": row["target_label"],
                "profile_external_key": row["profile_external_key"],
                "profile_display_name": row["profile_display_name"],
                "location": location,
            }

        payload = {
            "session_id": row["session_id"],
            "event_id": row["event_id"],
            "recipient_id": int(row["recipient_id"]),
            "recipient_name": row["recipient_name"],
            "app_user_id": row["app_user_id"],
            "device_token": row["device_token"],
            "platform": row["platform"],
            "status": row["status"],
            "channel": row["channel"],
            "detail": row["detail"],
            "join_path": row["join_path"],
            "created_at": row["created_at"],
            "accepted_at": row["accepted_at"],
            "ended_at": row["ended_at"],
            "location": location,
            "event": event_payload,
        }
        if event_payload:
            payload.update(
                {
                    "event_severity": event_payload["severity"],
                    "event_title": event_payload["title"],
                    "event_body": event_payload["body"],
                    "event_occurred_at": event_payload["occurred_at"],
                    "target_external_key": event_payload["target_external_key"],
                    "target_label": event_payload["target_label"],
                    "profile_external_key": event_payload["profile_external_key"],
                    "profile_display_name": event_payload["profile_display_name"],
                    "location": event_payload["location"],
                }
            )
        return payload

    def _extract_location_from_event_payload(self, raw_payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(raw_payload, dict):
            return {}
        location = raw_payload.get("location")
        if not isinstance(location, dict):
            fall_detection = raw_payload.get("fall_detection")
            if isinstance(fall_detection, dict):
                location = fall_detection.get("location")
        if not isinstance(location, dict):
            return {}
        try:
            latitude = float(location.get("latitude"))
            longitude = float(location.get("longitude"))
        except (TypeError, ValueError):
            return {}
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            return {}
        return {
            "latitude": latitude,
            "longitude": longitude,
            "label": str(location.get("label") or "").strip(),
        }

    def _session_select_sql(self) -> str:
        return """
            SELECT cs.session_id, cs.event_id, cs.recipient_id, cs.recipient_name, cs.app_user_id, cs.device_token,
                   cs.platform, cs.status, cs.channel, cs.detail, cs.join_path, cs.created_at, cs.accepted_at, cs.ended_at,
                   el.severity AS event_severity, el.title AS event_title, el.body AS event_body,
                   el.occurred_at AS event_occurred_at, el.target_external_key, el.target_label,
                   el.raw_payload_json AS event_raw_payload_json,
                   p.external_key AS profile_external_key, p.display_name AS profile_display_name
            FROM call_sessions cs
            LEFT JOIN event_logs el ON el.event_id = cs.event_id
            LEFT JOIN profiles p ON p.id = cs.profile_id
        """

    def _fetch_session_rows(self, where_sql: str, params: tuple[Any, ...], order_sql: str) -> list[sqlite3.Row]:
        query = f"""
            {self._session_select_sql()}
            {where_sql}
            {order_sql}
        """
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

    def _get_user_internal_id(self, user_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"User {user_id} not found.")
        return int(row["id"])

    def list_users(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            user_rows = conn.execute(
                """
                SELECT id, user_id, display_name, notes, current_device_token, current_platform
                FROM users
                ORDER BY created_at ASC, id ASC
                """
            ).fetchall()
            profile_rows = conn.execute(
                """
                SELECT p.id, p.external_key, p.display_name, p.notes,
                       u.user_id AS owner_public_id, u.display_name AS owner_display_name
                FROM profiles p
                LEFT JOIN users u ON u.id = p.owner_user_id
                ORDER BY p.created_at DESC, p.id DESC
                """
            ).fetchall()
            link_rows = conn.execute(
                """
                SELECT l.id, l.owner_user_id, l.relationship_label,
                       cu.user_id AS contact_user_id, cu.display_name AS contact_display_name
                FROM user_contact_links l
                JOIN users cu ON cu.id = l.contact_user_id
                ORDER BY l.created_at ASC, l.id ASC
                """
            ).fetchall()

        profiles_by_owner: dict[str, list[dict[str, Any]]] = {}
        for row in profile_rows:
            owner_public_id = row["owner_public_id"]
            if not owner_public_id:
                continue
            profiles_by_owner.setdefault(str(owner_public_id), []).append(self._profile_row_to_dict(row))

        links_by_owner: dict[int, list[dict[str, Any]]] = {}
        for row in link_rows:
            links_by_owner.setdefault(int(row["owner_user_id"]), []).append(
                {
                    "link_id": int(row["id"]),
                    "contact_user_id": row["contact_user_id"],
                    "contact_display_name": row["contact_display_name"],
                    "relationship_label": row["relationship_label"],
                }
            )

        items: list[dict[str, Any]] = []
        for row in user_rows:
            user = self._user_row_to_dict(row)
            user["owned_profiles"] = profiles_by_owner.get(user["user_id"], [])
            user["contacts"] = links_by_owner.get(user["id"], [])
            items.append(user)
        return items

    def get_user_by_public_id(self, user_id: str) -> dict[str, Any]:
        for user in self.list_users():
            if user["user_id"] == user_id:
                return user
        raise KeyError(f"User {user_id} not found.")

    def create_user(self, user_id: str, display_name: str, secret: str, notes: str = "") -> dict[str, Any]:
        now = utc_now_iso()
        salt, secret_hash = self._create_secret_payload(secret)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    user_id, display_name, notes, secret_salt, secret_hash,
                    current_device_token, current_platform, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 'web', ?, ?)
                """,
                (user_id, display_name, notes, salt, secret_hash, now, now),
            )
        return self.get_user_by_public_id(user_id)

    def verify_user_login(self, user_id: str, secret: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, display_name, notes, secret_salt, secret_hash,
                       current_device_token, current_platform
                FROM users
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"User {user_id} not found.")
        expected = self._hash_secret(secret, row["secret_salt"])
        if not hmac.compare_digest(expected, row["secret_hash"]):
            raise PermissionError("Invalid credentials.")
        user = self._user_row_to_dict(row)
        full_user = self.get_user_by_public_id(user["user_id"])
        return full_user

    def add_user_contact_link(self, owner_user_id: str, contact_user_id: str, relationship_label: str = "") -> dict[str, Any]:
        now = utc_now_iso()
        owner_internal_id = self._get_user_internal_id(owner_user_id)
        contact_internal_id = self._get_user_internal_id(contact_user_id)
        if owner_internal_id == contact_internal_id:
            raise ValueError("Owner user and contact user cannot be the same.")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_contact_links (
                    owner_user_id, contact_user_id, relationship_label, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(owner_user_id, contact_user_id) DO UPDATE SET
                    relationship_label = excluded.relationship_label,
                    updated_at = excluded.updated_at
                """,
                (owner_internal_id, contact_internal_id, relationship_label, now, now),
            )
        self._sync_linked_recipients_for_owner(owner_internal_id)
        return self.get_user_by_public_id(owner_user_id)

    def delete_user_contact_link(self, link_id: int) -> None:
        owner_internal_id: int | None = None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT owner_user_id
                FROM user_contact_links l
                WHERE l.id = ?
                """,
                (link_id,),
            ).fetchone()
            if row is None:
                return
            owner_internal_id = int(row["owner_user_id"])
            conn.execute("DELETE FROM user_contact_links WHERE id = ?", (link_id,))
        if owner_internal_id is not None:
            self._sync_linked_recipients_for_owner(owner_internal_id)

    def _sync_linked_recipients_for_owner(self, owner_internal_id: int) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            profile_rows = conn.execute(
                """
                SELECT id
                FROM profiles
                WHERE owner_user_id = ?
                ORDER BY id ASC
                """,
                (owner_internal_id,),
            ).fetchall()
            contact_rows = conn.execute(
                """
                SELECT cu.user_id, cu.display_name, cu.current_device_token, cu.current_platform
                FROM user_contact_links l
                JOIN users cu ON cu.id = l.contact_user_id
                WHERE l.owner_user_id = ?
                ORDER BY l.created_at ASC, l.id ASC
                """,
                (owner_internal_id,),
            ).fetchall()

            linked_user_ids = [str(row["user_id"]) for row in contact_rows]
            for profile_row in profile_rows:
                profile_id = int(profile_row["id"])
                existing_rows = conn.execute(
                    """
                    SELECT id, app_user_id
                    FROM app_recipients
                    WHERE profile_id = ? AND source_type = 'linked'
                    ORDER BY id ASC
                    """,
                    (profile_id,),
                ).fetchall()

                existing_ids_by_app_user: dict[str, list[int]] = {}
                for existing_row in existing_rows:
                    existing_ids_by_app_user.setdefault(str(existing_row["app_user_id"]), []).append(int(existing_row["id"]))

                for index, contact in enumerate(contact_rows, start=1):
                    placeholder_token = f"pending-registration-{contact['user_id']}"
                    existing_ids = existing_ids_by_app_user.get(str(contact["user_id"]), [])
                    if existing_ids:
                        keep_id = existing_ids[0]
                        if len(existing_ids) > 1:
                            extra_ids = existing_ids[1:]
                            placeholders = ",".join("?" for _ in extra_ids)
                            conn.execute(
                                f"DELETE FROM app_recipients WHERE id IN ({placeholders})",
                                extra_ids,
                            )
                        conn.execute(
                            """
                            UPDATE app_recipients
                            SET recipient_name = ?, device_token = ?, platform = ?, severity_scope = 'all',
                                priority = ?, updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                contact["display_name"],
                                str(contact["current_device_token"] or "").strip() or placeholder_token,
                                str(contact["current_platform"] or "web"),
                                index,
                                now,
                                keep_id,
                            ),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO app_recipients (
                                profile_id, recipient_name, app_user_id, device_token, platform,
                                severity_scope, priority, created_at, updated_at, source_type
                            )
                            VALUES (?, ?, ?, ?, ?, 'all', ?, ?, ?, 'linked')
                            """,
                            (
                                profile_id,
                                contact["display_name"],
                                contact["user_id"],
                                str(contact["current_device_token"] or "").strip() or placeholder_token,
                                str(contact["current_platform"] or "web"),
                                index,
                                now,
                                now,
                            ),
                        )

                if linked_user_ids:
                    placeholders = ",".join("?" for _ in linked_user_ids)
                    conn.execute(
                        f"""
                        DELETE FROM app_recipients
                        WHERE profile_id = ?
                          AND source_type = 'linked'
                          AND app_user_id NOT IN ({placeholders})
                        """,
                        [profile_id, *linked_user_ids],
                    )
                else:
                    conn.execute(
                        """
                        DELETE FROM app_recipients
                        WHERE profile_id = ? AND source_type = 'linked'
                        """,
                        (profile_id,),
                    )

    def _sync_linked_recipients_for_contact(self, contact_internal_id: int) -> None:
        with self._connect() as conn:
            owner_rows = conn.execute(
                """
                SELECT DISTINCT owner_user_id
                FROM user_contact_links
                WHERE contact_user_id = ?
                ORDER BY owner_user_id ASC
                """,
                (contact_internal_id,),
            ).fetchall()
        for row in owner_rows:
            self._sync_linked_recipients_for_owner(int(row["owner_user_id"]))

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            profile_rows = conn.execute(
                """
                SELECT p.id, p.external_key, p.display_name, p.notes,
                       u.user_id AS owner_public_id, u.display_name AS owner_display_name
                FROM profiles p
                LEFT JOIN users u ON u.id = p.owner_user_id
                ORDER BY p.created_at DESC
                """
            ).fetchall()
            recipient_rows = conn.execute(
                """
                SELECT id, profile_id, recipient_name, app_user_id, device_token, platform, severity_scope, priority, source_type
                FROM app_recipients
                ORDER BY priority ASC, id ASC
                """
            ).fetchall()

        recipients_by_profile: dict[int, list[dict[str, Any]]] = {}
        for row in recipient_rows:
            recipient = self._recipient_row_to_dict(row)
            recipients_by_profile.setdefault(recipient["profile_id"], []).append(recipient)

        profiles: list[dict[str, Any]] = []
        for row in profile_rows:
            profile = self._profile_row_to_dict(row)
            profile["app_recipients"] = recipients_by_profile.get(profile["id"], [])
            profiles.append(profile)
        return profiles

    def get_profile(self, profile_id: int) -> dict[str, Any]:
        for profile in self.list_profiles():
            if profile["id"] == profile_id:
                return profile
        raise KeyError(f"Profile {profile_id} not found.")

    def create_profile(
        self,
        external_key: str,
        display_name: str,
        notes: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        owner_internal_id = self._get_user_internal_id(owner_user_id) if owner_user_id else None
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO profiles (external_key, display_name, notes, owner_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (external_key, display_name, notes, owner_internal_id, now, now),
            )
            profile_id = int(cursor.lastrowid)
        if owner_internal_id is not None:
            self._sync_linked_recipients_for_owner(owner_internal_id)
        return self.get_profile(profile_id)

    def update_profile(
        self,
        profile_id: int,
        external_key: str,
        display_name: str,
        notes: str,
        owner_user_id: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        owner_internal_id = self._get_user_internal_id(owner_user_id) if owner_user_id else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE profiles
                SET external_key = ?, display_name = ?, notes = ?, owner_user_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (external_key, display_name, notes, owner_internal_id, now, profile_id),
            )
            conn.execute(
                """
                DELETE FROM app_recipients
                WHERE profile_id = ? AND source_type = 'linked'
                """,
                (profile_id,),
            )
        if owner_internal_id is not None:
            self._sync_linked_recipients_for_owner(owner_internal_id)
        return self.get_profile(profile_id)

    def delete_profile(self, profile_id: int) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))

    def get_app_recipient(self, recipient_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, profile_id, recipient_name, app_user_id, device_token, platform, severity_scope, priority, source_type
                FROM app_recipients
                WHERE id = ?
                """,
                (recipient_id,),
            ).fetchone()
        if row is None:
            raise KeyError(f"App recipient {recipient_id} not found.")
        return self._recipient_row_to_dict(row)

    def add_app_recipient(
        self,
        profile_id: int,
        recipient_name: str,
        app_user_id: str,
        device_token: str,
        platform: str,
        severity_scope: str,
        priority: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO app_recipients (
                    profile_id, recipient_name, app_user_id, device_token, platform,
                    severity_scope, priority, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (profile_id, recipient_name, app_user_id, device_token, platform, severity_scope, priority, now, now),
            )
            recipient_id = int(cursor.lastrowid)
        return self.get_app_recipient(recipient_id)

    def update_app_recipient(
        self,
        recipient_id: int,
        profile_id: int,
        recipient_name: str,
        app_user_id: str,
        device_token: str,
        platform: str,
        severity_scope: str,
        priority: int,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT source_type
                FROM app_recipients
                WHERE id = ?
                """,
                (recipient_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"App recipient {recipient_id} not found.")
            if str(row["source_type"]) == "linked":
                raise PermissionError("Linked recipients must be managed from user contact links.")
            conn.execute(
                """
                UPDATE app_recipients
                SET profile_id = ?, recipient_name = ?, app_user_id = ?, device_token = ?,
                    platform = ?, severity_scope = ?, priority = ?, updated_at = ?
                WHERE id = ?
                """,
                (profile_id, recipient_name, app_user_id, device_token, platform, severity_scope, priority, now, recipient_id),
            )
        return self.get_app_recipient(recipient_id)

    def delete_app_recipient(self, recipient_id: int) -> None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT source_type
                FROM app_recipients
                WHERE id = ?
                """,
                (recipient_id,),
            ).fetchone()
            if row is None:
                return
            if str(row["source_type"]) == "linked":
                raise PermissionError("Linked recipients must be removed from user contact links.")
            conn.execute("DELETE FROM app_recipients WHERE id = ?", (recipient_id,))

    def _upsert_mini_program_device_record(
        self,
        conn: sqlite3.Connection,
        app_user_id: str,
        recipient_name: str,
        device_token: str,
        platform: str,
        external_key: str | None = None,
    ) -> None:
        now = utc_now_iso()
        existing = conn.execute(
            """
            SELECT external_key
            FROM mini_program_devices
            WHERE app_user_id = ?
            """,
            (app_user_id,),
        ).fetchone()
        resolved_external_key = external_key if external_key else (str(existing["external_key"]) if existing and existing["external_key"] else None)

        if existing is None:
            conn.execute(
                """
                INSERT INTO mini_program_devices (
                    app_user_id, recipient_name, device_token, platform, external_key, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (app_user_id, recipient_name, device_token, platform, resolved_external_key, now, now),
            )
            return

        conn.execute(
            """
            UPDATE mini_program_devices
            SET recipient_name = ?, device_token = ?, platform = ?, external_key = ?, updated_at = ?
            WHERE app_user_id = ?
            """,
            (recipient_name, device_token, platform, resolved_external_key, now, app_user_id),
        )

    def register_device_for_user(
        self,
        app_user_id: str,
        recipient_name: str,
        device_token: str,
        platform: str,
        external_key: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        linked_contact_internal_id: int | None = None
        with self._connect() as conn:
            user_row = conn.execute(
                """
                SELECT id
                FROM users
                WHERE user_id = ?
                """,
                (app_user_id,),
            ).fetchone()
            rows = conn.execute(
                """
                SELECT id, profile_id, source_type
                FROM app_recipients
                WHERE app_user_id = ?
                ORDER BY id ASC
                """,
                (app_user_id,),
            ).fetchall()

            profile_row = None
            if external_key:
                profile_row = conn.execute(
                    """
                    SELECT id
                    FROM profiles
                    WHERE external_key = ?
                    """,
                    (external_key,),
                ).fetchone()
                if profile_row is None:
                    raise KeyError(f"Profile external_key {external_key} not found.")

            if not rows:
                if user_row is not None:
                    linked_contact_internal_id = int(user_row["id"])
                    if profile_row is not None:
                        next_priority = int(
                            conn.execute(
                                """
                                SELECT COALESCE(MAX(priority), 0) + 1
                                FROM app_recipients
                                WHERE profile_id = ?
                                """,
                                (int(profile_row["id"]),),
                            ).fetchone()[0]
                        )
                        conn.execute(
                            """
                            INSERT INTO app_recipients (
                                profile_id, recipient_name, app_user_id, device_token, platform,
                                severity_scope, priority, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, 'all', ?, ?, ?)
                            """,
                            (
                                int(profile_row["id"]),
                                recipient_name,
                                app_user_id,
                                device_token,
                                platform,
                                next_priority,
                                now,
                                now,
                            ),
                        )
                elif not external_key:
                    raise KeyError(
                        f"App user {app_user_id} not found. Provide external_key to create it on first registration."
                    )
                else:
                    next_priority = int(
                        conn.execute(
                            """
                            SELECT COALESCE(MAX(priority), 0) + 1
                            FROM app_recipients
                            WHERE profile_id = ?
                            """,
                            (int(profile_row["id"]),),
                        ).fetchone()[0]
                    )
                    conn.execute(
                        """
                        INSERT INTO app_recipients (
                            profile_id, recipient_name, app_user_id, device_token, platform,
                            severity_scope, priority, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'all', ?, ?, ?)
                        """,
                        (
                            int(profile_row["id"]),
                            recipient_name,
                            app_user_id,
                            device_token,
                            platform,
                            next_priority,
                            now,
                            now,
                        ),
                    )
            else:
                conn.execute(
                    """
                    UPDATE app_recipients
                    SET recipient_name = ?, device_token = ?, platform = ?, updated_at = ?
                    WHERE app_user_id = ?
                    """,
                    (recipient_name, device_token, platform, now, app_user_id),
                )
                if profile_row is not None and not any(int(row["profile_id"]) == int(profile_row["id"]) for row in rows):
                    next_priority = int(
                        conn.execute(
                            """
                            SELECT COALESCE(MAX(priority), 0) + 1
                            FROM app_recipients
                            WHERE profile_id = ?
                            """,
                            (int(profile_row["id"]),),
                        ).fetchone()[0]
                    )
                    conn.execute(
                        """
                        INSERT INTO app_recipients (
                            profile_id, recipient_name, app_user_id, device_token, platform,
                            severity_scope, priority, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'all', ?, ?, ?)
                        """,
                        (
                            int(profile_row["id"]),
                            recipient_name,
                            app_user_id,
                            device_token,
                            platform,
                            next_priority,
                            now,
                            now,
                        ),
                    )

            if user_row is not None:
                linked_contact_internal_id = int(user_row["id"])
                conn.execute(
                    """
                    UPDATE users
                    SET current_device_token = ?, current_platform = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (device_token, platform, now, linked_contact_internal_id),
                )

            if platform == "wechat_miniprogram":
                self._upsert_mini_program_device_record(
                    conn=conn,
                    app_user_id=app_user_id,
                    recipient_name=recipient_name,
                    device_token=device_token,
                    platform=platform,
                    external_key=external_key,
                )

        if linked_contact_internal_id is not None:
            self._sync_linked_recipients_for_contact(linked_contact_internal_id)

        recipients = self.resolve_recipients_by_app_user(app_user_id)
        mini_program_device = self.get_mini_program_device(app_user_id) if platform == "wechat_miniprogram" else None
        return {
            "app_user_id": app_user_id,
            "device_token": device_token,
            "platform": platform,
            "recipient_name": recipient_name,
            "linked_profiles": len(recipients),
            "recipients": recipients,
            "mini_program_device": mini_program_device,
        }

    def get_mini_program_device(self, app_user_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM mini_program_devices
                WHERE app_user_id = ?
                """,
                (app_user_id,),
            ).fetchone()
        if row is None:
            return None
        return self._mini_program_device_row_to_dict(row)

    def bind_mini_program_openid(
        self,
        app_user_id: str,
        recipient_name: str,
        device_token: str,
        platform: str,
        openid: str,
        unionid: str = "",
        external_key: str | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            self._upsert_mini_program_device_record(
                conn=conn,
                app_user_id=app_user_id,
                recipient_name=recipient_name,
                device_token=device_token,
                platform=platform,
                external_key=external_key,
            )
            conn.execute(
                """
                UPDATE mini_program_devices
                SET wechat_openid = ?, wechat_unionid = ?, last_login_at = ?, updated_at = ?
                WHERE app_user_id = ?
                """,
                (openid, unionid, now, now, app_user_id),
            )
        device = self.get_mini_program_device(app_user_id)
        if device is None:
            raise KeyError(f"Mini program device {app_user_id} not found.")
        return device

    def save_mini_program_subscription(
        self,
        app_user_id: str,
        recipient_name: str,
        device_token: str,
        platform: str,
        permission_result: dict[str, str],
        external_key: str | None = None,
        active_template_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        normalized_active_template_ids = [
            str(template_id).strip()
            for template_id in (active_template_ids or [])
            if str(template_id).strip()
        ]
        active_template_id_set = set(normalized_active_template_ids)
        with self._connect() as conn:
            self._upsert_mini_program_device_record(
                conn=conn,
                app_user_id=app_user_id,
                recipient_name=recipient_name,
                device_token=device_token,
                platform=platform,
                external_key=external_key,
            )
            row = conn.execute(
                """
                SELECT granted_template_ids_json
                FROM mini_program_devices
                WHERE app_user_id = ?
                """,
                (app_user_id,),
            ).fetchone()
            existing_granted = json.loads(row["granted_template_ids_json"] or "[]") if row else []
            if active_template_id_set:
                existing_granted = [
                    str(template_id)
                    for template_id in existing_granted
                    if str(template_id) in active_template_id_set
                ]
            accepted_template_ids = [
                str(template_id)
                for template_id, state in permission_result.items()
                if str(state).lower().startswith("accept")
                and (not active_template_id_set or str(template_id) in active_template_id_set)
            ]
            granted_template_ids = [*existing_granted, *accepted_template_ids]
            notification_enabled = 1 if granted_template_ids else 0
            conn.execute(
                """
                UPDATE mini_program_devices
                SET notification_enabled = ?, granted_template_ids_json = ?, last_permission_result_json = ?,
                    subscription_updated_at = ?, updated_at = ?
                WHERE app_user_id = ?
                """,
                (
                    notification_enabled,
                    json.dumps(granted_template_ids, ensure_ascii=False),
                    json.dumps(permission_result, ensure_ascii=False),
                    now,
                    now,
                    app_user_id,
                ),
            )
        device = self.get_mini_program_device(app_user_id)
        if device is None:
            raise KeyError(f"Mini program device {app_user_id} not found.")
        return device

    def list_notifiable_mini_program_devices(self, app_user_ids: list[str]) -> list[dict[str, Any]]:
        if not app_user_ids:
            return []
        placeholders = ",".join("?" for _ in app_user_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM mini_program_devices
                WHERE app_user_id IN ({placeholders})
                  AND COALESCE(wechat_openid, '') != ''
                ORDER BY id ASC
                """,
                app_user_ids,
            ).fetchall()
        return [
            device
            for row in rows
            if (device := self._mini_program_device_row_to_dict(row)).get("granted_template_ids")
        ]

    def mark_mini_program_notification_result(
        self,
        app_user_id: str,
        template_id: str | None,
        success: bool,
        error: str = "",
    ) -> None:
        now = utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT granted_template_ids_json
                FROM mini_program_devices
                WHERE app_user_id = ?
                """,
                (app_user_id,),
            ).fetchone()
            if row is None:
                return
            granted_template_ids = json.loads(row["granted_template_ids_json"] or "[]")
            if success and template_id:
                remaining_template_ids: list[Any] = []
                consumed = False
                for item in granted_template_ids:
                    if not consumed and str(item) == str(template_id):
                        consumed = True
                        continue
                    remaining_template_ids.append(item)
                granted_template_ids = remaining_template_ids
            notification_enabled = 1 if granted_template_ids else 0
            conn.execute(
                """
                UPDATE mini_program_devices
                SET notification_enabled = ?, granted_template_ids_json = ?, last_notification_at = ?,
                    last_notification_status = ?, last_notification_error = ?, updated_at = ?
                WHERE app_user_id = ?
                """,
                (
                    notification_enabled,
                    json.dumps(granted_template_ids, ensure_ascii=False),
                    now,
                    "sent" if success else "failed",
                    error,
                    now,
                    app_user_id,
                ),
            )

    def resolve_recipients_by_app_user(self, app_user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.id, ar.profile_id, ar.recipient_name, ar.app_user_id, ar.device_token,
                       ar.platform, ar.severity_scope, ar.priority, ar.source_type,
                       p.display_name, p.external_key
                FROM app_recipients ar
                JOIN profiles p ON p.id = ar.profile_id
                WHERE ar.app_user_id = ?
                ORDER BY ar.priority ASC, ar.id ASC
                """,
                (app_user_id,),
            ).fetchall()
        return [
            {
                **self._recipient_row_to_dict(row),
                "profile_display_name": row["display_name"],
                "profile_external_key": row["external_key"],
            }
            for row in rows
        ]

    def resolve_app_recipients(self, external_key: str | None, severity: str) -> list[dict[str, Any]]:
        if not external_key:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ar.id, ar.profile_id, ar.recipient_name, ar.app_user_id, ar.device_token,
                       ar.platform, ar.severity_scope, ar.priority, ar.source_type
                FROM app_recipients ar
                JOIN profiles p ON p.id = ar.profile_id
                WHERE p.external_key = ?
                  AND (ar.severity_scope = 'all' OR ar.severity_scope = ?)
                ORDER BY ar.priority ASC, ar.id ASC
                """,
                (external_key, severity.lower()),
            ).fetchall()
        recipients = [self._recipient_row_to_dict(row) for row in rows]
        linked_recipients = [item for item in recipients if item["source_type"] == "linked"]
        return linked_recipients if linked_recipients else recipients

    def save_dispatch_result(self, result: dict[str, Any]) -> None:
        event = result["event"]
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO event_logs (
                    event_id, source, severity, title, body, occurred_at,
                    target_external_key, target_label, provider, message, raw_payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["event_id"],
                    event["source"],
                    event["severity"],
                    event["title"],
                    event["body"],
                    event["occurred_at"],
                    event.get("target_external_key"),
                    event.get("target_label"),
                    result["provider"],
                    result["message"],
                    json.dumps(event.get("raw_payload", {}), ensure_ascii=False),
                    utc_now_iso(),
                ),
            )
            for session in result["sessions"]:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO call_sessions (
                        session_id, event_id, profile_id, recipient_id, recipient_name, app_user_id,
                        device_token, platform, status, channel, detail, join_path,
                        created_at, updated_at, accepted_at, ended_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session["session_id"],
                        event["event_id"],
                        self._lookup_profile_id(event.get("target_external_key")),
                        session["recipient_id"],
                        session["recipient_name"],
                        session["app_user_id"],
                        session["device_token"],
                        session["platform"],
                        session["status"],
                        session["channel"],
                        session["detail"],
                        session["join_path"],
                        session["created_at"],
                        session["created_at"],
                        None,
                        None,
                    ),
                )

    def _lookup_profile_id(self, external_key: str | None) -> int | None:
        if not external_key:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM profiles WHERE external_key = ?", (external_key,)).fetchone()
        return int(row["id"]) if row else None

    def list_recent_event_results(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            event_rows = conn.execute(
                """
                SELECT event_id, source, severity, title, body, occurred_at,
                       target_external_key, target_label, provider, message, raw_payload_json
                FROM event_logs
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            session_rows = conn.execute(
                """
                SELECT session_id, event_id, recipient_id, recipient_name, app_user_id, device_token,
                       platform, status, channel, detail, join_path, created_at, accepted_at, ended_at
                FROM call_sessions
                ORDER BY created_at DESC
                """
            ).fetchall()

        sessions_by_event: dict[str, list[dict[str, Any]]] = {}
        for row in session_rows:
            session = self._session_row_to_dict(row)
            sessions_by_event.setdefault(str(row["event_id"]), []).append(session)

        results: list[dict[str, Any]] = []
        for row in event_rows:
            event_id = str(row["event_id"])
            raw_payload = json.loads(row["raw_payload_json"])
            results.append(
                {
                    "event": {
                        "event_id": event_id,
                        "source": row["source"],
                        "severity": row["severity"],
                        "title": row["title"],
                        "body": row["body"],
                        "occurred_at": row["occurred_at"],
                        "target_external_key": row["target_external_key"],
                        "target_label": row["target_label"],
                        "location": self._extract_location_from_event_payload(raw_payload),
                        "raw_payload": raw_payload,
                    },
                    "provider": row["provider"],
                    "message": row["message"],
                    "sessions": sessions_by_event.get(event_id, []),
                }
            )
        return results

    def load_processed_ids(self) -> set[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT event_id FROM event_logs").fetchall()
        return {str(row["event_id"]) for row in rows}

    def list_pending_sessions_for_user(self, app_user_id: str) -> list[dict[str, Any]]:
        rows = self._fetch_session_rows(
            "WHERE cs.app_user_id = ? AND cs.status IN ('pending', 'ringing')",
            (app_user_id,),
            "ORDER BY cs.created_at DESC",
        )
        return [self._session_row_to_dict(row) for row in rows]

    def list_sessions_for_user(self, app_user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._fetch_session_rows(
            "WHERE cs.app_user_id = ?",
            (app_user_id, limit),
            "ORDER BY cs.created_at DESC LIMIT ?",
        )
        return [self._session_row_to_dict(row) for row in rows]

    def list_active_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._fetch_session_rows(
            "WHERE cs.status IN ('pending', 'ringing', 'accepted')",
            (limit,),
            "ORDER BY cs.created_at DESC LIMIT ?",
        )
        return [self._session_row_to_dict(row) for row in rows]

    def get_session(self, session_id: str) -> dict[str, Any]:
        rows = self._fetch_session_rows(
            "WHERE cs.session_id = ?",
            (session_id,),
            "",
        )
        row = rows[0] if rows else None
        if row is None:
            raise KeyError(f"Session {session_id} not found.")
        session = self._session_row_to_dict(row)
        session["participants"] = self.list_session_participants(session_id)
        return session

    def update_session_status(self, session_id: str, status: str) -> dict[str, Any]:
        now = utc_now_iso()
        accepted_at = now if status == "accepted" else None
        ended_at = now if status in {"ended", "missed", "rejected"} else None
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE call_sessions
                SET status = ?, updated_at = ?,
                    accepted_at = COALESCE(accepted_at, ?),
                    ended_at = COALESCE(ended_at, ?)
                WHERE session_id = ?
                """,
                (status, now, accepted_at, ended_at, session_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Session {session_id} not found.")

        return self.get_session(session_id)

    def join_session(self, session_id: str, participant_id: str, role: str, label: str) -> dict[str, Any]:
        self.get_session(session_id)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO call_session_participants (
                    session_id, participant_id, role, label, joined_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, participant_id) DO UPDATE SET
                    role = excluded.role,
                    label = excluded.label,
                    last_seen_at = excluded.last_seen_at
                """,
                (session_id, participant_id, role, label, now, now),
            )
            conn.execute(
                """
                UPDATE call_sessions
                SET status = CASE
                    WHEN status = 'pending' THEN 'ringing'
                    ELSE status
                END,
                    updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
        return {
            "participant_id": participant_id,
            "role": role,
            "label": label,
            "session": self.get_session(session_id),
        }

    def list_session_participants(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT participant_id, role, label, joined_at, last_seen_at
                FROM call_session_participants
                WHERE session_id = ?
                ORDER BY joined_at ASC
                """,
                (session_id,),
            ).fetchall()
        return [self._participant_row_to_dict(row) for row in rows]

    def leave_session(self, session_id: str, participant_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self._connect() as conn:
            session_row = conn.execute(
                """
                SELECT status
                FROM call_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise KeyError(f"Session {session_id} not found.")

            cursor = conn.execute(
                """
                DELETE FROM call_session_participants
                WHERE session_id = ? AND participant_id = ?
                """,
                (session_id, participant_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Participant {participant_id} not found in session {session_id}.")

            remaining_count = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM call_session_participants
                    WHERE session_id = ?
                    """,
                    (session_id,),
                ).fetchone()[0]
            )

            next_status = None
            ended_at = None
            if remaining_count == 0:
                next_status = "ended"
                ended_at = now
            elif session_row["status"] == "accepted":
                next_status = "ringing"

            conn.execute(
                """
                UPDATE call_sessions
                SET status = COALESCE(?, status),
                    updated_at = ?,
                    ended_at = CASE
                        WHEN ? IS NULL THEN ended_at
                        ELSE COALESCE(ended_at, ?)
                    END
                WHERE session_id = ?
                """,
                (next_status, now, ended_at, ended_at, session_id),
            )

        return {
            "session": self.get_session(session_id),
            "removed_participant_id": participant_id,
            "remaining_participants": remaining_count,
        }

    def save_signal(
        self,
        session_id: str,
        sender_participant_id: str,
        sender_role: str,
        signal_type: str,
        payload: dict[str, Any],
        target_participant_id: str | None = None,
    ) -> dict[str, Any]:
        self.get_session(session_id)
        now = utc_now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO call_session_signals (
                    session_id, sender_participant_id, sender_role,
                    target_participant_id, signal_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sender_participant_id,
                    sender_role,
                    target_participant_id,
                    signal_type,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE call_session_participants
                SET last_seen_at = ?
                WHERE session_id = ? AND participant_id = ?
                """,
                (now, session_id, sender_participant_id),
            )
        return {
            "id": int(cursor.lastrowid),
            "session_id": session_id,
            "sender_participant_id": sender_participant_id,
            "sender_role": sender_role,
            "target_participant_id": target_participant_id,
            "signal_type": signal_type,
            "payload": payload,
            "created_at": now,
        }

    def list_signals(self, session_id: str, participant_id: str, since_id: int = 0) -> list[dict[str, Any]]:
        self.get_session(session_id)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE call_session_participants
                SET last_seen_at = ?
                WHERE session_id = ? AND participant_id = ?
                """,
                (now, session_id, participant_id),
            )
            rows = conn.execute(
                """
                SELECT id, sender_participant_id, sender_role, target_participant_id,
                       signal_type, payload_json, created_at
                FROM call_session_signals
                WHERE session_id = ?
                  AND id > ?
                  AND sender_participant_id != ?
                  AND (target_participant_id IS NULL OR target_participant_id = ?)
                ORDER BY id ASC
                """,
                (session_id, since_id, participant_id, participant_id),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "sender_participant_id": row["sender_participant_id"],
                "sender_role": row["sender_role"],
                "target_participant_id": row["target_participant_id"],
                "signal_type": row["signal_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def clear_all_session_data(self) -> dict[str, int]:
        with self._connect() as conn:
            session_count = int(conn.execute("SELECT COUNT(*) FROM call_sessions").fetchone()[0])
            event_count = int(conn.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0])
            participant_count = int(conn.execute("SELECT COUNT(*) FROM call_session_participants").fetchone()[0])
            signal_count = int(conn.execute("SELECT COUNT(*) FROM call_session_signals").fetchone()[0])

            conn.execute("DELETE FROM call_session_signals")
            conn.execute("DELETE FROM call_session_participants")
            conn.execute("DELETE FROM call_sessions")
            conn.execute("DELETE FROM event_logs")

        return {
            "removed_sessions": session_count,
            "removed_events": event_count,
            "removed_participants": participant_count,
            "removed_signals": signal_count,
            "kept_event_id": 0,
        }

    def keep_latest_dispatch_only(self) -> dict[str, int | str | None]:
        with self._connect() as conn:
            latest_event = conn.execute(
                """
                SELECT event_id
                FROM event_logs
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if latest_event is None:
                return {
                    "removed_sessions": 0,
                    "removed_events": 0,
                    "removed_participants": 0,
                    "removed_signals": 0,
                    "kept_event_id": None,
                }

            kept_event_id = str(latest_event["event_id"])
            removed_event_ids = [
                str(row["event_id"])
                for row in conn.execute(
                    """
                    SELECT event_id
                    FROM event_logs
                    WHERE event_id != ?
                    """,
                    (kept_event_id,),
                ).fetchall()
            ]

            if not removed_event_ids:
                return {
                    "removed_sessions": 0,
                    "removed_events": 0,
                    "removed_participants": 0,
                    "removed_signals": 0,
                    "kept_event_id": kept_event_id,
                }

            placeholders = ",".join("?" for _ in removed_event_ids)
            removed_session_ids = [
                str(row["session_id"])
                for row in conn.execute(
                    f"""
                    SELECT session_id
                    FROM call_sessions
                    WHERE event_id IN ({placeholders})
                    """,
                    removed_event_ids,
                ).fetchall()
            ]

            removed_signals = 0
            removed_participants = 0
            if removed_session_ids:
                session_placeholders = ",".join("?" for _ in removed_session_ids)
                removed_signals = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM call_session_signals
                        WHERE session_id IN ({session_placeholders})
                        """,
                        removed_session_ids,
                    ).fetchone()[0]
                )
                removed_participants = int(
                    conn.execute(
                        f"""
                        SELECT COUNT(*)
                        FROM call_session_participants
                        WHERE session_id IN ({session_placeholders})
                        """,
                        removed_session_ids,
                    ).fetchone()[0]
                )

                conn.execute(
                    f"DELETE FROM call_session_signals WHERE session_id IN ({session_placeholders})",
                    removed_session_ids,
                )
                conn.execute(
                    f"DELETE FROM call_session_participants WHERE session_id IN ({session_placeholders})",
                    removed_session_ids,
                )
                conn.execute(
                    f"DELETE FROM call_sessions WHERE session_id IN ({session_placeholders})",
                    removed_session_ids,
                )

            conn.execute(
                f"DELETE FROM event_logs WHERE event_id IN ({placeholders})",
                removed_event_ids,
            )

        return {
            "removed_sessions": len(removed_session_ids),
            "removed_events": len(removed_event_ids),
            "removed_participants": removed_participants,
            "removed_signals": removed_signals,
            "kept_event_id": kept_event_id,
        }
