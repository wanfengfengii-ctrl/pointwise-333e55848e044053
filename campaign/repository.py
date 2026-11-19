from fractions import Fraction
import sqlite3

from campaign.allocation import StationAllocation
from campaign.modes import ModeManifest, ModeSpec
from storage import initialize_schema, write_transaction


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS campaign_batches (
    batch_id TEXT PRIMARY KEY NOT NULL,
    sealed_count TEXT NOT NULL,
    revealed INTEGER NOT NULL DEFAULT 0 CHECK (revealed IN (0, 1))
);
CREATE TABLE IF NOT EXISTS campaign_allocations (
    batch_id TEXT NOT NULL REFERENCES campaign_batches(batch_id),
    station_id TEXT NOT NULL,
    capacity TEXT NOT NULL,
    PRIMARY KEY (batch_id, station_id)
);
CREATE TABLE IF NOT EXISTS campaign_mode_sets (
    batch_id TEXT PRIMARY KEY NOT NULL REFERENCES campaign_batches(batch_id)
);
CREATE TABLE IF NOT EXISTS campaign_modes (
    batch_id TEXT NOT NULL REFERENCES campaign_mode_sets(batch_id),
    ordinal INTEGER NOT NULL,
    name TEXT NOT NULL,
    prior TEXT NOT NULL,
    PRIMARY KEY (batch_id, ordinal),
    UNIQUE (batch_id, name)
);
'''


class CampaignRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        initialize_schema(self.connection, _SCHEMA)

    def register_sealed(
        self, batch_id: str, allocations: tuple[StationAllocation, ...], sealed_count: int
    ) -> None:
        with write_transaction(self.connection):
            if self.connection.execute(
                'SELECT 1 FROM campaign_mode_sets WHERE batch_id = ?', (batch_id,)
            ).fetchone() is not None:
                raise ValueError(f'batch {batch_id!r} is registered as a shared-mode batch')
            existing = self.connection.execute(
                'SELECT sealed_count FROM campaign_batches WHERE batch_id = ?', (batch_id,)
            ).fetchone()
            if existing is not None:
                if int(existing[0]) != sealed_count or self.load_allocations(batch_id) != allocations:
                    raise ValueError(f'conflicting registration for batch {batch_id!r}')
                return

            self.connection.execute(
                'INSERT INTO campaign_batches (batch_id, sealed_count) VALUES (?, ?)',
                (batch_id, str(sealed_count)),
            )
            self.connection.executemany(
                'INSERT INTO campaign_allocations (batch_id, station_id, capacity) VALUES (?, ?, ?)',
                ((batch_id, row.station_id, str(row.capacity)) for row in allocations),
            )

    def register_mode_batch(
        self,
        batch_id: str,
        allocations: tuple[StationAllocation, ...],
        sealed_count: int,
        modes: tuple[ModeSpec, ...],
    ) -> ModeManifest:
        with write_transaction(self.connection):
            existing = self.connection.execute(
                'SELECT sealed_count FROM campaign_batches WHERE batch_id = ?', (batch_id,)
            ).fetchone()
            if existing is not None:
                stored_modes = self.load_modes(batch_id)
                if (
                    stored_modes is None
                    or int(existing[0]) != sealed_count
                    or self.load_allocations(batch_id) != allocations
                    or stored_modes != modes
                ):
                    raise ValueError(f'conflicting registration for batch {batch_id!r}')
                return ModeManifest(batch_id, allocations, modes)

            self.connection.execute(
                'INSERT INTO campaign_batches (batch_id, sealed_count) VALUES (?, ?)',
                (batch_id, str(sealed_count)),
            )
            self.connection.executemany(
                'INSERT INTO campaign_allocations (batch_id, station_id, capacity) VALUES (?, ?, ?)',
                ((batch_id, row.station_id, str(row.capacity)) for row in allocations),
            )
            self.connection.execute(
                'INSERT INTO campaign_mode_sets (batch_id) VALUES (?)', (batch_id,)
            )
            self.connection.executemany(
                'INSERT INTO campaign_modes (batch_id, ordinal, name, prior) VALUES (?, ?, ?, ?)',
                (
                    (batch_id, index, mode.name, str(mode.prior))
                    for index, mode in enumerate(modes)
                ),
            )
        return ModeManifest(batch_id, allocations, modes)

    def load_modes(self, batch_id: str) -> tuple[ModeSpec, ...] | None:
        if self.connection.execute(
            'SELECT 1 FROM campaign_mode_sets WHERE batch_id = ?', (batch_id,)
        ).fetchone() is None:
            return None
        rows = self.connection.execute(
            'SELECT name, prior FROM campaign_modes WHERE batch_id = ? ORDER BY ordinal',
            (batch_id,),
        ).fetchall()
        return tuple(ModeSpec(name, Fraction(prior)) for name, prior in rows)

    def load_mode_manifest(self, batch_id: str) -> ModeManifest | None:
        modes = self.load_modes(batch_id)
        if modes is None:
            return None
        allocations = self.load_allocations(batch_id)
        if allocations is None:
            return None
        return ModeManifest(batch_id, allocations, modes)

    def load_allocations(self, batch_id: str) -> tuple[StationAllocation, ...] | None:
        rows = self.connection.execute(
            '''SELECT station_id, capacity FROM campaign_allocations
               WHERE batch_id = ? ORDER BY station_id''',
            (batch_id,),
        ).fetchall()
        if not rows:
            return None
        return tuple(StationAllocation(station_id, int(capacity)) for station_id, capacity in rows)

    def reveal_count(self, batch_id: str) -> int:
        with write_transaction(self.connection):
            row = self.connection.execute(
                'SELECT sealed_count, revealed FROM campaign_batches WHERE batch_id = ?', (batch_id,)
            ).fetchone()
            if row is None:
                raise LookupError(f'unknown batch {batch_id!r}')
            count, revealed = row
            if not revealed:
                self.connection.execute(
                    'UPDATE campaign_batches SET revealed = 1 WHERE batch_id = ?', (batch_id,)
                )
            return int(count)

    def load_revealed_count(self, batch_id: str) -> int | None:
        row = self.connection.execute(
            '''SELECT CASE WHEN revealed = 1 THEN sealed_count END
               FROM campaign_batches WHERE batch_id = ?''',
            (batch_id,),
        ).fetchone()
        if row is None:
            raise LookupError(f'unknown batch {batch_id!r}')
        return None if row[0] is None else int(row[0])
