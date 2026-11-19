from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from campaign.modes import ModeManifest, ModeSpec, parse_mode_manifest
from observations.repository import ObservationRepository
from observations.weights import EvidenceWeights


@dataclass(frozen=True, slots=True)
class ModeTrialEvidence:
    trial_id: str
    weights: tuple[tuple[str, EvidenceWeights], ...]

    def weights_for(self, mode: str) -> EvidenceWeights:
        for name, weights in self.weights:
            if name == mode:
                return weights
        raise ValueError(f'trial {self.trial_id!r} has no weight for mode {mode!r}')


@dataclass(frozen=True, slots=True)
class FrozenModeRevision:
    revision_id: int
    batch_id: str
    station_id: str
    capacity: int
    manifest: ModeManifest
    trials: tuple[ModeTrialEvidence, ...]


def _parse_mode_weight(value: object, field: str, context: str) -> Fraction:
    if type(value) is bool:
        raise ValueError(f'{context}: {field} weight must be an exact rational')
    if type(value) is not int and not isinstance(value, (str, Fraction)):
        raise ValueError(f'{context}: {field} weight must be an exact rational')
    try:
        weight = Fraction(value)
    except (ValueError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(f'{context}: invalid {field} weight') from exc
    if weight < 0:
        raise ValueError(f'{context}: {field} weight must be nonnegative')
    return weight


def _parse_trial_weights(
    row: Mapping[str, object], line: int, modes: tuple[ModeSpec, ...]
) -> tuple[str, tuple[tuple[str, EvidenceWeights], ...]]:
    if not isinstance(row, Mapping) or set(row) != {'trial_id', 'mode_weights'}:
        raise ValueError(f'trial row {line} must contain trial_id and mode_weights')
    trial_id = row['trial_id']
    if not isinstance(trial_id, str) or not trial_id.strip():
        raise ValueError(f'trial row {line} requires a nonempty trial_id')
    submitted = row['mode_weights']
    if not isinstance(submitted, list):
        raise ValueError(f'trial {trial_id!r}: mode_weights must be an array')
    expected = {mode.name for mode in modes}
    entries: dict[str, EvidenceWeights] = {}
    for position, item in enumerate(submitted, start=1):
        if not isinstance(item, Mapping) or set(item) != {'mode', 'normal', 'defect'}:
            raise ValueError(f'trial {trial_id!r}: mode weight row {position} is invalid')
        name = item['mode']
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f'trial {trial_id!r}: mode weight row {position} requires a mode name')
        if name not in expected:
            raise ValueError(f'trial {trial_id!r}: unknown mode {name!r}')
        if name in entries:
            raise ValueError(f'trial {trial_id!r}: duplicate weights for mode {name!r}')
        entries[name] = EvidenceWeights(
            _parse_mode_weight(item['normal'], 'normal', f'trial {trial_id!r}'),
            _parse_mode_weight(item['defect'], 'defect', f'trial {trial_id!r}'),
        )
    missing = expected - set(entries)
    if missing:
        raise ValueError(f'trial {trial_id!r}: missing weights for modes {sorted(missing)!r}')
    return trial_id, tuple((mode.name, entries[mode.name]) for mode in modes)


def freeze_modes(
    repository: ObservationRepository,
    manifest: ModeManifest | Mapping[str, object],
    station_id: str,
    rows: Iterable[Mapping[str, object]],
) -> int:
    parsed = parse_mode_manifest(manifest)
    capacity = parsed.capacity_for(station_id)
    try:
        submitted = tuple(rows)
    except TypeError as exc:
        raise ValueError('trial evidence must be a collection of rows') from exc
    trials = []
    seen = set()
    for line, row in enumerate(submitted, start=1):
        trial_id, weights = _parse_trial_weights(row, line, parsed.modes)
        if trial_id in seen:
            raise ValueError(f'duplicate trial {trial_id!r}')
        seen.add(trial_id)
        trials.append(ModeTrialEvidence(trial_id, weights))
    if len(trials) != capacity:
        raise ValueError(f'trial coverage is {len(trials)}, expected station capacity {capacity}')
    return repository.save_frozen_modes(parsed, station_id, capacity, tuple(trials))


def read_frozen_mode_revision(
    repository: ObservationRepository, revision_id: int
) -> FrozenModeRevision:
    if type(revision_id) is not int or revision_id <= 0:
        raise ValueError('revision_id must be a positive integer')
    stored = repository.load_frozen_modes(revision_id)
    if stored is None:
        raise LookupError(f'unknown revision {revision_id}')
    batch_id, station_id, capacity, manifest, trials = stored
    return FrozenModeRevision(
        revision_id,
        batch_id,
        station_id,
        capacity,
        manifest,
        tuple(ModeTrialEvidence(trial_id, weights) for trial_id, weights in trials),
    )
