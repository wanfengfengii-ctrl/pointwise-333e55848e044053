from dataclasses import dataclass

from campaign.manifest import read_manifest
from campaign.modes import ModeManifest, parse_mode_manifest
from campaign.repository import CampaignRepository
from receipt.origin import validate_origin
from receipt.repository import ReceiptRepository
from station.codec import decode_v1, decode_v2, encode_v1, encode_v2, scale_canonical
from station.piece import ModeStationPiece, StationPiece


@dataclass(frozen=True, slots=True)
class AcceptedPiece:
    receipt_id: int
    piece: StationPiece
    envelope: str


@dataclass(frozen=True, slots=True)
class AcceptedModePiece:
    receipt_id: int
    piece: ModeStationPiece
    envelope: str


def receive(
    campaign: CampaignRepository, receipts: ReceiptRepository, batch_id: str, envelope: str
) -> int:
    manifest = read_manifest(campaign, batch_id)
    if campaign.load_modes(manifest.batch_id) is not None:
        raise ValueError(f'batch {batch_id!r} accepts shared-mode pieces only')
    piece = decode_v1(envelope)
    validate_origin(manifest, piece)
    return receipts.accept_piece(
        piece.batch_id, piece.station_id, piece.issue_id, envelope, encode_v1(piece)
    )


def read_accepted_piece(receipts: ReceiptRepository, receipt_id: int) -> AcceptedPiece:
    if type(receipt_id) is not int or receipt_id <= 0:
        raise ValueError('receipt_id must be a positive integer')
    envelope = receipts.read_v1_piece(receipt_id)
    if envelope is None:
        raise LookupError(f'unknown receipt {receipt_id}')
    return AcceptedPiece(receipt_id, decode_v1(envelope), envelope)


def _validate_mode_origin(manifest: ModeManifest, piece: ModeStationPiece) -> None:
    if piece.batch_id != manifest.batch_id:
        raise ValueError(f'piece {piece.issue_id!r} belongs to a different batch')
    if piece.station_id not in {row.station_id for row in manifest.allocations}:
        raise ValueError(f'unknown station {piece.station_id!r} in batch {manifest.batch_id!r}')
    capacity = manifest.capacity_for(piece.station_id)
    if capacity != piece.capacity:
        raise ValueError(f'piece capacity for station {piece.station_id!r} differs from manifest')
    if set(piece.mode_names) != {mode.name for mode in manifest.modes}:
        raise ValueError(f'piece {piece.issue_id!r} does not carry the registered mode evidence')


def receive_modes(
    campaign: CampaignRepository, receipts: ReceiptRepository, batch_id: str, envelope: str
) -> int:
    stored = campaign.load_mode_manifest(batch_id)
    if stored is None:
        if campaign.load_allocations(batch_id) is not None:
            raise ValueError(f'batch {batch_id!r} is not a shared-mode batch')
        raise LookupError(f'unknown batch {batch_id!r}')
    manifest = parse_mode_manifest(stored)
    piece = decode_v2(envelope)
    _validate_mode_origin(manifest, piece)
    return receipts.accept_mode_piece(
        piece.batch_id,
        piece.station_id,
        piece.issue_id,
        envelope,
        encode_v2(piece),
        scale_canonical(piece),
    )


def read_mode_envelope(receipts: ReceiptRepository, receipt_id: int) -> str:
    if type(receipt_id) is not int or receipt_id <= 0:
        raise ValueError('receipt_id must be a positive integer')
    envelope = receipts.read_mode_piece(receipt_id)
    if envelope is None:
        raise LookupError(f'unknown receipt {receipt_id}')
    return envelope


def read_accepted_mode_piece(receipts: ReceiptRepository, receipt_id: int) -> AcceptedModePiece:
    envelope = read_mode_envelope(receipts, receipt_id)
    return AcceptedModePiece(receipt_id, decode_v2(envelope), envelope)
