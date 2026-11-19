from dataclasses import dataclass
from fractions import Fraction
import json
import sqlite3

from review.thresholds import RiskProbabilities, Threshold
from storage import initialize_schema, write_transaction


_SCHEMA = '''
CREATE TABLE IF NOT EXISTS reports (
    report_id INTEGER PRIMARY KEY,
    batch_id TEXT NOT NULL,
    defect_count TEXT NOT NULL,
    receipt_ids_json TEXT NOT NULL,
    thresholds_json TEXT NOT NULL,
    probabilities_json TEXT NOT NULL,
    manifest_json TEXT,
    mode_posterior_json TEXT
);
CREATE TABLE IF NOT EXISTS publication_receipts (
    receipt_id INTEGER PRIMARY KEY,
    report_id INTEGER NOT NULL UNIQUE REFERENCES reports(report_id)
);
'''


@dataclass(frozen=True, slots=True)
class PublishedReport:
    report_id: int
    batch_id: str
    defect_count: int
    receipt_ids: tuple[int, ...]
    thresholds: tuple[Threshold, Threshold]
    probabilities: RiskProbabilities


@dataclass(frozen=True, slots=True)
class PublishedModeReport:
    report_id: int
    batch_id: str
    manifest: dict
    defect_count: int
    receipt_ids: tuple[int, ...]
    thresholds: tuple[Threshold, Threshold]
    probabilities: RiskProbabilities
    mode_posterior: tuple[Fraction, ...]


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    receipt_id: int
    report_id: int


class ReportRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def initialize(self) -> None:
        initialize_schema(self.connection, _SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        columns = {row[1] for row in self.connection.execute('PRAGMA table_info(reports)')}
        additions = []
        if 'manifest_json' not in columns:
            additions.append('ALTER TABLE reports ADD COLUMN manifest_json TEXT')
        if 'mode_posterior_json' not in columns:
            additions.append('ALTER TABLE reports ADD COLUMN mode_posterior_json TEXT')
        if additions:
            with write_transaction(self.connection):
                for statement in additions:
                    self.connection.execute(statement)

    def save_publication(
        self,
        batch_id: str,
        defect_count: int,
        receipt_ids: tuple[int, ...],
        thresholds: tuple[Threshold, Threshold],
        probabilities: RiskProbabilities,
    ) -> tuple[PublishedReport, PublicationReceipt]:
        selected_json = json.dumps(receipt_ids)
        thresholds_json = json.dumps([(query.station_id, query.minimum) for query in thresholds])
        probabilities_json = json.dumps([str(value) for value in probabilities.as_tuple()])
        with write_transaction(self.connection):
            report_id = self.connection.execute(
                '''INSERT INTO reports
                   (batch_id, defect_count, receipt_ids_json, thresholds_json, probabilities_json)
                   VALUES (?, ?, ?, ?, ?)''',
                (batch_id, str(defect_count), selected_json, thresholds_json, probabilities_json),
            ).lastrowid
            receipt_id = self.connection.execute(
                'INSERT INTO publication_receipts (report_id) VALUES (?)', (report_id,)
            ).lastrowid
        return (
            PublishedReport(report_id, batch_id, defect_count, receipt_ids, thresholds, probabilities),
            PublicationReceipt(receipt_id, report_id),
        )

    def save_mode_publication(
        self,
        manifest: dict,
        defect_count: int,
        receipt_ids: tuple[int, ...],
        thresholds: tuple[Threshold, Threshold],
        probabilities: RiskProbabilities,
        mode_posterior: tuple[Fraction, ...],
    ) -> tuple[PublishedModeReport, PublicationReceipt]:
        manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
        selected_json = json.dumps(receipt_ids)
        thresholds_json = json.dumps([(query.station_id, query.minimum) for query in thresholds])
        probabilities_json = json.dumps([str(value) for value in probabilities.as_tuple()])
        posterior_json = json.dumps([str(value) for value in mode_posterior])
        batch_id = manifest['batch_id']
        with write_transaction(self.connection):
            report_id = self.connection.execute(
                '''INSERT INTO reports
                   (batch_id, defect_count, receipt_ids_json, thresholds_json, probabilities_json,
                    manifest_json, mode_posterior_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    batch_id,
                    str(defect_count),
                    selected_json,
                    thresholds_json,
                    probabilities_json,
                    manifest_json,
                    posterior_json,
                ),
            ).lastrowid
            receipt_id = self.connection.execute(
                'INSERT INTO publication_receipts (report_id) VALUES (?)', (report_id,)
            ).lastrowid
        return (
            PublishedModeReport(
                report_id,
                batch_id,
                manifest,
                defect_count,
                receipt_ids,
                thresholds,
                probabilities,
                mode_posterior,
            ),
            PublicationReceipt(receipt_id, report_id),
        )

    def load_publication(self, report_id: int) -> tuple[PublishedReport, PublicationReceipt] | None:
        row = self._load_row(report_id)
        if row is None:
            return None
        (report_id, batch_id, defect_count, ids, queries, probabilities, manifest, posterior, receipt_id) = row
        if manifest is not None:
            raise ValueError(f'report {report_id} is a shared-mode report')
        first, second = (Threshold(station_id, minimum) for station_id, minimum in json.loads(queries))
        report = PublishedReport(
            report_id,
            batch_id,
            int(defect_count),
            tuple(json.loads(ids)),
            (first, second),
            RiskProbabilities(*(Fraction(value) for value in json.loads(probabilities))),
        )
        return report, PublicationReceipt(receipt_id, report_id)

    def load_mode_publication(
        self, report_id: int
    ) -> tuple[PublishedModeReport, PublicationReceipt] | None:
        row = self._load_row(report_id)
        if row is None:
            return None
        (report_id, batch_id, defect_count, ids, queries, probabilities, manifest, posterior, receipt_id) = row
        if manifest is None or posterior is None:
            raise ValueError(f'report {report_id} is not a shared-mode report')
        first, second = (Threshold(station_id, minimum) for station_id, minimum in json.loads(queries))
        report = PublishedModeReport(
            report_id,
            batch_id,
            json.loads(manifest),
            int(defect_count),
            tuple(json.loads(ids)),
            (first, second),
            RiskProbabilities(*(Fraction(value) for value in json.loads(probabilities))),
            tuple(Fraction(value) for value in json.loads(posterior)),
        )
        return report, PublicationReceipt(receipt_id, report_id)

    def _load_row(self, report_id: int):
        if report_id > 2**63 - 1:
            return None
        row = self.connection.execute(
            '''SELECT r.report_id, r.batch_id, r.defect_count, r.receipt_ids_json, r.thresholds_json,
                      r.probabilities_json, r.manifest_json, r.mode_posterior_json, p.receipt_id
               FROM reports AS r JOIN publication_receipts AS p ON p.report_id = r.report_id
               WHERE r.report_id = ?''',
            (report_id,),
        ).fetchone()
        return None if row is None else tuple(row)
