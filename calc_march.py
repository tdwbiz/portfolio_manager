from decimal import Decimal

cad_end = Decimal('117581.70')
cad_positions = Decimal('117572.56')

# CAD April transactions:
# 2026-04-24 DIST 306.71
# 2026-04-27 DIV 306.71
# 2026-04-28 BUY -1250.00
cad_net_april = Decimal('306.71') + Decimal('306.71') + Decimal('-1250.00')
print(f'CAD April net flow: {cad_net_april:.2f}')

# CAD March transactions:
# 2026-03-27 DIST 282.31
# 2026-03-31 INT 0
cad_net_march = Decimal('282.31') + Decimal('0.00')
print(f'CAD March net flow: {cad_net_march:.2f}')

combined = cad_net_march + cad_net_april
print(f'Combined net flow (March + April): {combined:.2f}')

cad_march31_begin = cad_end - combined
print(f'CAD March 31 beginning (computed): {cad_march31_begin:.2f}')

pdf_march31_opening = Decimal('670.12') + cad_positions
print(f'PDF March 31 opening total: {pdf_march31_opening:.2f}')

diff = cad_march31_begin - pdf_march31_opening
print(f'Difference: {diff:.2f}')

# USD
usd_end = Decimal('157174.08')
usd_positions = Decimal('156365.17')

# USD April: no activity
usd_net_april = Decimal('0')
print()
print(f'USD April net flow: {usd_net_april:.2f}')

# USD March:
# 2026-03-05 DIV 38.05
# 2026-03-31 INT 0
# 2026-04-01 DIV 84.91
# 2026-04-02 DIV 86.34
# 2026-04-07 DIV 40.89
# 2026-04-30 DIV 402.80

usd_net_march = Decimal('38.05') + Decimal('0.00')
usd_net_april = Decimal('84.91') + Decimal('86.34') + Decimal('40.89') + Decimal('402.80')
combined_usd = usd_net_march + usd_net_april
print(f'USD March net flow: {usd_net_march:.2f}')
print(f'USD April net flow: {usd_net_april:.2f}')
print(f'USD combined net flow: {combined_usd:.2f}')

usd_march31_begin = usd_end - combined_usd
print(f'USD March 31 beginning (computed): {usd_march31_begin:.2f}')

pdf_usd_march31_opening = Decimal('392.79') + usd_positions
print(f'PDF March 31 opening total: {pdf_usd_march31_opening:.2f}')

diff_usd = usd_march31_begin - pdf_usd_march31_opening
print(f'Difference: {diff_usd:.2f}')
