from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class StationPiece:
    batch_id: str
    station_id: str
    capacity: int
    issue_id: str
    revision_id: str
    count_evidence: tuple[Fraction, ...]


@dataclass(frozen=True, slots=True)
class ModeStationPiece:
    batch_id: str
    station_id: str
    capacity: int
    issue_id: str
    revision_id: str
    mode_evidence: tuple[tuple[str, tuple[Fraction, ...]], ...]

    def evidence_for(self, mode: str) -> tuple[Fraction, ...]:
        for name, values in self.mode_evidence:
            if name == mode:
                return values
        raise ValueError(f'station piece has no evidence for mode {mode!r}')

    @property
    def mode_names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.mode_evidence)


def validate_piece(piece: StationPiece) -> None:
    for field in ('batch_id', 'station_id', 'issue_id', 'revision_id'):
        value = getattr(piece, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{field} must be a nonempty string')
    if type(piece.capacity) is not int or piece.capacity <= 0:
        raise ValueError('piece capacity must be a positive integer')
    if not isinstance(piece.count_evidence, tuple) or len(piece.count_evidence) != piece.capacity + 1:
        raise ValueError('count evidence must cover zero through station capacity')
    for weight in piece.count_evidence:
        if not isinstance(weight, Fraction) or weight < 0:
            raise ValueError('count evidence requires nonnegative fractions')
    if not any(piece.count_evidence):
        raise ValueError('count evidence requires at least one positive weight')


def validate_mode_piece(piece: ModeStationPiece) -> None:
    for field in ('batch_id', 'station_id', 'issue_id', 'revision_id'):
        value = getattr(piece, field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{field} must be a nonempty string')
    if type(piece.capacity) is not int or piece.capacity <= 0:
        raise ValueError('piece capacity must be a positive integer')
    if not isinstance(piece.mode_evidence, tuple) or not piece.mode_evidence:
        raise ValueError('mode evidence must cover every shared mode')
    names = set()
    for name, values in piece.mode_evidence:
        if not isinstance(name, str) or not name.strip():
            raise ValueError('mode names must be nonempty strings')
        if name in names:
            raise ValueError(f'duplicate mode {name!r} in station piece')
        names.add(name)
        if not isinstance(values, tuple) or len(values) != piece.capacity + 1:
            raise ValueError('count evidence must cover zero through station capacity')
        for weight in values:
            if not isinstance(weight, Fraction) or weight < 0:
                raise ValueError('count evidence requires nonnegative fractions')
