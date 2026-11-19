from fractions import Fraction
import json
import sqlite3

from campaign.allocation import StationAllocation
from campaign.modes import ModeManifest, ModeSpec
from observations.intake import TrialEvidence
from observations.weights import EvidenceWeights
from storage import initialize_schema, write_transaction


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS frozen_revisions (
    revision_id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    capacity TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frozen_trials (
    revision_id INTEGER NOT NULL REFERENCES frozen_revisions(revision_id),
    ordinal INTEGER NOT NULL,
    trial_id TEXT NOT NULL,
    normal_weight TEXT NOT NULL,
    defect_weight TEXT NOT NULL,
    PRIMARY KEY (revision_id, ordinal),
    UNIQUE (revision_id, trial_id)
);
CREATE TABLE IF NOT EXISTS frozen_mode_revisions (
    revision_id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    station_id TEXT NOT NULL,
    capacity TEXT NOT NULL,
    manifest_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS frozen_mode_trials (
    revision_id INTEGER NOT NULL REFERENCES frozen_mode_revisions(revision_id),
    ordinal INTEGER NOT NULL,
    trial_id TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    PRIMARY KEY (revision_id, ordinal),
    UNIQUE (revision_id, trial_id)
);
'''


class ObservationRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        initialize_schema(self.connection, _SCHEMA)

    def save_frozen(
        self, batch_id: str, station_id: str, capacity: int, trials: tuple[TrialEvidence, ...]
    ) -> int:
        with write_transaction(self.connection):
            cursor = self.connection.execute(
                'INSERT INTO frozen_revisions (batch_id, station_id, capacity) VALUES (?, ?, ?)',
                (batch_id, station_id, str(capacity)),
            )
            revision_id = cursor.lastrowid
            self.connection.executemany(
                '''INSERT INTO frozen_trials
                   (revision_id, ordinal, trial_id, normal_weight, defect_weight)
                   VALUES (?, ?, ?, ?, ?)''',
                (
                    (revision_id, index, trial.trial_id, str(trial.weights.normal), str(trial.weights.defect))
                    for index, trial in enumerate(trials)
                ),
            )
        return revision_id

    def load_frozen(self, revision_id: int) -> tuple[str, str, int, tuple[TrialEvidence, ...]] | None:
        header = self.connection.execute(
            'SELECT batch_id, station_id, capacity FROM frozen_revisions WHERE revision_id = ?',
            (revision_id,),
        ).fetchone()
        if header is None:
            return None
        rows = self.connection.execute(
            '''SELECT trial_id, normal_weight, defect_weight FROM frozen_trials
               WHERE revision_id = ? ORDER BY ordinal''',
            (revision_id,),
        )
        trials = tuple(
            TrialEvidence(trial_id, EvidenceWeights(Fraction(normal), Fraction(defect)))
            for trial_id, normal, defect in rows
        )
        batch_id, station_id, capacity = header
        return batch_id, station_id, int(capacity), trials

    def save_frozen_modes(
        self,
        manifest: ModeManifest,
        station_id: str,
        capacity: int,
        trials: tuple[object, ...],
    ) -> int:
        manifest_json = json.dumps(manifest.to_dict(), ensure_ascii=False, sort_keys=True)
        with write_transaction(self.connection):
            cursor = self.connection.execute(
                '''INSERT INTO frozen_mode_revisions
                   (batch_id, station_id, capacity, manifest_json) VALUES (?, ?, ?, ?)''',
                (manifest.batch_id, station_id, str(capacity), manifest_json),
            )
            revision_id = cursor.lastrowid
            self.connection.executemany(
                '''INSERT INTO frozen_mode_trials
                   (revision_id, ordinal, trial_id, weights_json) VALUES (?, ?, ?, ?)''',
                (
                    (
                        revision_id,
                        index,
                        trial.trial_id,
                        json.dumps(
                            [[name, str(weights.normal), str(weights.defect)] for name, weights in trial.weights],
                            ensure_ascii=False,
                        ),
                    )
                    for index, trial in enumerate(trials)
                ),
            )
        return revision_id

    def load_frozen_modes(
        self, revision_id: int
    ) -> tuple[str, str, int, ModeManifest, tuple[tuple[str, tuple[tuple[str, EvidenceWeights], ...]], ...]] | None:
        header = self.connection.execute(
            '''SELECT batch_id, station_id, capacity, manifest_json
               FROM frozen_mode_revisions WHERE revision_id = ?''',
            (revision_id,),
        ).fetchone()
        if header is None:
            return None
        batch_id, station_id, capacity, manifest_json = header
        manifest = ModeManifest(
            json.loads(manifest_json)['batch_id'],
            tuple(
                StationAllocation(row['station_id'], int(row['capacity']))
                for row in json.loads(manifest_json)['allocations']
            ),
            tuple(
                ModeSpec(row['name'], Fraction(row['prior']))
                for row in json.loads(manifest_json)['modes']
            ),
        )
        rows = self.connection.execute(
            '''SELECT trial_id, weights_json FROM frozen_mode_trials
               WHERE revision_id = ? ORDER BY ordinal''',
            (revision_id,),
        )
        trials = tuple(
            (
                trial_id,
                tuple(
                    (name, EvidenceWeights(Fraction(normal), Fraction(defect)))
                    for name, normal, defect in json.loads(weights_json)
                ),
            )
            for trial_id, weights_json in rows
        )
        return batch_id, station_id, int(capacity), manifest, trials
