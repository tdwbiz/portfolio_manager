# Architecture Overview

## System Architecture

```mermaid
graph TB
    subgraph "User Interface"
        Browser[Web Browser]
    end
    
    subgraph "Django Application"
        subgraph "Core App"
            Views[Views]
            Models[Models]
            Utils[Utilities]
        end
        
        subgraph "Imports App"
            PDFParser[PDF Parsers]
            LLMClient[LLM Client]
            AutoImport[Auto Import]
        end
        
        subgraph "Risk App"
            Services[Risk Services]
            Engine[Risk Engine]
            Reports[Reports]
        end
    end
    
    subgraph "Data Layer"
        SQLServer[(SQL Server)]
        SQLite[(SQLite - Tests)]
        Filesystem[File System]
    end
    
    subgraph "External Services"
        YFinance[yFinance API]
        VLModel[Vision-Language Model]
    end
    
    Browser --> Views
    Views --> Models
    Views --> Utils
    Views --> Services
    
    PDFParser --> Filesystem
    AutoImport --> PDFParser
    LLMClient --> VLModel
    
    Services --> Engine
    Engine --> YFinance
    Engine --> SQLServer
    Engine --> SQLite
    
    Models --> SQLServer
    Models --> SQLite
```

## Data Flow

### Price Fetching Pipeline

```mermaid
sequenceDiagram
    participant Cmd as Management Command
    participant DS as DataSource
    participant YF as yFinance
    participant DB as Database
    
    Cmd->>DS: fetch_prices(symbols)
    DS->>YF: download(symbols, start, end)
    YF-->>DS: OHLCV data
    DS->>DB: save HistoricalPrice records
    DB-->>DS: confirmation
    DS-->>Cmd: success count
```

### PDF Import Workflow

```mermaid
sequenceDiagram
    participant User as User
    participant View as Import View
    participant Parser as PDF Parser
    participant DB as Database
    
    User->>View: Upload PDF
    View->>View: Validate PDF magic bytes
    View->>Parser: parse(file_path)
    Parser->>Parser: Extract text (pdfplumber/PyMuPDF)
    Parser-->>View: Parsed data
    View->>DB: Create RbcMonthly/RbcAnnual records
    DB-->>View: confirmation
    View-->>User: Import results
```

### Risk Analytics Pipeline

```mermaid
sequenceDiagram
    participant Cmd as Management Command
    participant Vol as Volatility Service
    participant MC as Monte Carlo
    participant Stress as Stress Test
    participant DB as Database
    
    Cmd->>Vol: compute_volatility(symbols)
    Vol->>DB: Load HistoricalPrice
    Vol-->>Cmd: Volatility summaries
    
    Cmd->>MC: run_simulation(portfolio, scenarios)
    MC->>DB: Load positions, prices
    MC->>MC: Vectorised GBM/JD simulation
    MC-->>Cmd: Simulation results
    
    Cmd->>Stress: run_stress_test(scenario)
    Stress->>DB: Load portfolio
    Stress-->>Cmd: Stress test results
```

## App Dependencies

| App | Depends On | Used By |
|-----|-----------|---------|
| `core` | Django ORM | Views, Management Commands |
| `imports` | `core`, `llm_client` | Auto Import, PDF Views |
| `risk` | `core`, `imports` | Risk Engine, Reports |

## Key Design Decisions

1. **SQLite for Tests**: All tests use SQLite in-memory database for speed and isolation
2. **SQL Server for Production**: Production uses SQL Server for enterprise-grade reliability
3. **Mocking External Services**: yFinance and VL models are mocked in tests
4. **Pure Python Services**: Risk services take DataFrames/arrays as input — independently testable
5. **Fallback PDF Libraries**: Try pdfplumber → PyMuPDF → plain text for maximum compatibility
