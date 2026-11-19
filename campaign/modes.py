from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from campaign.allocation import StationAllocation, total_capacity, validate_allocations


@dataclass(frozen=True, slots=True)
class ModeSpec:
    name: str
    prior: Fraction


@dataclass(frozen=True, slots=True)
class ModeManifest:
    batch_id: str
    allocations: tuple[StationAllocation, ...]
    modes: tuple[ModeSpec, ...]

    def capacity_for(self, station_id: str) -> int:
        for allocation in self.allocations:
            if allocation.station_id == station_id:
                return allocation.capacity
        raise ValueError(f'unknown station {station_id!r} in batch {self.batch_id!r}')

    def to_dict(self) -> dict[str, object]:
        return {
            'batch_id': self.batch_id,
            'allocations': [
                {'station_id': row.station_id, 'capacity': row.capacity} for row in self.allocations
            ],
            'modes': [{'name': mode.name, 'prior': str(mode.prior)} for mode in self.modes],
        }


def parse_prior(value: object) -> Fraction:
    if type(value) is bool:
        raise ValueError('mode prior must be an exact positive rational')
    if type(value) is not int and not isinstance(value, (str, Fraction)):
        raise ValueError('mode prior must be an exact positive rational')
    try:
        prior = Fraction(value)
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise ValueError('invalid mode prior') from exc
    if prior <= 0:
        raise ValueError('mode prior must be positive')
    return prior


def parse_modes(values: Iterable[Mapping[str, object]]) -> tuple[ModeSpec, ...]:
    try:
        submitted = tuple(values)
    except TypeError as exc:
        raise ValueError('modes must be a collection of mode definitions') from exc
    if not submitted:
        raise ValueError('a shared-mode batch requires at least one mode')

    modes = []
    names = set()
    for line, row in enumerate(submitted, start=1):
        if not isinstance(row, Mapping) or set(row) != {'name', 'prior'}:
            raise ValueError(f'mode row {line} must contain name and prior')
        name = row['name']
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'mode row {line} requires a nonempty name')
        if name in names:
            raise ValueError(f'duplicate mode {name!r}')
        names.add(name)
        try:
            prior = parse_prior(row['prior'])
        except ValueError as exc:
            raise ValueError(f'mode {name!r}: {exc}') from exc
        modes.append(ModeSpec(name, prior))

    if sum((mode.prior for mode in modes), start=Fraction(0)) != 1:
        raise ValueError('mode priors must sum exactly to one')
    return tuple(modes)


def _validate_batch_id(batch_id: object) -> str:
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise ValueError('batch_id must be a nonempty string')
    return batch_id


def make_mode_manifest(
    batch_id: str,
    allocations: Iterable[StationAllocation],
    modes: Iterable[Mapping[str, object]] | Iterable[ModeSpec],
) -> ModeManifest:
    _validate_batch_id(batch_id)
    station_rows = validate_allocations(allocations)
    try:
        submitted = tuple(modes)
    except TypeError as exc:
        raise ValueError('modes must be a collection of mode definitions') from exc
    if submitted and all(isinstance(mode, ModeSpec) for mode in submitted):
        mode_rows = tuple(submitted)
        names = set()
        for mode in mode_rows:
            if not isinstance(mode.name, str) or not mode.name.strip():
                raise ValueError('mode name must be a nonempty string')
            if mode.name in names:
                raise ValueError(f'duplicate mode {mode.name!r}')
            names.add(mode.name)
            if not isinstance(mode.prior, Fraction) or mode.prior <= 0:
                raise ValueError('mode prior must be a positive exact rational')
        if not mode_rows:
            raise ValueError('a shared-mode batch requires at least one mode')
        if sum((mode.prior for mode in mode_rows), start=Fraction(0)) != 1:
            raise ValueError('mode priors must sum exactly to one')
    else:
        mode_rows = parse_modes(submitted)
    return ModeManifest(batch_id, station_rows, mode_rows)


def parse_mode_manifest(value: object) -> ModeManifest:
    if isinstance(value, ModeManifest):
        return make_mode_manifest(value.batch_id, value.allocations, value.modes)
    if not isinstance(value, Mapping):
        raise ValueError('shared-mode manifest must be an object')
    if set(value) != {'batch_id', 'allocations', 'modes'}:
        raise ValueError('shared-mode manifest requires batch_id, allocations and modes')
    allocations = value['allocations']
    if not isinstance(allocations, list):
        raise ValueError('manifest allocations must be an array')
    rows = []
    for line, row in enumerate(allocations, start=1):
        if not isinstance(row, Mapping) or set(row) != {'station_id', 'capacity'}:
            raise ValueError(f'allocation row {line} must contain station_id and capacity')
        try:
            rows.append(StationAllocation(**row))
        except TypeError as exc:
            raise ValueError(f'invalid allocation row {line}') from exc
    return make_mode_manifest(_validate_batch_id(value['batch_id']), rows, value['modes'])


def check_sealed_count(sealed_count: object, allocations: Iterable[StationAllocation]) -> int:
    if type(sealed_count) is not int or not 0 <= sealed_count <= total_capacity(allocations):
        raise ValueError('sealed defect count is outside batch capacity')
    return sealed_count
