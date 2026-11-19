from fractions import Fraction

from observations.modes import FrozenModeRevision


def build_count_evidence(revision) -> tuple[Fraction, ...]:
    coefficients = [Fraction(1)]
    for trial in revision.trials:
        normal, defect = trial.weights.normal, trial.weights.defect
        extended = [Fraction(0)] * (len(coefficients) + 1)
        for count, weight in enumerate(coefficients):
            extended[count] += weight * normal
            extended[count + 1] += weight * defect
        coefficients = extended
    return tuple(coefficients)


def build_mode_count_evidence(
    revision: FrozenModeRevision,
) -> tuple[tuple[str, tuple[Fraction, ...]], ...]:
    evidence = []
    for mode in revision.manifest.modes:
        coefficients = [Fraction(1)]
        for trial in revision.trials:
            weights = trial.weights_for(mode.name)
            normal, defect = weights.normal, weights.defect
            extended = [Fraction(0)] * (len(coefficients) + 1)
            for count, weight in enumerate(coefficients):
                extended[count] += weight * normal
                extended[count + 1] += weight * defect
            coefficients = extended
        evidence.append((mode.name, tuple(coefficients)))
    return tuple(evidence)
