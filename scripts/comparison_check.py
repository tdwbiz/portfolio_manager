"""Compare CSV prediction counts against actual DB transaction counts."""

import csv
import sys

sys.path.insert(0, '.')

import pyodbc

DB_CONFIG = {
    'driver': '{ODBC Driver 18 for SQL Server}',
    'server': '.',
    'database': 'PortfolioDEV',
    'trusted_connection': True,
}

conn = pyodbc.connect(
    'DRIVER={ODBC Driver 18 for SQL Server};SERVER=.;DATABASE=PortfolioDEV;TRUSTED_CONNECTION=yes;Connection Timeout=30;TrustServerCertificate=yes',
    autocommit=True,
)

# Load CSV predictions
with open('docs/reconciliation/CSV_Prediction_Counts.csv', 'r', newline='') as f:
    csv_data = {row['Filename']: int(row['Activity_Count']) for row in csv.DictReader(f)}

# Query actual DB counts per BrokerStatement file
cursor = conn.cursor()
cursor.execute('''
    SELECT bs.file_name,
           COUNT(t.id) AS actual_count,
           SUM(t.amount_cad) AS value_sum
    FROM [PortfolioDEV].[dbo].[core_brokerstatement] bs
    LEFT JOIN [PortfolioDEV].[dbo].[core_transaction] t
        ON t.broker_statement_id = bs.id
    GROUP BY bs.file_name
    ORDER BY bs.file_name
''')

print(f"{'Filename':<55} {'CSV Predicted':>12} {'Actual DB':>12} {'Diff':>10} {'Value_Sum':>15}")
print('-' * 100)

mismatches = 0
total_csv = 0
for r in cursor.fetchall():
    fname, predicted, actual, val = r
    total_csv += predicted
    diff = actual - predicted
    val_str = f"{val:,.2f}" if val else "0.00"
    if predicted != actual or diff != 0:
        mismatches += 1
    print(f'{fname:<55} {predicted:>12} {actual:>12} {diff:>10} {val_str:>15}')

conn.close()

print('-' * 100)
print(f"Mismatches: {mismatches}")
print(f"Total CSV predicted lines: {total_csv}")
