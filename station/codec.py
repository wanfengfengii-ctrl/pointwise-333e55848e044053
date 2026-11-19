from fractions import Fraction
import json
import re

from station.piece import ModeStationPiece, StationPiece, validate_mode_piece, validate_piece


_FIELDS = {'version', 'batch_id', 'station_id', 'capacity', 'issue_id', 'revision_id', 'count_evidence'}
_MODE_FIELDS = {'version', 'batch_id', 'station_id', 'capacity', 'issue_id', 'revision_id', 'modes'}
_MODE_ROW_FIELDS = {'mode', 'count_evidence'}
_FRACTION_TEXT = re.compile(r'-?[0-9]+(?:/[0-9]+)?')
_NONNEGATIVE_TEXT = re.compile(r'[0-9]+(?:/[0-9]+)?')


def encode_v1(piece: StationPiece) -> str:
    validate_piece(piece)
    payload = {
        'version': 1,
        'batch_id': piece.batch_id,
        'station_id': piece.station_id,
        'capacity': piece.capacity,
        'issue_id': piece.issue_id,
        'revision_id': piece.revision_id,
        'count_evidence': [str(weight) for weight in piece.count_evidence],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def encode_v2(piece: ModeStationPiece) -> str:
    validate_mode_piece(piece)
    payload = {
        'version': 2,
        'batch_id': piece.batch_id,
        'station_id': piece.station_id,
        'capacity': piece.capacity,
        'issue_id': piece.issue_id,
        'revision_id': piece.revision_id,
        'modes': [
            {'mode': name, 'count_evidence': [str(weight) for weight in values]}
            for name, values in piece.mode_evidence
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _unique_fields(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate envelope field {key!r}')
        result[key] = value
    return result


def _decode_weight(value: object) -> Fraction:
    if not isinstance(value, str) or _FRACTION_TEXT.fullmatch(value) is None:
        raise ValueError('count evidence entries must be rational strings')
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('invalid count evidence fraction') from exc


def _decode_mode_weight(value: object) -> Fraction:
    if not isinstance(value, str) or _NONNEGATIVE_TEXT.fullmatch(value) is None:
        raise ValueError('count evidence entries must be nonnegative rational strings')
    try:
        weight = Fraction(value)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError('invalid count evidence fraction') from exc
    if weight < 0:
        raise ValueError('count evidence requires nonnegative fractions')
    return weight


def _parse(envelope: str) -> object:
    if not isinstance(envelope, str):
        raise ValueError('station envelope must be JSON text')
    try:
        return json.loads(envelope, object_pairs_hook=_unique_fields)
    except (json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('invalid station envelope JSON') from exc


def decode_v1(envelope: str) -> StationPiece:
    payload = _parse(envelope)
    if not isinstance(payload, dict) or set(payload) != _FIELDS:
        raise ValueError('invalid station envelope fields')
    if type(payload['version']) is not int or payload['version'] != 1:
        raise ValueError('unsupported station envelope version')
    if not isinstance(payload['count_evidence'], list):
        raise ValueError('count evidence must be an array')

    piece = StationPiece(
        batch_id=payload['batch_id'],
        station_id=payload['station_id'],
        capacity=payload['capacity'],
        issue_id=payload['issue_id'],
        revision_id=payload['revision_id'],
        count_evidence=tuple(_decode_weight(value) for value in payload['count_evidence']),
    )
    validate_piece(piece)
    return piece


def decode_v2(envelope: str) -> ModeStationPiece:
    payload = _parse(envelope)
    if not isinstance(payload, dict) or set(payload) != _MODE_FIELDS:
        raise ValueError('invalid station envelope fields')
    if type(payload['version']) is not int or payload['version'] != 2:
        raise ValueError('unsupported station envelope version')
    if not isinstance(payload['modes'], list) or not payload['modes']:
        raise ValueError('mode evidence must be a nonempty array')

    mode_evidence = []
    for entry in payload['modes']:
        if not isinstance(entry, dict) or set(entry) != _MODE_ROW_FIELDS:
            raise ValueError('invalid mode evidence row')
        count_evidence = entry['count_evidence']
        if not isinstance(count_evidence, list):
            raise ValueError('count evidence must be an array')
        mode_evidence.append(
            (entry['mode'], tuple(_decode_mode_weight(value) for value in count_evidence))
        )

    piece = ModeStationPiece(
        batch_id=payload['batch_id'],
        station_id=payload['station_id'],
        capacity=payload['capacity'],
        issue_id=payload['issue_id'],
        revision_id=payload['revision_id'],
        mode_evidence=tuple(mode_evidence),
    )
    validate_mode_piece(piece)
    return piece


def scale_canonical(piece: ModeStationPiece) -> str:
    """Canonical content up to one positive factor shared by every mode weight.

    Two pieces share this value exactly when every count weight of one is the
    same positive rational multiple of the corresponding weight of the other.
    An all-zero piece keeps its own all-zero canonical form, so it is never
    equivalent to a nonzero piece.
    """
    validate_mode_piece(piece)
    entries = [weight for _, values in piece.mode_evidence for weight in values]
    anchor = next((weight for weight in entries if weight), None)
    if anchor is None:
        normalized = [[name, ['0'] * len(values)] for name, values in piece.mode_evidence]
    else:
        normalized = [
            [name, [str(weight / anchor) for weight in values]]
            for name, values in piece.mode_evidence
        ]
    return json.dumps(
        {
            'capacity': piece.capacity,
            'revision_id': piece.revision_id,
            'modes': sorted(normalized),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
