# Code Review Report: Portfolio Manager

**Date:** 2026-06-27
**Reviewer:** Gemma 4

---

## 1. Executive Summary

The **Portfolio Manager** project is a sophisticated, well-architected Django application designed for professional-grade financial tracking and risk analytics. The codebase demonstrates a high level of maturity, particularly in its handling of:
* **Data Integrity**: Use of SHA-256 transaction hashing and atomic database transactions.
* **Advanced Ingestion**: A hybrid approach combining traditional CSV parsing with cutting-edge Vision Language (VL) model integration for complex PDF statements.
* **Risk Modeling**: A robust schema for security universes, market data, and "what-if" scenario modeling via alternative portfolios.
* **Observability**: A dedicated `DataQualityLog` and `FileImportLog` system for auditing the ingestion pipeline.

While the core architecture is excellent, there are several areas where robustness, consistency, and error handling can be improved to prevent future failures and improve the user experience.

---

## 2. Detailed Review

### 2.1 Core Module (`core/`)
The central data model is strong. The use of `Decimal` for all financial fields is correct, and the `Transaction` model's `tx_hash` provides a solid foundation for idempotency.

* **Strengths**: Strong use of `on_delete=models.PROTECT` for critical relationships and `transaction.atomic()` for data ingestion.
* **Weaknesses**: The `TransactionType` model is highly flexible but lacks a mechanism to map diverse broker-specific codes to a unified "canonical" type for high-level reporting.

### 2.2 Import Module (`imports/`)
The ingestion pipeline is the most complex part of the system. The dual-track approach (CSV vs. VL-PDF) is highly effective.

* **Strengths**: The `parsers_monthly_vl.py` implementation includes ingenious post-processing heuristics to correct common LLM errors (e.g., mismatch between total market value and the sum of individual holdings).
* **Weaknesses**: The `auto_import.py` logic is somewhat inconsistent with the `core/models.py` design, particularly regarding duplicate detection.

### 2.3 Risk Module (`risk/`)
The risk engine is built on a very professional foundation. The `SecurityUniverse` and `AlternativePortfolio` models allow for advanced financial modeling.

* **Strengths**: The `DataQualityLog` is a standout feature, providing a structured way to track data gaps and fetch failures, which is essential for a production financial system.
* **Weaknesses**: Some validation logic (like weight checks in `AlternativeHolding`) is somewhat minimal.

---

## 3. Categorized Findings

### 🔴 SEVERE
*Critical bugs or architectural flaws that risk data corruption or loss.*

| ID | Finding | Description |
| :--- | :--- | :--- |
| **S1** | **Inconsistent Duplicate Detection** | `imports/auto_import.py` performs a manual field-by-field check for duplicate transactions instead of utilizing the `tx_hash` defined in `core/models.py`. This is less robust and more prone to errors if any field (like `commission`) varies slightly between files. |

### 🟠 URGENT
*High-priority improvements to prevent future failures or significant user friction.*

| ID | Finding | Description |
| :--- | :--- | :--- |
| **U1** | **Brittle Ingestion Trigger** | `imports/watcher.py` relies on filename prefixes (e.g., `annual report`) to distinguish between statement types. This is highly susceptible to user error and could lead to files being processed by the wrong parser. |
| **U2** | **Hard Error on LLM Failure** | `imports/parsers_monthly_vl.py` raises a `ValueError` if the LLM fails to return a `statement_date`. This halts the entire import process for the file. A more graceful "partial success" or "retry" mechanism would be more resilient. |
| **U3** | **Incorrect Statement Date** | `imports/auto_import.py` sets `BrokerStatement.file_date` to `timezone.now().date()`. This records the *import* date, not the actual *statement* date, which will cause inaccuracies in historical reporting and trend analysis. |

### 🟡 MODERATE
*Best practices, code quality, and minor robustness improvements.*

| ID | Finding | Description |
| :--- | :--- | :--- |
| **M1** | **Implicit Currency Assumption** | In `auto_import.py`, if a currency is not 'CAD', it is implicitly treated as 'USD' for the purpose of exchange rate calculation. This should be made explicit or handled via a more robust lookup. |
| **M2** | **Incomplete Weight Validation** | `risk/models.py:AlternativeHolding.clean()` only validates that total weights do not *exceed* 100%. It does not check if they are below 100%, which might be important depending on the use case. |
| **M3** | **Transaction Type Fragmentation** | While `TransactionType` is flexible, there is no mechanism to "normalize" different broker codes (e.g., 'BUY' vs 'PURCHASE') into a single canonical type for consistent reporting. |

### 🔵 LOW
*Style, minor optimizations, and documentation.*

| ID | Finding | Description |
| :--- | :--- | :--- |
| **L1** | **Polling vs. Events** | `imports/watcher.py` uses a polling loop. Switching to a library like `watchdog` would be more efficient and responsive to file system events. |
| **L2** | **Symbol Length Constraint** | `risk/models.py:SecurityUniverse.symbol` has a `max_length=20`. While usually sufficient, some international or complex symbols might exceed this. |
| **L3** | **Type Safety for Severity** | `risk/models.py:DataQualityLog.severity` uses a `CharField`. Using a `choices` field would ensure better data consistency. |
