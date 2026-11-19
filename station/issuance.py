from uuid import uuid4

from observations.modes import read_frozen_mode_revision
from observations.repository import ObservationRepository
from observations.revision import read_frozen_revision
from station.codec import encode_v1, encode_v2
from station.evidence import build_count_evidence, build_mode_count_evidence
from station.piece import ModeStationPiece, StationPiece
from station.repository import StationRepository


def issue(observations: ObservationRepository, station: StationRepository, revision_id: int) -> str:
    if type(revision_id) is not int or revision_id <= 0:
        raise ValueError('revision_id must be a positive integer')
    existing = station.get_by_revision(revision_id)
    if existing is not None:
        return existing

    revision = read_frozen_revision(observations, revision_id)
    piece = StationPiece(
        batch_id=revision.batch_id,
        station_id=revision.station_id,
        capacity=revision.capacity,
        issue_id=uuid4().hex,
        revision_id=str(revision.revision_id),
        count_evidence=build_count_evidence(revision),
    )
    return station.save_issued(revision_id, piece.issue_id, encode_v1(piece))


def issue_modes(
    observations: ObservationRepository, station: StationRepository, revision_id: int
) -> str:
    if type(revision_id) is not int or revision_id <= 0:
        raise ValueError('revision_id must be a positive integer')
    existing = station.get_mode_by_revision(revision_id)
    if existing is not None:
        return existing

    revision = read_frozen_mode_revision(observations, revision_id)
    piece = ModeStationPiece(
        batch_id=revision.batch_id,
        station_id=revision.station_id,
        capacity=revision.capacity,
        issue_id=uuid4().hex,
        revision_id=str(revision.revision_id),
        mode_evidence=build_mode_count_evidence(revision),
    )
    return station.save_issued_mode(revision_id, piece.issue_id, encode_v2(piece))
