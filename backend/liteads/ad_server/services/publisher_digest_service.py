"""Publisher Weekly Placement Digest Service (Phase 6 WU5).

Generates and delivers weekly performance summaries to publishers,
highlighting their top- and bottom-performing placements based on
PlacementAnalyzerService analysis over a rolling 30-day window.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import html
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from liteads.ad_server.services.placement_analyzer import (
    DEFAULT_WINDOW_DAYS,
    MIN_IMPRESSIONS_FOR_VERDICT,
    PlacementAnalysisResult,
    PlacementAnalyzerService,
    VERDICT_GREAT,
    VERDICT_HEALTHY,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_LOW_VISIBILITY,
)
from liteads.common.cache import redis_client
from liteads.common.email import EmailDeliveryError, send_email
from liteads.common.logger import get_logger
from liteads.models.auth import User
from liteads.models.base import CreativeType, Status
from liteads.models.publisher import Publisher

logger = get_logger(__name__)

# Batch status contract constants
BATCH_STATUS_NO_OP = "no_op"              # No eligible publishers or no active publishers requiring emails
BATCH_STATUS_SUCCESS = "success"          # Complete delivery success (all targeted publishers delivered)
BATCH_STATUS_PARTIAL = "partial_success"  # Partial delivery success (some delivered, some failed)
BATCH_STATUS_FAILURE = "failure"          # Complete delivery failure (all targeted publishers failed)

DIGEST_DELIVERED_KEY_PREFIX = "digest:delivered:"

# Format name mapping
FORMAT_NAMES = {
    CreativeType.BANNER: "Banner",
    CreativeType.NATIVE: "Native",
    CreativeType.VIDEO: "Video",
    CreativeType.INTERSTITIAL: "Interstitial",
}


@dataclass(frozen=True)
class DigestRanking:
    """Categorized ranking of a publisher's placements."""
    top_placements: list[PlacementAnalysisResult]
    bottom_placements: list[PlacementAnalysisResult]  # Strictly low_visibility placements needing attention
    additional_placements: list[PlacementAnalysisResult]  # Other eligible healthy/great placements
    insufficient_data_placements: list[PlacementAnalysisResult]
    total_impressions: int
    total_clicks: int
    total_earnings: float
    average_ctr: float


class PublisherDigestService:
    """Builds and delivers placement performance digests to publishers."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.analyzer = PlacementAnalyzerService(session)

    @staticmethod
    def is_valid_email(email_str: str | None) -> bool:
        """Validate recipient email address syntax safely."""
        if not email_str or not isinstance(email_str, str):
            return False
        email_str = email_str.strip()
        if len(email_str) < 5 or "@" not in email_str:
            return False
        # Basic regex check for user@domain.tld
        pattern = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
        return bool(re.match(pattern, email_str))

    @staticmethod
    def categorize_and_rank_placements(
        analyses: list[PlacementAnalysisResult],
    ) -> DigestRanking:
        """
        Categorize and deterministically rank placements into top and bottom performers.

        Rules:
        - Only placements with >= 100 impressions (eligible for verdict) can be ranked as top or bottom.
        - Placements with < 100 impressions are placed into insufficient_data_placements.
        - Ranking score:
            Primary: Verdict weight (great = 2, healthy = 1, low_visibility = 0).
            Secondary: CTR descending.
            Tertiary: eCPM descending.
            Tie-break: slot_id ascending.
        - Top performers: up to 3 highest-ranking eligible placements (excluding low_visibility).
        - Placements needing attention: strictly eligible placements with low_visibility (up to 3).
        - Additional active placements: other eligible healthy/great placements not in top 3.
        """
        eligible = [p for p in analyses if p.impressions >= MIN_IMPRESSIONS_FOR_VERDICT and p.verdict != VERDICT_INSUFFICIENT_DATA]
        insufficient = [p for p in analyses if p.impressions < MIN_IMPRESSIONS_FOR_VERDICT or p.verdict == VERDICT_INSUFFICIENT_DATA]

        # Calculate totals
        total_impressions = sum(p.impressions for p in analyses)
        total_clicks = sum(p.clicks for p in analyses)
        total_earnings = round(sum(p.earnings for p in analyses), 4)
        average_ctr = round(float(total_clicks) / total_impressions, 4) if total_impressions > 0 else 0.0

        if not eligible:
            return DigestRanking(
                top_placements=[],
                bottom_placements=[],
                additional_placements=[],
                insufficient_data_placements=insufficient,
                total_impressions=total_impressions,
                total_clicks=total_clicks,
                total_earnings=total_earnings,
                average_ctr=average_ctr,
            )

        # Eligible placements candidates for top performers (exclude low_visibility)
        eligible_for_top = [p for p in eligible if p.verdict != VERDICT_LOW_VISIBILITY]

        def rank_key_desc(p: PlacementAnalysisResult):
            v_weight = 2 if p.verdict == VERDICT_GREAT else (1 if p.verdict == VERDICT_HEALTHY else 0)
            return (-v_weight, -p.ctr, -p.ecpm, str(p.slot_id))

        sorted_eligible_desc = sorted(eligible_for_top, key=rank_key_desc)

        # Up to top 3 eligible top performers
        top_count = min(3, len(sorted_eligible_desc))
        top_placements = sorted_eligible_desc[:top_count]
        top_slot_ids = {t.slot_id for t in top_placements}

        # Placements needing attention: strictly low_visibility placements
        low_visibility_candidates = [p for p in eligible if p.verdict == VERDICT_LOW_VISIBILITY]
        def rank_key_asc(p: PlacementAnalysisResult):
            return (p.ctr, p.ecpm, str(p.slot_id))

        sorted_low = sorted(low_visibility_candidates, key=rank_key_asc)
        bottom_placements = sorted_low[:min(3, len(sorted_low))]

        # Additional active placements: other eligible healthy/great placements not in top 3
        additional_candidates = [p for p in eligible_for_top if p.slot_id not in top_slot_ids]
        additional_placements = sorted(additional_candidates, key=rank_key_desc)

        return DigestRanking(
            top_placements=top_placements,
            bottom_placements=bottom_placements,
            additional_placements=additional_placements,
            insufficient_data_placements=insufficient,
            total_impressions=total_impressions,
            total_clicks=total_clicks,
            total_earnings=total_earnings,
            average_ctr=average_ctr,
        )

    def build_digest_content(
        self,
        publisher: Publisher,
        analyses: list[PlacementAnalysisResult],
        start_date: str,
        end_date: str,
    ) -> tuple[str, str]:
        """
        Build clean plain-text and HTML versions of the weekly digest.
        Ensures tenant isolation and strips private platform/competitor data.
        """
        ranking = self.categorize_and_rank_placements(analyses)
        pub_name = html.escape(publisher.name)

        # --- Plain Text Version ---
        text_lines = [
            f"RivinityAds — Weekly Placement Digest for {publisher.name}",
            f"Reporting Window: {start_date} to {end_date} (Rolling 30-Day Window)",
            "=" * 65,
            "",
            "SUMMARY OVERVIEW:",
            f"• Total Tracked Placements: {len(analyses)}",
            f"• Total Impressions: {ranking.total_impressions:,}",
            f"• Total Clicks: {ranking.total_clicks:,}",
            f"• Average CTR: {ranking.average_ctr * 100:.2f}%",
            f"• Estimated Earnings: ₹{ranking.total_earnings:,.2f}",
            "",
        ]

        if not analyses:
            text_lines.extend([
                "NO ACTIVE PLACEMENTS RECORDED",
                "No placement statistics were recorded for your inventory during this reporting period.",
                "To start serving ads and earning revenue, ensure your ad units are integrated and active.",
                "",
            ])
        else:
            if ranking.top_placements:
                text_lines.append("TOP PERFORMING PLACEMENTS:")
                for p in ranking.top_placements:
                    fmt_name = FORMAT_NAMES.get(p.format, f"Format {p.format}")
                    med_ctr = p.benchmark.get("median_ctr")
                    bench_str = f" (Platform Median: {med_ctr*100:.2f}%)" if med_ctr is not None else ""
                    text_lines.extend([
                        f"• {p.slot_name} ({p.slot_id}) - {fmt_name}",
                        f"  Verdict: {p.verdict.upper()} | CTR: {p.ctr*100:.2f}%{bench_str} | eCPM: ₹{p.ecpm:.2f}",
                        f"  Impressions: {p.impressions:,} | Recommendation: {p.recommendation}",
                        "",
                    ])

            if ranking.bottom_placements:
                text_lines.append("PLACEMENTS NEEDING ATTENTION:")
                for p in ranking.bottom_placements:
                    fmt_name = FORMAT_NAMES.get(p.format, f"Format {p.format}")
                    text_lines.extend([
                        f"• {p.slot_name} ({p.slot_id}) - {fmt_name}",
                        f"  Verdict: {p.verdict.upper()} | CTR: {p.ctr*100:.2f}% | eCPM: ₹{p.ecpm:.2f}",
                        f"  Impressions: {p.impressions:,} | Recommendation: {p.recommendation}",
                        "",
                    ])

            if ranking.additional_placements:
                text_lines.append("ADDITIONAL ACTIVE PLACEMENTS:")
                for p in ranking.additional_placements:
                    fmt_name = FORMAT_NAMES.get(p.format, f"Format {p.format}")
                    text_lines.extend([
                        f"• {p.slot_name} ({p.slot_id}) - {fmt_name}",
                        f"  Verdict: {p.verdict.upper()} | CTR: {p.ctr*100:.2f}% | eCPM: ₹{p.ecpm:.2f}",
                        f"  Impressions: {p.impressions:,} | Recommendation: {p.recommendation}",
                        "",
                    ])

            if ranking.insufficient_data_placements:
                text_lines.append("PLACEMENTS IN DATA COLLECTION (under 100 impressions):")
                for p in ranking.insufficient_data_placements:
                    fmt_name = FORMAT_NAMES.get(p.format, f"Format {p.format}")
                    text_lines.extend([
                        f"• {p.slot_name} ({p.slot_id}) - {fmt_name}: {p.impressions} impressions recorded.",
                        f"  Note: Minimum 100 impressions required before performance health classification.",
                        "",
                    ])

        text_lines.extend([
            "=" * 65,
            "Notice: Metrics reflect historical delivery over the stated rolling 30-day period.",
            "Historical performance does not guarantee future results.",
            "This automated message is intended solely for the registered publisher.",
        ])

        text_content = "\n".join(text_lines)

        # --- HTML Version ---
        html_blocks = [
            f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; line-height: 1.5; color: #1e293b; background-color: #f8fafc; margin: 0; padding: 24px; }}
.container {{ max-width: 680px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
.header {{ background: #0f172a; color: #ffffff; padding: 24px 32px; }}
.header h1 {{ margin: 0 0 6px 0; font-size: 20px; font-weight: 600; letter-spacing: -0.025em; }}
.header p {{ margin: 0; font-size: 13px; color: #94a3b8; }}
.content {{ padding: 32px; }}
.summary-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 28px; }}
.metric-box {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; text-align: center; }}
.metric-val {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-bottom: 4px; }}
.metric-lbl {{ font-size: 11px; text-transform: uppercase; color: #64748b; font-weight: 600; letter-spacing: 0.05em; }}
h2 {{ font-size: 15px; text-transform: uppercase; letter-spacing: 0.05em; color: #334155; margin: 24px 0 12px 0; border-bottom: 1px solid #e2e8f0; padding-bottom: 6px; }}
.slot-card {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 14px 16px; margin-bottom: 10px; }}
.badge {{ display: inline-block; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 9999px; text-transform: uppercase; }}
.badge-great {{ background: #dcfce7; color: #166534; }}
.badge-healthy {{ background: #e0f2fe; color: #0369a1; }}
.badge-low {{ background: #fee2e2; color: #991b1b; }}
.badge-info {{ background: #f1f5f9; color: #475569; }}
.slot-title {{ font-weight: 600; font-size: 14px; margin-bottom: 4px; }}
.slot-meta {{ font-size: 12px; color: #64748b; margin-bottom: 6px; }}
.slot-rec {{ font-size: 12px; color: #0f172a; font-weight: 500; background: #f8fafc; padding: 6px 10px; border-radius: 4px; }}
.footer {{ background: #f8fafc; border-top: 1px solid #e2e8f0; padding: 16px 32px; font-size: 11px; color: #64748b; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>RivinityAds Weekly Placement Digest</h1>
    <p>Publisher: <strong>{pub_name}</strong> &bull; Period: {start_date} to {end_date} (Rolling 30 Days)</p>
  </div>
  <div class="content">
    <div class="summary-grid">
      <div class="metric-box"><div class="metric-val">{len(analyses)}</div><div class="metric-lbl">Placements</div></div>
      <div class="metric-box"><div class="metric-val">{ranking.total_impressions:,}</div><div class="metric-lbl">Impressions</div></div>
      <div class="metric-box"><div class="metric-val">{ranking.average_ctr*100:.2f}%</div><div class="metric-lbl">Avg CTR</div></div>
      <div class="metric-box"><div class="metric-val">₹{ranking.total_earnings:,.2f}</div><div class="metric-lbl">Est. Earnings</div></div>
    </div>
"""
        ]

        if not analyses:
            html_blocks.append(
                """<div style="text-align: center; padding: 32px 16px; color: #64748b;">
                   <p style="font-weight: 600; font-size: 15px; margin-bottom: 6px; color: #0f172a;">No Placement Activity Recorded</p>
                   <p style="font-size: 13px; margin: 0;">No impression data was recorded for your ad placements over this 30-day period. Ensure your ad units are correctly embedded and live.</p>
                   </div>"""
            )
        else:
            if ranking.top_placements:
                html_blocks.append("<h2>Top Performing Placements</h2>")
                for p in ranking.top_placements:
                    fmt_name = html.escape(FORMAT_NAMES.get(p.format, f"Format {p.format}"))
                    badge_class = "badge-great" if p.verdict == VERDICT_GREAT else "badge-healthy"
                    med_ctr = p.benchmark.get("median_ctr")
                    bench_note = f" &bull; Platform Median: {med_ctr*100:.2f}%" if med_ctr is not None else ""
                    html_blocks.append(f"""
<div class="slot-card">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
    <div class="slot-title">{html.escape(p.slot_name)} <span style="font-size: 12px; font-weight: normal; color: #64748b;">({html.escape(p.slot_id)})</span></div>
    <span class="badge {badge_class}">{html.escape(p.verdict)}</span>
  </div>
  <div class="slot-meta">{fmt_name} &bull; CTR: <strong>{p.ctr*100:.2f}%</strong>{bench_note} &bull; eCPM: <strong>₹{p.ecpm:.2f}</strong> &bull; Impressions: {p.impressions:,}</div>
  <div class="slot-rec">Recommendation: {html.escape(p.recommendation)}</div>
</div>""")

            if ranking.bottom_placements:
                html_blocks.append("<h2>Placements Needing Attention</h2>")
                for p in ranking.bottom_placements:
                    fmt_name = html.escape(FORMAT_NAMES.get(p.format, f"Format {p.format}"))
                    badge_class = "badge-low"
                    html_blocks.append(f"""
<div class="slot-card">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
    <div class="slot-title">{html.escape(p.slot_name)} <span style="font-size: 12px; font-weight: normal; color: #64748b;">({html.escape(p.slot_id)})</span></div>
    <span class="badge {badge_class}">{html.escape(p.verdict)}</span>
  </div>
  <div class="slot-meta">{fmt_name} &bull; CTR: <strong>{p.ctr*100:.2f}%</strong> &bull; eCPM: <strong>₹{p.ecpm:.2f}</strong> &bull; Impressions: {p.impressions:,}</div>
  <div class="slot-rec">Recommendation: {html.escape(p.recommendation)}</div>
</div>""")

            if ranking.additional_placements:
                html_blocks.append("<h2>Additional Active Placements</h2>")
                for p in ranking.additional_placements:
                    fmt_name = html.escape(FORMAT_NAMES.get(p.format, f"Format {p.format}"))
                    badge_class = "badge-great" if p.verdict == VERDICT_GREAT else "badge-healthy"
                    html_blocks.append(f"""
<div class="slot-card">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
    <div class="slot-title">{html.escape(p.slot_name)} <span style="font-size: 12px; font-weight: normal; color: #64748b;">({html.escape(p.slot_id)})</span></div>
    <span class="badge {badge_class}">{html.escape(p.verdict)}</span>
  </div>
  <div class="slot-meta">{fmt_name} &bull; CTR: <strong>{p.ctr*100:.2f}%</strong> &bull; eCPM: <strong>₹{p.ecpm:.2f}</strong> &bull; Impressions: {p.impressions:,}</div>
  <div class="slot-rec">Recommendation: {html.escape(p.recommendation)}</div>
</div>""")

            if ranking.insufficient_data_placements:
                html_blocks.append("<h2>Placements in Learning Phase (&lt; 100 Impressions)</h2>")
                for p in ranking.insufficient_data_placements:
                    fmt_name = html.escape(FORMAT_NAMES.get(p.format, f"Format {p.format}"))
                    html_blocks.append(f"""
<div class="slot-card" style="background: #fafafa;">
  <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
    <div class="slot-title">{html.escape(p.slot_name)} <span style="font-size: 12px; font-weight: normal; color: #64748b;">({html.escape(p.slot_id)})</span></div>
    <span class="badge badge-info">Collecting Data</span>
  </div>
  <div class="slot-meta">{fmt_name} &bull; {p.impressions} impressions &bull; Minimum 100 impressions required for health verdict.</div>
</div>""")

        html_blocks.append(
            """  </div>
  <div class="footer">
    Historical 30-day performance data. Does not constitute a financial guarantee.<br>
    RivinityAds Automated Publisher Service.
  </div>
</div>
</body>
</html>"""
        )
        html_content = "\n".join(html_blocks)

        return text_content, html_content

    async def get_authoritative_publisher_email(self, publisher: Publisher) -> str | None:
        """
        Resolve authoritative email address for the publisher.
        Checks Publisher.email first; if missing, falls back to owner User.email.
        """
        if self.is_valid_email(publisher.email):
            return publisher.email.strip()

        if publisher.owner_user_id:
            user = await self.session.get(User, publisher.owner_user_id)
            if user and self.is_valid_email(user.email):
                return user.email.strip()

        return None

    async def send_digest_for_publisher(
        self,
        publisher_id: int,
        window_days: int = DEFAULT_WINDOW_DAYS,
        as_of_date: date | None = None,
    ) -> bool:
        """
        Analyze and deliver the placement digest for a single publisher.
        Returns True on successful delivery, False on skip or failure.
        """
        publisher = await self.session.get(Publisher, publisher_id)
        if not publisher:
            logger.warning("Publisher not found for digest delivery", publisher_id=publisher_id)
            return False

        if publisher.status != Status.ACTIVE:
            logger.info("Skipping digest for inactive publisher", publisher_id=publisher_id)
            return False

        recipient = await self.get_authoritative_publisher_email(publisher)
        if not recipient:
            logger.warning("No valid recipient email address for publisher", publisher_id=publisher_id)
            return False

        # Run placement analysis strictly scoped to this publisher
        end_d = as_of_date or date.today()
        start_d = end_d - timedelta(days=window_days - 1)

        try:
            analyses = await self.analyzer.analyze_publisher_placements(
                publisher_id=publisher_id,
                window_days=window_days,
                as_of_date=end_d,
            )
        except Exception as e:
            logger.error("Placement analysis failed during digest generation", publisher_id=publisher_id, error=str(e))
            return False

        text_content, html_content = self.build_digest_content(
            publisher=publisher,
            analyses=analyses,
            start_date=start_d.isoformat(),
            end_date=end_d.isoformat(),
        )

        subject = f"RivinityAds — Weekly Placement Performance Digest ({end_d.isoformat()})"

        try:
            await send_email(
                to_address=recipient,
                subject=subject,
                text_content=text_content,
                html_content=html_content,
                template_purpose="digest",
                template_data={
                    "publisher_id": publisher_id,
                    "as_of_date": end_d.isoformat(),
                    "placements_count": len(analyses),
                },
            )
            logger.info(
                "Publisher placement digest delivered successfully",
                publisher_id=publisher_id,
                recipient_domain=recipient.split("@")[-1],
                placements_count=len(analyses),
            )
            return True
        except EmailDeliveryError as e:
            logger.error(
                "Email delivery failed for publisher digest",
                publisher_id=publisher_id,
                error=str(e),
            )
            return False
        except Exception as e:
            logger.error(
                "Unexpected failure sending publisher digest",
                publisher_id=publisher_id,
                error=str(e),
            )
            return False

    async def send_all_digests(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        as_of_date: date | None = None,
        publisher_ids: list[int] | None = None,
        skip_already_delivered: bool = True,
        cycle_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Iterate over publishers and deliver weekly digests.
        Isolates per-publisher exceptions so one failure does not break the batch.

        Supports:
        - Optional publisher_ids filter for targeted retry.
        - Stable cycle_id for delivery ledger tracking across calendar dates and restarts.
        - Idempotent delivery tracking via Redis (skips publishers already sent in this cycle).
        - Distinct batch outcome contract (no_op, success, partial_success, failure).
        """
        cycle_date = as_of_date or date.today()

        if publisher_ids is not None:
            target_ids = [int(pid) for pid in publisher_ids]
        else:
            stmt = (
                select(Publisher.id)
                .where(Publisher.status == Status.ACTIVE)
                .order_by(Publisher.id)
            )
            res = await self.session.execute(stmt)
            target_ids = [row[0] for row in res.all()]

        total = len(target_ids)
        if total == 0:
            logger.info("No active publishers found for weekly placement digest")
            return {
                "total": 0,
                "sent": 0,
                "failed_or_skipped": 0,
                "status": BATCH_STATUS_NO_OP,
                "successful_publisher_ids": [],
                "failed_publisher_ids": [],
                "newly_sent_count": 0,
                "previously_sent_count": 0,
                "cycle_id": cycle_id,
            }

        delivered_token = cycle_id or cycle_date.isoformat()
        delivered_key = f"{DIGEST_DELIVERED_KEY_PREFIX}{delivered_token}"
        already_delivered_ids: set[int] = set()

        if skip_already_delivered and redis_client.connected:
            try:
                raw_delivered = await redis_client.smembers(delivered_key)
                already_delivered_ids = {int(x) for x in raw_delivered if str(x).isdigit()}
            except Exception as e:
                logger.warning("Failed to fetch delivered digest publisher IDs from Redis", error=str(e))

        to_send = [pid for pid in target_ids if pid not in already_delivered_ids]
        previously_sent_ids = [pid for pid in target_ids if pid in already_delivered_ids]
        previously_sent_count = len(previously_sent_ids)

        logger.info(
            "Starting weekly placement digest cycle",
            total_publishers=total,
            already_delivered=previously_sent_count,
            to_process=len(to_send),
            cycle_date=cycle_date.isoformat(),
            cycle_id=cycle_id,
        )

        newly_sent_ids: list[int] = []
        failed_ids: list[int] = []

        for pub_id in to_send:
            try:
                success = await self.send_digest_for_publisher(
                    publisher_id=pub_id,
                    window_days=window_days,
                    as_of_date=cycle_date,
                )
                if success:
                    newly_sent_ids.append(pub_id)
                    if redis_client.connected:
                        try:
                            await redis_client.sadd(delivered_key, str(pub_id))
                            # If no cycle_id is provided (standalone ad-hoc run), maintain safe fallback TTL
                            if not cycle_id:
                                await redis_client.expire(delivered_key, 14 * 86400)
                        except Exception as e:
                            logger.warning(
                                "Failed to record delivered publisher in Redis",
                                publisher_id=pub_id,
                                error=str(e),
                            )
                else:
                    failed_ids.append(pub_id)
            except Exception as e:
                logger.error(
                    "Error processing publisher digest in batch",
                    publisher_id=pub_id,
                    error=str(e),
                )
                failed_ids.append(pub_id)

        all_successful_ids = sorted(list(set(previously_sent_ids) | set(newly_sent_ids)))
        sent = len(all_successful_ids)
        failed_or_skipped = len(failed_ids)

        if failed_or_skipped == 0 and sent > 0:
            status = BATCH_STATUS_SUCCESS
        elif sent > 0 and failed_or_skipped > 0:
            status = BATCH_STATUS_PARTIAL
        else:
            status = BATCH_STATUS_FAILURE

        logger.info(
            "Completed weekly placement digest cycle",
            total=total,
            sent=sent,
            newly_sent=len(newly_sent_ids),
            previously_sent=previously_sent_count,
            failed_or_skipped=failed_or_skipped,
            status=status,
            cycle_id=cycle_id,
        )

        return {
            "total": total,
            "sent": sent,
            "failed_or_skipped": failed_or_skipped,
            "status": status,
            "successful_publisher_ids": all_successful_ids,
            "failed_publisher_ids": failed_ids,
            "newly_sent_count": len(newly_sent_ids),
            "previously_sent_count": previously_sent_count,
            "cycle_id": cycle_id,
        }

