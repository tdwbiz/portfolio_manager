"""One-time management command to merge auto-created duplicate RBC accounts
into the canonical accounts given via --canonical (comma list of account pks).

Safe to run multiple times — skips accounts that are already canonical or
have no linked data.
"""
import re
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Account, RbcMonthly, RbcAnnual

logger = logging.getLogger(__name__)


def _normalize(raw: str) -> str:
    """Strip non-digits and return the first 8 digits."""
    digits = re.sub(r'\D', '', raw)
    return digits[:8] if len(digits) >= 8 else digits


def _find_target(dupe: Account, canonical_map: dict) -> Account | None:
    """Find the canonical account this duplicate should merge into."""
    # 1. Normalize the account_id directly.
    norm = _normalize(dupe.account_id)
    if norm in canonical_map:
        return canonical_map[norm]

    # 2. For FALLBACK-XXXX — the 4-digit suffix appears at the end of a canonical id.
    if dupe.account_id.startswith('FALLBACK-'):
        suffix = dupe.account_id.split('-', 1)[1]  # e.g. '2259'
        for cid, acct in canonical_map.items():
            if cid.endswith(suffix):
                return acct

    # 3. Look at the account_number stored in linked RbcMonthly / RbcAnnual records.
    sample = (
        RbcMonthly.objects.filter(account=dupe).values_list('account_number', flat=True).first()
        or RbcAnnual.objects.filter(account=dupe).values_list('account_number', flat=True).first()
    )
    if sample:
        norm2 = _normalize(sample)
        if norm2 in canonical_map:
            return canonical_map[norm2]

    return None


class Command(BaseCommand):
    help = 'Merge auto-created duplicate RBC accounts into the canonical accounts given via --canonical.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be done without making any DB changes.',
        )
        parser.add_argument(
            '--canonical',
            required=True,
            help='Comma-separated list of canonical account pks (core_account.id).',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN — no changes will be made.\n'))

        try:
            canonical_pks = [
                int(p.strip()) for p in options['canonical'].split(',') if p.strip()
            ]
        except ValueError:
            self.stderr.write(self.style.ERROR('--canonical must be a comma list of account pks.'))
            return
        if not canonical_pks:
            self.stderr.write(self.style.ERROR('--canonical is empty.'))
            return

        canonical_accounts = list(Account.objects.filter(pk__in=canonical_pks))
        found_pks = {a.pk for a in canonical_accounts}
        missing = set(canonical_pks) - found_pks
        if missing:
            self.stderr.write(self.style.ERROR(
                f'Only found {len(found_pks)}/{len(canonical_pks)} canonical accounts. Missing pks: {sorted(missing)}'
            ))
            return

        # Keyed by account_id so _find_target can match normalised numbers.
        canonical_map = {a.account_id: a for a in canonical_accounts}

        self.stdout.write(f'Canonical accounts: {len(canonical_map)}\n')

        duplicates = Account.objects.filter(
            broker='RBC',
        ).exclude(pk__in=canonical_pks)

        self.stdout.write(f'Duplicate accounts to process: {duplicates.count()}\n\n')

        total_monthly = 0
        total_annual = 0
        merged = 0
        skipped = []

        with transaction.atomic():
            for dupe in duplicates:
                target = _find_target(dupe, canonical_map)

                monthly_count = RbcMonthly.objects.filter(account=dupe).count()
                annual_count = RbcAnnual.objects.filter(account=dupe).count()

                if target is None:
                    msg = (
                        f'  SKIP  id={dupe.id:>4}  {dupe.account_id!r:<30}  '
                        f'monthly={monthly_count}  annual={annual_count}  '
                        f'(cannot determine target)'
                    )
                    self.stdout.write(self.style.WARNING(msg))
                    skipped.append(dupe.account_id)
                    continue

                msg = (
                    f'  MERGE id={dupe.id:>4}  {dupe.account_id!r:<30}  '
                    f'-> {target.account_id} ({target.name})  '
                    f'monthly={monthly_count}  annual={annual_count}'
                )
                self.stdout.write(msg)

                if not dry_run:
                    # Monthly: delete true duplicates (same account+date+currency+file), reassign rest
                    for stmt in RbcMonthly.objects.filter(account=dupe):
                        already_exists = RbcMonthly.objects.filter(
                            account=target,
                            statement_date=stmt.statement_date,
                            currency=stmt.currency,
                            file_name=stmt.file_name,
                        ).exists()
                        if already_exists:
                            stmt.delete()
                        else:
                            stmt.account = target
                            stmt.save(update_fields=['account'])

                    # Annual: delete true duplicates (same account+year+currency), reassign rest
                    for rec in RbcAnnual.objects.filter(account=dupe):
                        already_exists = RbcAnnual.objects.filter(
                            account=target,
                            report_year=rec.report_year,
                            currency=rec.currency,
                        ).exists()
                        if already_exists:
                            rec.delete()
                        else:
                            rec.account = target
                            rec.save(update_fields=['account'])

                    dupe.delete()

                total_monthly += monthly_count
                total_annual += annual_count
                merged += 1

            if dry_run:
                # Roll back so nothing is actually committed
                transaction.set_rollback(True)

        self.stdout.write('\n')
        self.stdout.write(self.style.SUCCESS(
            f'{"Would merge" if dry_run else "Merged"} {merged} duplicate accounts — '
            f'{total_monthly} monthly + {total_annual} annual records reassigned.'
        ))
        if skipped:
            self.stdout.write(self.style.WARNING(
                f'Skipped {len(skipped)} accounts (could not determine target): {skipped}'
            ))
