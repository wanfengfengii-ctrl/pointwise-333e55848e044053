from collections.abc import Iterable

from campaign.manifest import read_manifest
from campaign.modes import parse_mode_manifest
from campaign.repository import CampaignRepository
from campaign.reveal import require_revealed_count
from receipt.repository import ReceiptRepository
from receipt.selection import select_pieces
from review.modes import evaluate_modes, select_mode_pieces
from review.repository import (
    PublicationReceipt,
    PublishedModeReport,
    PublishedReport,
    ReportRepository,
)
from review.thresholds import (
    RiskProbabilities,
    Threshold,
    evaluate_thresholds,
    validate_thresholds,
)


def publish(
    campaign: CampaignRepository,
    receipts: ReceiptRepository,
    reports: ReportRepository,
    batch_id: str,
    receipt_ids: Iterable[int],
    thresholds: Iterable[Threshold],
) -> tuple[PublishedReport, PublicationReceipt]:
    manifest = read_manifest(campaign, batch_id)
    if campaign.load_modes(manifest.batch_id) is not None:
        raise ValueError(f'batch {batch_id!r} is a shared-mode batch')
    defect_count = require_revealed_count(campaign, manifest.batch_id)
    selection = select_pieces(receipts, manifest, receipt_ids)
    queries = validate_thresholds(selection, thresholds)
    probabilities = evaluate_thresholds(selection, defect_count, queries)
    return reports.save_publication(
        selection.batch_id, defect_count, selection.receipt_ids, queries, probabilities
    )


def publish_modes(
    campaign: CampaignRepository,
    receipts: ReceiptRepository,
    reports: ReportRepository,
    batch_id: str,
    receipt_ids: Iterable[int],
    thresholds: Iterable[Threshold],
) -> tuple[PublishedModeReport, PublicationReceipt]:
    stored = campaign.load_mode_manifest(batch_id)
    if stored is None:
        if campaign.load_allocations(batch_id) is not None:
            raise ValueError(f'batch {batch_id!r} is not a shared-mode batch')
        raise LookupError(f'unknown batch {batch_id!r}')
    manifest = parse_mode_manifest(stored)
    defect_count = require_revealed_count(campaign, manifest.batch_id)
    selection = select_mode_pieces(receipts, manifest, receipt_ids)
    queries = validate_thresholds(selection, thresholds)
    risk = evaluate_modes(selection, defect_count, queries)
    return reports.save_mode_publication(
        manifest.to_dict(),
        defect_count,
        selection.receipt_ids,
        queries,
        RiskProbabilities(*risk.probabilities),
        risk.posterior,
    )
