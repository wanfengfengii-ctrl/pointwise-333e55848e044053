from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from itertools import product
from math import prod

from campaign.modes import ModeManifest
from receipt.intake import AcceptedModePiece, read_accepted_mode_piece
from receipt.repository import ReceiptRepository
from review.thresholds import Threshold
from station.piece import ModeStationPiece


@dataclass(frozen=True, slots=True)
class ModeSelection:
    batch_id: str
    manifest: ModeManifest
    entries: tuple[AcceptedModePiece, ...]

    @property
    def receipt_ids(self) -> tuple[int, ...]:
        return tuple(entry.receipt_id for entry in self.entries)

    @property
    def station_ids(self) -> tuple[str, ...]:
        return tuple(entry.piece.station_id for entry in self.entries)


def _check_against_manifest(manifest: ModeManifest, piece: ModeStationPiece) -> None:
    if piece.batch_id != manifest.batch_id:
        raise ValueError(f'piece {piece.issue_id!r} belongs to a different batch')
    if piece.station_id not in {row.station_id for row in manifest.allocations}:
        raise ValueError(f'unknown station {piece.station_id!r} in batch {manifest.batch_id!r}')
    if manifest.capacity_for(piece.station_id) != piece.capacity:
        raise ValueError(f'piece capacity for station {piece.station_id!r} differs from manifest')
    if set(piece.mode_names) != {mode.name for mode in manifest.modes}:
        raise ValueError(f'piece {piece.issue_id!r} does not carry the registered mode evidence')


def select_mode_pieces(
    receipts: ReceiptRepository,
    manifest: ModeManifest,
    receipt_ids: Iterable[int],
) -> ModeSelection:
    try:
        ids = tuple(receipt_ids)
    except TypeError as exc:
        raise ValueError('receipt_ids must be a collection of positive integers') from exc
    if any(type(receipt_id) is not int or receipt_id <= 0 for receipt_id in ids):
        raise ValueError('receipt_ids must be positive integers')
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate receipt ID in selection')

    entries = []
    stations = set()
    for receipt_id in ids:
        accepted = read_accepted_mode_piece(receipts, receipt_id)
        _check_against_manifest(manifest, accepted.piece)
        station_id = accepted.piece.station_id
        if station_id in stations:
            raise ValueError(f'multiple pieces selected for station {station_id!r}')
        stations.add(station_id)
        entries.append(accepted)

    missing = {row.station_id for row in manifest.allocations} - stations
    if missing:
        raise ValueError(f'selection is missing stations {sorted(missing)!r}')
    return ModeSelection(manifest.batch_id, manifest, tuple(entries))


def iter_mode_counts(
    selection: ModeSelection, mode_name: str, defect_count: int
) -> Iterable[tuple[tuple[int, ...], Fraction]]:
    supports = []
    for entry in selection.entries:
        values = entry.piece.evidence_for(mode_name)
        supports.append(tuple((count, weight) for count, weight in enumerate(values) if weight))
    for combination in product(*supports):
        counts = tuple(count for count, _ in combination)
        if sum(counts) == defect_count:
            yield counts, prod((weight for _, weight in combination), start=Fraction(1))


@dataclass(frozen=True, slots=True)
class ModeRisk:
    probabilities: tuple[Fraction, Fraction, Fraction]
    posterior: tuple[Fraction, ...]


def evaluate_modes(
    selection: ModeSelection, defect_count: int, thresholds: Iterable[Threshold]
) -> ModeRisk:
    queries = tuple(thresholds)
    first, second = queries
    stations = selection.station_ids
    first_index = stations.index(first.station_id)
    second_index = stations.index(second.station_id)

    qualities = []
    totals = []
    for mode in selection.manifest.modes:
        total = first_weight = second_weight = joint_weight = Fraction(0)
        for counts, weight in iter_mode_counts(selection, mode.name, defect_count):
            total += weight
            first_met = counts[first_index] >= first.minimum
            second_met = counts[second_index] >= second.minimum
            if first_met:
                first_weight += weight
            if second_met:
                second_weight += weight
            if first_met and second_met:
                joint_weight += weight
        qualities.append(mode.prior * total)
        totals.append((first_weight, second_weight, joint_weight))

    denominator = sum(qualities, start=Fraction(0))
    if denominator == 0:
        raise ValueError('no compatible evidence for the revealed defect count')

    def blend(position: int) -> Fraction:
        return sum(
            (mode.prior * values[position] for mode, values in zip(selection.manifest.modes, totals)),
            start=Fraction(0),
        ) / denominator

    posterior = tuple(quality / denominator for quality in qualities)
    return ModeRisk((blend(0), blend(1), blend(2)), posterior)
