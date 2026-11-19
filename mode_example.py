import json
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from campaign.allocation import StationAllocation
from campaign.manifest import register_modes
from campaign.repository import CampaignRepository
from campaign.reveal import reveal
from observations.repository import ObservationRepository
from observations.revision import freeze_modes
from receipt.intake import receive_modes
from receipt.repository import ReceiptRepository
from review.publication import publish_modes
from review.readback import fetch_modes
from review.repository import ReportRepository
from review.thresholds import Threshold
from station.issuance import issue_modes
from station.repository import StationRepository
from storage import open_database


def station_piece(path, manifest, station_id, rows):
    with closing(open_database(path)) as db:
        observations, station = ObservationRepository(db), StationRepository(db)
        observations.initialize()
        station.initialize()
        revision_id = freeze_modes(observations, manifest, station_id, rows)
        return issue_modes(observations, station, revision_id)


def saved_fields(publication):
    report, receipt = publication
    return {
        "report_id": report.report_id,
        "receipt": {"receipt_id": receipt.receipt_id, "report_id": receipt.report_id},
        "batch_id": report.batch_id,
        "manifest": report.manifest,
        "defect_count": report.defect_count,
        "receipt_ids": list(report.receipt_ids),
        "thresholds": [{"station_id": q.station_id, "minimum": q.minimum} for q in report.thresholds],
        "probabilities": [str(p) for p in report.probabilities.as_tuple()],
        "mode_posterior": [str(p) for p in report.mode_posterior],
    }


def main():
    fixture = json.loads((Path(__file__).parent / "samples" / "shared-modes.json").read_text(encoding="utf-8"))
    batch_id = fixture["batch_id"]
    allocations = tuple(StationAllocation(**row) for row in fixture["allocations"])
    queries = tuple(Threshold(**row) for row in fixture["thresholds"])
    with TemporaryDirectory(prefix="mode-review-") as directory:
        root = Path(directory)
        center = root / "center.sqlite"
        with closing(open_database(center)) as db:
            campaign, receipts, reports = CampaignRepository(db), ReceiptRepository(db), ReportRepository(db)
            for repo in (campaign, receipts, reports):
                repo.initialize()
            manifest = register_modes(campaign, batch_id, allocations, fixture["sealed_count"], fixture["modes"])
            manifest = json.loads(json.dumps(manifest))
            envelopes = {
                site: station_piece(root / f"{site}.sqlite", manifest, site, rows)
                for site, rows in fixture["trials"].items()
            }
            ids = {site: receive_modes(campaign, receipts, batch_id, text) for site, text in envelopes.items()}
            reveal(campaign, batch_id)
            first = publish_modes(campaign, receipts, reports, batch_id, (ids["B"], ids["A"]), queries)
            correction = fixture["correction"]
            amended = station_piece(root / "A.sqlite", manifest, "A", correction["trials"])
            new_id = receive_modes(campaign, receipts, batch_id, amended)
            second = publish_modes(campaign, receipts, reports, batch_id, (ids["B"], new_id), queries)

        original, revised = saved_fields(first), saved_fields(second)
        for actual, expected in ((original, fixture["expected"]), (revised, correction["expected"])):
            if any(actual[key] != value for key, value in expected.items()):
                raise RuntimeError(f"unexpected probabilities in report {actual}")
        with closing(open_database(center)) as db:
            reports = ReportRepository(db)
            reports.initialize()
            reopened = saved_fields(fetch_modes(reports, original["report_id"]))
        if reopened != original:
            raise RuntimeError("reopened report changed")
        print(json.dumps({"reopened": reopened, "corrected": revised}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
