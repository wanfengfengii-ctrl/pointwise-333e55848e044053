import sqlite3

from storage import initialize_schema, write_transaction


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS accepted_pieces (
    receipt_id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    issue_id TEXT NOT NULL,
    envelope TEXT NOT NULL,
    canonical_envelope TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'v1',
    scaled_canonical TEXT,
    UNIQUE (batch_id, station_id, issue_id)
);
'''


class ReceiptRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        initialize_schema(self.connection, _SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(accepted_pieces)')}
        additions = []
        if 'kind' not in columns:
            additions.append("ALTER TABLE accepted_pieces ADD COLUMN kind TEXT NOT NULL DEFAULT 'v1'")
        if 'scaled_canonical' not in columns:
            additions.append('ALTER TABLE accepted_pieces ADD COLUMN scaled_canonical TEXT')
        if additions:
            with write_transaction(self.connection):
                for statement in additions:
                    self.connection.execute(statement)

    def accept_piece(
        self, batch_id: str, station_id: str, issue_id: str, envelope: str, canonical: str
    ) -> int:
        with write_transaction(self.connection):
            existing = self.connection.execute(
                '''SELECT receipt_id, canonical_envelope, kind FROM accepted_pieces
                   WHERE batch_id = ? AND station_id = ? AND issue_id = ?''',
                (batch_id, station_id, issue_id),
            ).fetchone()
            if existing is not None:
                receipt_id, existing_content, kind = existing
                if kind != 'v1' or existing_content != canonical:
                    raise ValueError(f'conflicting content for issue {issue_id!r}')
                return receipt_id
            cursor = self.connection.execute(
                '''INSERT INTO accepted_pieces
                   (batch_id, station_id, issue_id, envelope, canonical_envelope, kind)
                   VALUES (?, ?, ?, ?, ?, 'v1')''',
                (batch_id, station_id, issue_id, envelope, canonical),
            )
        return cursor.lastrowid

    def accept_mode_piece(
        self,
        batch_id: str,
        station_id: str,
        issue_id: str,
        envelope: str,
        canonical: str,
        scaled_canonical: str,
    ) -> int:
        with write_transaction(self.connection):
            existing = self.connection.execute(
                '''SELECT receipt_id, kind, scaled_canonical FROM accepted_pieces
                   WHERE batch_id = ? AND station_id = ? AND issue_id = ?''',
                (batch_id, station_id, issue_id),
            ).fetchone()
            if existing is not None:
                receipt_id, kind, stored_scaled = existing
                if kind != 'mode' or stored_scaled != scaled_canonical:
                    raise ValueError(f'conflicting content for issue {issue_id!r}')
                return receipt_id
            cursor = self.connection.execute(
                '''INSERT INTO accepted_pieces
                   (batch_id, station_id, issue_id, envelope, canonical_envelope, kind, scaled_canonical)
                   VALUES (?, ?, ?, ?, ?, 'mode', ?)''',
                (batch_id, station_id, issue_id, envelope, canonical, scaled_canonical),
            )
        return cursor.lastrowid

    def read_piece(self, receipt_id: int) -> str | None:
        return self.read_v1_piece(receipt_id)

    def read_v1_piece(self, receipt_id: int) -> str | None:
        row = self._read_row(receipt_id)
        if row is None:
            return None
        envelope, kind = row
        if kind != 'v1':
            raise ValueError(f'receipt {receipt_id} is not a V1 piece')
        return envelope

    def read_mode_piece(self, receipt_id: int) -> str | None:
        row = self._read_row(receipt_id)
        if row is None:
            return None
        envelope, kind = row
        if kind != 'mode':
            raise ValueError(f'receipt {receipt_id} is not a shared-mode piece')
        return envelope

    def _read_row(self, receipt_id: int) -> tuple[str, str] | None:
        # A positive number beyond SQLite's rowid range cannot refer to a row.
        if receipt_id > 2**63 - 1:
            return None
        row = self.connection.execute(
            'SELECT envelope, kind FROM accepted_pieces WHERE receipt_id = ?', (receipt_id,)
        ).fetchone()
        return None if row is None else (row[0], row[1])
