"""One-time management command to merge auto-created duplicate RBC accounts
into the canonical accounts given via --canonical (comma list of account pks).

Safe to run multiple times — skips accounts that are already canonical or
have no linked data.
"""
import logging

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Account, RbcMonthly, RbcAnnual
from core.utils.account_fingerprint import lookup_account

logger = logging.getLogger(__name__)


def _find_target(dupe: Account, canonical_map: dict) -> Account | None:
    """Find the canonical account this duplicate should merge into.

    Matches by name, then by a unique last-4, then by the fingerprint of an
    account_number stored on linked RbcMonthly / RbcAnnual records.
    """
    # 1. Exact name match.
    if dupe.name in canonical_map:
        return canonical_map[dupe.name]

    # 2. Unique last-4 match among the canonical accounts.
    if dupe.last_4:
        matches = [a for a in canonical_map.values() if a.last_4 == dupe.last_4]
        if len(matches) == 1:
            return matches[0]

    # 3. Look at the account_number stored in linked RbcMonthly / RbcAnnual records.
    sample = (
        RbcMonthly.objects.filter(account=dupe).values_list('account_number', flat=True).first()
        or RbcAnnual.objects.filter(account=dupe).values_list('account_number', flat=True).first()
    )
    if sample:
        acct = lookup_account(sample)
        if acct is not None and any(a.pk == acct.pk for a in canonical_map.values()):
            return acct

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

        # Keyed by name so _find_target can match duplicates.
        canonical_map = {a.name: a for a in canonical_accounts}

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
                        f'  SKIP  id={dupe.id:>4}  {dupe.name!r:<30}  '
                        f'monthly={monthly_count}  annual={annual_count}  '
                        f'(cannot determine target)'
                    )
                    self.stdout.write(self.style.WARNING(msg))
                    skipped.append(dupe.name)
                    continue

                msg = (
                    f'  MERGE id={dupe.id:>4}  {dupe.name!r:<30}  '
                    f'-> {target.name} (last_4={target.last_4})  '
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
