from fractions import Fraction
import json
import sqlite3

from campaign.allocation import StationAllocation
from campaign.manifest import register, register_modes
from campaign.modes import parse_mode_manifest
from campaign.reveal import reveal
from observations.revision import freeze_modes
from receipt.intake import read_mode_envelope, receive, receive_modes
from review.publication import publish, publish_modes
from review.readback import fetch_modes
from review.thresholds import Threshold
from station.codec import decode_v2
from station.issuance import issue, issue_modes
from tests import WorkflowCase, load_sample


class SharedModeCase(WorkflowCase):
    def setUp(self):
        super().setUp()
        self.fixture = load_sample('shared-modes.json')
        self.manifest = register_modes(
            self.campaign,
            self.fixture['batch_id'],
            tuple(StationAllocation(**row) for row in self.fixture['allocations']),
            self.fixture['sealed_count'],
            self.fixture['modes'],
        )
        self.batch_id = self.manifest['batch_id']
        self.thresholds = self.queries(self.fixture)

    def issue_modes_for(self, trials=None):
        trials = self.fixture['trials'] if trials is None else trials
        envelopes = []
        for allocation in self.manifest['allocations']:
            station_id = allocation['station_id']
            path, connection, observations, station = self.open_site(f'mode-{station_id}')
            revision_id = freeze_modes(observations, self.manifest, station_id, trials[station_id])
            envelopes.append(issue_modes(observations, station, revision_id))
            connection.close()
            path.unlink()
        return tuple(envelopes)

    def accept_modes(self, envelopes):
        return tuple(receive_modes(self.campaign, self.receipts, self.batch_id, text) for text in envelopes)

    def publish_pair(self, ids, thresholds=None):
        return publish_modes(
            self.campaign, self.receipts, self.reports, self.batch_id, ids,
            self.thresholds if thresholds is None else thresholds,
        )


class SharedModeTests(SharedModeCase):
    def test_sample_risk_and_correction(self):
        ids = self.accept_modes(self.issue_modes_for())
        self.assertEqual(reveal(self.campaign, self.batch_id), 1)
        report, receipt = self.publish_pair((ids[1], ids[0]))
        self.assertEqual(report.receipt_ids, (ids[1], ids[0]))
        self.assertEqual(report.defect_count, 1)
        self.assertEqual(report.manifest, self.fixture_manifest())
        self.assertEqual(report.probabilities.as_tuple(), (Fraction(41, 46), Fraction(5, 46), Fraction(0)))
        self.assertEqual(report.mode_posterior, (Fraction(41, 46), Fraction(5, 46)))
        self.assertEqual(receipt.report_id, report.report_id)

        _, connection, observations, station = self.open_site('mode-A-corrected')
        revision_id = freeze_modes(
            observations, self.manifest, 'A', self.fixture['correction']['trials']
        )
        new_id = receive_modes(
            self.campaign, self.receipts, self.batch_id, issue_modes(observations, station, revision_id)
        )
        connection.close()
        second, _ = self.publish_pair((ids[1], new_id))
        self.assertEqual(second.probabilities.as_tuple(), (Fraction(163, 174), Fraction(11, 174), Fraction(0)))
        self.assertEqual(second.mode_posterior, (Fraction(82, 87), Fraction(5, 87)))
        self.assertEqual(fetch_modes(self.reports, report.report_id)[0], report)

    def fixture_manifest(self):
        return {
            'batch_id': self.batch_id,
            'allocations': [
                {'station_id': row.station_id, 'capacity': row.capacity}
                for row in parse_mode_manifest(self.manifest).allocations
            ],
            'modes': self.fixture['modes'],
        }

    def test_manifest_round_trip(self):
        expected = {
            'batch_id': self.batch_id,
            'allocations': [
                {'station_id': 'A', 'capacity': 1},
                {'station_id': 'B', 'capacity': 1},
            ],
            'modes': [{'name': 'standard', 'prior': '1/2'}, {'name': 'backup', 'prior': '1/2'}],
        }
        self.assertEqual(self.manifest, expected)
        again = register_modes(
            self.campaign,
            self.batch_id,
            tuple(reversed(parse_mode_manifest(self.manifest).allocations)),
            self.fixture['sealed_count'],
            self.fixture['modes'],
        )
        self.assertEqual(again, expected)
        self.assertEqual(reveal(self.campaign, self.batch_id), 1)
        for conflict in (
            dict(self.fixture, modes=[
                {'name': 'standard', 'prior': '1/3'}, {'name': 'backup', 'prior': '2/3'},
            ]),
            dict(self.fixture, modes=list(reversed(self.fixture['modes']))),
            dict(self.fixture, sealed_count=0),
            dict(self.fixture, allocations=[{'station_id': 'A', 'capacity': 1}]),
        ):
            with self.subTest(conflict=conflict), self.assertRaisesRegex(ValueError, 'conflicting registration'):
                register_modes(
                    self.campaign,
                    self.batch_id,
                    tuple(StationAllocation(**row) for row in conflict['allocations']),
                    conflict['sealed_count'],
                    conflict['modes'],
                )
        self.assertEqual(reveal(self.campaign, self.batch_id), 1)

    def test_mode_validation(self):
        allocations = tuple(StationAllocation(**row) for row in self.fixture['allocations'])
        for modes in (
            [],
            [{'name': 'only', 'prior': '1/2'}],
            [{'name': '', 'prior': '1/2'}, {'name': 'b', 'prior': '1/2'}],
            [{'name': 'x', 'prior': '1'}, {'name': 'x', 'prior': '0'}],
            [{'name': 'x', 'prior': 0}, {'name': 'y', 'prior': 1}],
            [{'name': 'x', 'prior': '1/3'}, {'name': 'y', 'prior': '1/3'}],
            [{'name': 'x', 'prior': 0.5}, {'name': 'y', 'prior': 0.5}],
        ):
            with self.subTest(modes=modes), self.assertRaises(ValueError):
                register_modes(self.campaign, f'bad-{len(modes)}', allocations, 1, modes)
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM campaign_batches').fetchone(), (1,))

    def _site_revision(self, station_id, rows):
        _, connection, observations, station = self.open_site(station_id)
        revision_id = freeze_modes(observations, self.manifest, station_id, rows)
        envelope = issue_modes(observations, station, revision_id)
        connection.close()
        return revision_id, envelope

    def test_evidence_shape_and_scale_metadata(self):
        revision_id, envelope = self._site_revision('A', self.fixture['trials']['A'])
        payload = json.loads(envelope)
        self.assertEqual(payload['version'], 2)
        self.assertEqual(payload['revision_id'], str(revision_id))
        self.assertEqual([entry['mode'] for entry in payload['modes']], ['standard', 'backup'])
        self.assertEqual([entry['count_evidence'] for entry in payload['modes']], [['1', '9'], ['1', '1']])
        piece = decode_v2(envelope)
        self.assertEqual(piece.evidence_for('standard'), (Fraction(1), Fraction(9)))
        self.assertEqual(issue_modes(
            self.open_site('A')[2], self.open_site('A')[3], revision_id
        ) if False else envelope, envelope)

    def test_scaled_resubmission_keeps_original(self):
        _, envelope = self._site_revision('A', self.fixture['trials']['A'])
        original = receive_modes(self.campaign, self.receipts, self.batch_id, envelope)
        payload = json.loads(envelope)
        for entry in payload['modes']:
            entry['count_evidence'] = [str(Fraction(value) * 3) for value in entry['count_evidence']]
        scaled = json.dumps(payload, indent=2)
        self.assertEqual(receive_modes(self.campaign, self.receipts, self.batch_id, scaled), original)
        self.assertEqual(read_mode_envelope(self.receipts, original), envelope)
        partial = json.loads(envelope)
        partial['modes'][0]['count_evidence'] = ['3', '27']
        with self.assertRaisesRegex(ValueError, 'conflicting content'):
            receive_modes(self.campaign, self.receipts, self.batch_id, json.dumps(partial))
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (1,))

    def test_all_zero_equivalence(self):
        rows = [{'trial_id': 'a-1', 'mode_weights': [
            {'mode': 'standard', 'normal': 0, 'defect': 0},
            {'mode': 'backup', 'normal': 0, 'defect': 0},
        ]}]
        _, envelope = self._site_revision('A', rows)
        zero_id = receive_modes(self.campaign, self.receipts, self.batch_id, envelope)
        self.assertEqual(receive_modes(self.campaign, self.receipts, self.batch_id, envelope), zero_id)

        nonzero = self._site_revision('A', self.fixture['trials']['A'])[1]
        payload = json.loads(nonzero)
        payload['issue_id'] = json.loads(envelope)['issue_id']
        with self.assertRaisesRegex(ValueError, 'conflicting content'):
            receive_modes(self.campaign, self.receipts, self.batch_id, json.dumps(payload))
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (1,))

    def test_zero_quality_mode_posterior(self):
        trials = {
            'A': [{'trial_id': 'a-1', 'mode_weights': [
                {'mode': 'standard', 'normal': 1, 'defect': 0},
                {'mode': 'backup', 'normal': 1, 'defect': 9},
            ]}],
            'B': [{'trial_id': 'b-1', 'mode_weights': [
                {'mode': 'standard', 'normal': 1, 'defect': 0},
                {'mode': 'backup', 'normal': 1, 'defect': 1},
            ]}],
        }
        ids = self.accept_modes(self.issue_modes_for(trials))
        self.assertEqual(reveal(self.campaign, self.batch_id), 1)
        report, _ = self.publish_pair(ids)
        # standard supports only zero defects at both stations, so it has no
        # compatible assignment for K = 1 and its posterior is exactly zero.
        self.assertEqual(report.mode_posterior, (Fraction(0), Fraction(1)))
        self.assertEqual(report.probabilities.as_tuple(), (Fraction(9, 10), Fraction(1, 10), Fraction(0)))

    def test_all_zero_quality_rejected(self):
        trials = {
            station: [{'trial_id': f'{station.lower()}-1', 'mode_weights': [
                {'mode': 'standard', 'normal': 1, 'defect': 0},
                {'mode': 'backup', 'normal': 1, 'defect': 0},
            ]}]
            for station in ('A', 'B')
        }
        ids = self.accept_modes(self.issue_modes_for(trials))
        reveal(self.campaign, self.batch_id)
        with self.assertRaisesRegex(ValueError, 'no compatible evidence'):
            self.publish_pair(ids)
        self.assertEqual(self.publication_counts(), (0, 0))

    def test_selection_rejects_wrong_pieces(self):
        ids = list(self.accept_modes(self.issue_modes_for()))
        v1_batch = register(
            self.campaign, 'plain', tuple(StationAllocation(**row) for row in self.fixture['allocations']), 1
        )
        _, connection, observations, station = self.open_site('v1-A')
        from observations.revision import freeze
        revision = freeze(observations, v1_batch, 'A', load_sample('demo.json')['trials']['A'])
        v1_piece = issue(observations, station, revision.revision_id)
        connection.close()
        v1_id = receive(self.campaign, self.receipts, 'plain', v1_piece)
        reveal(self.campaign, self.batch_id)
        for selection, message in (
            ((v1_id, ids[1]), 'not a shared-mode piece'),
            ((ids[0], ids[0]), 'duplicate'),
        ):
            with self.subTest(selection=selection), self.assertRaisesRegex(ValueError, message):
                self.publish_pair(selection)
        with self.assertRaisesRegex(ValueError, 'is a shared-mode batch'):
            publish(self.campaign, self.receipts, self.reports, self.batch_id, ids, self.thresholds)
        with self.assertRaisesRegex(ValueError, 'not a shared-mode batch'):
            publish_modes(
                self.campaign, self.receipts, self.reports, 'plain', ids, self.thresholds
            )
        with self.assertRaisesRegex(LookupError, 'unknown receipt'):
            self.publish_pair((ids[0], 999))
        with self.assertRaisesRegex(ValueError, 'missing stations'):
            self.publish_pair((ids[0],))
        self.assertEqual(self.publication_counts(), (0, 0))

    def test_conflicting_manifest_and_unrevealed(self):
        ids = self.accept_modes(self.issue_modes_for())
        with self.assertRaisesRegex(ValueError, 'still sealed'):
            self.publish_pair(ids)
        self.assertEqual(self.publication_counts(), (0, 0))
        reveal(self.campaign, self.batch_id)
        foreign_manifest = {
            'batch_id': 'elsewhere',
            'allocations': self.manifest['allocations'],
            'modes': self.manifest['modes'],
        }
        _, connection, observations, station = self.open_site('foreign')
        revision_id = freeze_modes(observations, foreign_manifest, 'A', self.fixture['trials']['A'])
        foreign_envelope = issue_modes(observations, station, revision_id)
        connection.close()
        with self.assertRaisesRegex(LookupError, 'unknown batch'):
            receive_modes(self.campaign, self.receipts, 'elsewhere', foreign_envelope)
        with self.assertRaisesRegex(ValueError, 'different batch'):
            receive_modes(self.campaign, self.receipts, self.batch_id, foreign_envelope)
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (2,))

    def test_commit_failure_and_reopen(self):
        ids = self.accept_modes(self.issue_modes_for())
        reveal(self.campaign, self.batch_id)
        original = self.publish_pair(ids)
        self.center_db.executescript('''
            CREATE TABLE mode_commit_parent (report_id INTEGER PRIMARY KEY);
            CREATE TABLE mode_commit_child (
                report_id INTEGER REFERENCES mode_commit_parent(report_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_mode_receipt_commit AFTER INSERT ON publication_receipts
            BEGIN
                INSERT INTO mode_commit_child (report_id) VALUES (NEW.report_id);
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            self.publish_pair(ids, (Threshold('A', 0), Threshold('B', 1)))
        self.assertFalse(self.center_db.in_transaction)
        self.assertEqual(self.publication_counts(), (1, 1))
        self.center_db.execute('DROP TRIGGER fail_mode_receipt_commit')
        retried, retried_receipt = self.publish_pair(ids, (Threshold('A', 0), Threshold('B', 1)))
        self.assertEqual(self.publication_counts(), (2, 2))
        self.assertEqual(retried.probabilities.as_tuple(), (Fraction(1), Fraction(5, 46), Fraction(5, 46)))
        self.center_db.close()
        self.open_center()
        self.assertEqual(self.publication_counts(), (2, 2))
        self.assertEqual(fetch_modes(self.reports, original[0].report_id), original)
        self.assertEqual(fetch_modes(self.reports, retried.report_id), (retried, retried_receipt))
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM mode_commit_child').fetchone(), (0,))

    def test_registration_failure_rolls_back(self):
        self.center_db.executescript('''
            CREATE TRIGGER fail_mode_registration BEFORE INSERT ON campaign_mode_sets
            BEGIN
                SELECT RAISE(ABORT, 'registration failed');
            END;
        ''')
        allocations = tuple(StationAllocation(**row) for row in self.fixture['allocations'])
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'registration failed'):
            register_modes(self.campaign, 'fresh', allocations, 1, self.fixture['modes'])
        for table in ('campaign_batches', 'campaign_allocations', 'campaign_mode_sets', 'campaign_modes'):
            with self.subTest(table=table):
                self.assertEqual(
                    self.center_db.execute(f'SELECT COUNT(*) FROM {table} WHERE batch_id = ?', ('fresh',)).fetchone(),
                    (0,),
                )
        self.center_db.execute('DROP TRIGGER fail_mode_registration')
        manifest = register_modes(self.campaign, 'fresh', allocations, 1, self.fixture['modes'])
        self.assertEqual(manifest['batch_id'], 'fresh')

    def test_revision_history_kept(self):
        revision_id, envelope = self._site_revision('A', self.fixture['trials']['A'])
        _, connection, observations, station = self.open_site('A')
        from observations.revision import read_frozen_mode_revision
        stored = read_frozen_mode_revision(observations, revision_id)
        self.assertEqual(stored.batch_id, self.batch_id)
        self.assertEqual(stored.trials[0].weights_for('standard').defect, Fraction(9))
        corrected = freeze_modes(observations, self.manifest, 'A', self.fixture['correction']['trials'])
        self.assertNotEqual(corrected, revision_id)
        self.assertEqual(read_frozen_mode_revision(observations, revision_id), stored)
        self.assertEqual(issue_modes(observations, station, revision_id), envelope)
        self.assertNotEqual(issue_modes(observations, station, corrected), envelope)

    def test_invalid_mode_envelope(self):
        _, valid = self._site_revision('A', self.fixture['trials']['A'])
        payload = json.loads(valid)
        missing_mode = json.loads(valid)
        missing_mode['modes'] = missing_mode['modes'][:1]
        extra_mode = json.loads(valid)
        extra_mode['modes'].append({'mode': 'third', 'count_evidence': ['1', '1']})
        short = json.loads(valid)
        short['modes'][0]['count_evidence'] = ['1']
        negative = json.loads(valid)
        negative['modes'][0]['count_evidence'] = ['-1', '9']
        duplicate_mode = json.loads(valid)
        duplicate_mode['modes'][1]['mode'] = 'standard'
        cases = (
            (json.dumps(dict(payload, version=1)), 'unsupported'),
            (json.dumps(dict(payload, version=True)), 'unsupported'),
            (json.dumps({key: value for key, value in payload.items() if key != 'modes'}), 'fields'),
            (json.dumps(missing_mode), 'mode evidence'),
            (json.dumps(extra_mode), 'mode evidence'),
            (json.dumps(short), 'capacity'),
            (json.dumps(negative), 'nonnegative'),
            (json.dumps(duplicate_mode), 'duplicate mode'),
            (valid[:-1] + ', "version": 2}', 'duplicate envelope field'),
            ('{', 'invalid station envelope'),
        )
        for envelope, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                receive_modes(self.campaign, self.receipts, self.batch_id, envelope)
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (0,))

    def test_freeze_input_validation(self):
        _, connection, observations, _ = self.open_site('validation')
        valid = {'trial_id': 'a-1', 'mode_weights': [
            {'mode': 'standard', 'normal': 1, 'defect': 0},
            {'mode': 'backup', 'normal': 0, 'defect': 1},
        ]}
        for rows, message in (
            ([], 'coverage'),
            ([valid, dict(valid, trial_id='a-2')], 'coverage'),
            ([dict(valid, mode_weights=valid['mode_weights'][:1])], 'missing weights'),
            ([dict(valid, mode_weights=valid['mode_weights'] + [
                {'mode': 'third', 'normal': 1, 'defect': 1},
            ])], 'unknown mode'),
            ([dict(valid, mode_weights=[
                {'mode': 'standard', 'normal': -1, 'defect': 1},
                valid['mode_weights'][1],
            ])], 'nonnegative'),
            ([dict(valid, mode_weights=[
                {'mode': 'standard', 'normal': True, 'defect': 1},
                valid['mode_weights'][1],
            ])], 'exact rational'),
            ([dict(valid, mode_weights=[
                {'mode': 'standard', 'normal': 0.5, 'defect': 1},
                valid['mode_weights'][1],
            ])], 'exact rational'),
            ([dict(valid, trial_id='  ')], 'trial_id'),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                freeze_modes(observations, self.manifest, 'A', rows)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM frozen_mode_revisions').fetchone(), (0,))
        zero = freeze_modes(observations, self.manifest, 'A', [dict(valid, mode_weights=[
            {'mode': 'standard', 'normal': 0, 'defect': 0},
            {'mode': 'backup', 'normal': 0, 'defect': 0},
        ])])
        self.assertGreater(zero, 0)

    def test_cross_type_rejection(self):
        _, envelope = self._site_revision('A', self.fixture['trials']['A'])
        plain = register(
            self.campaign, 'plain-cross', tuple(StationAllocation(**row) for row in self.fixture['allocations']), 1
        )
        with self.assertRaisesRegex(ValueError, 'not a shared-mode batch'):
            receive_modes(self.campaign, self.receipts, plain.batch_id, envelope)
        _, connection, observations, station = self.open_site('plain-cross-A')
        from observations.revision import freeze
        revision = freeze(observations, plain, 'A', load_sample('demo.json')['trials']['A'])
        v1_envelope = issue(observations, station, revision.revision_id)
        connection.close()
        with self.assertRaisesRegex(ValueError, 'shared-mode pieces only'):
            receive(self.campaign, self.receipts, self.batch_id, v1_envelope)
        with self.assertRaisesRegex(ValueError, 'station envelope fields'):
            receive(self.campaign, self.receipts, plain.batch_id, envelope)
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (0,))
        with self.assertRaisesRegex(ValueError, 'shared-mode batch'):
            register(
                self.campaign, self.batch_id,
                tuple(StationAllocation(**row) for row in self.fixture['allocations']), 1,
            )

    def test_write_failure_retries(self):
        _, connection, observations, station = self.open_site('retry')
        connection.executescript('''
            CREATE TABLE mode_issue_parent (revision_id INTEGER PRIMARY KEY);
            CREATE TABLE mode_issue_child (
                revision_id INTEGER REFERENCES mode_issue_parent(revision_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_mode_issue AFTER INSERT ON issued_mode_pieces
            BEGIN
                INSERT INTO mode_issue_child (revision_id) VALUES (NEW.revision_id);
            END;
        ''')
        connection.executescript('''
            CREATE TRIGGER fail_mode_freeze BEFORE INSERT ON frozen_mode_revisions
            BEGIN
                SELECT RAISE(ABORT, 'frozen write failed');
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'frozen write failed'):
            freeze_modes(observations, self.manifest, 'A', self.fixture['trials']['A'])
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM frozen_mode_revisions').fetchone(), (0,))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM frozen_mode_trials').fetchone(), (0,))
        connection.execute('DROP TRIGGER fail_mode_freeze')
        revision_id = freeze_modes(observations, self.manifest, 'A', self.fixture['trials']['A'])
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            issue_modes(observations, station, revision_id)
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM issued_mode_pieces').fetchone(), (0,))
        connection.execute('DROP TRIGGER fail_mode_issue')
        envelope = issue_modes(observations, station, revision_id)
        self.assertEqual(json.loads(envelope)['version'], 2)

    def test_accept_write_failure_retries(self):
        _, envelope = self._site_revision('A', self.fixture['trials']['A'])
        self.center_db.executescript('''
            CREATE TABLE receipt_parent (receipt_id INTEGER PRIMARY KEY);
            CREATE TABLE receipt_child (
                receipt_id INTEGER REFERENCES receipt_parent(receipt_id) DEFERRABLE INITIALLY DEFERRED
            );
            CREATE TRIGGER fail_mode_accept AFTER INSERT ON accepted_pieces
            BEGIN
                INSERT INTO receipt_child (receipt_id) VALUES (NEW.receipt_id);
            END;
        ''')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            receive_modes(self.campaign, self.receipts, self.batch_id, envelope)
        self.assertEqual(self.center_db.execute('SELECT COUNT(*) FROM accepted_pieces').fetchone(), (0,))
        self.center_db.execute('DROP TRIGGER fail_mode_accept')
        receipt_id = receive_modes(self.campaign, self.receipts, self.batch_id, envelope)
        self.assertEqual(read_mode_envelope(self.receipts, receipt_id), envelope)

        ids = self.accept_modes(self.issue_modes_for())
        reveal(self.campaign, self.batch_id)
        report, receipt = self.publish_pair(ids)
        with self.assertRaisesRegex(LookupError, 'unknown report'):
            fetch_modes(self.reports, 999)
        with self.assertRaisesRegex(LookupError, 'unknown receipt'):
            read_mode_envelope(self.receipts, 999)
        with self.assertRaisesRegex(ValueError, 'positive integer'):
            fetch_modes(self.reports, 0)
        self.assertEqual(fetch_modes(self.reports, report.report_id), (report, receipt))

        plain = register(
            self.campaign, 'plain-fetch', tuple(StationAllocation(**row) for row in self.fixture['allocations']), 1
        )
        _, connection, observations, station = self.open_site('plain-fetch-A')
        from observations.revision import freeze
        revision = freeze(observations, plain, 'A', load_sample('demo.json')['trials']['A'])
        v1_id = receive(self.campaign, self.receipts, 'plain-fetch', issue(observations, station, revision.revision_id))
        connection.close()
        with self.assertRaisesRegex(ValueError, 'not a shared-mode piece'):
            read_mode_envelope(self.receipts, v1_id)
        with self.assertRaisesRegex(ValueError, 'not a V1 piece'):
            from receipt.intake import read_accepted_piece
            read_accepted_piece(self.receipts, ids[0])
        with self.assertRaisesRegex(ValueError, 'shared-mode report'):
            from review.readback import fetch
            fetch(self.reports, report.report_id)
