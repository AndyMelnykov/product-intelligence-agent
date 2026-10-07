# Repository structure

Logical view of the repo, grouped by layer. Module dependencies reflect the actual imports.

```mermaid
flowchart TB
    subgraph EXT["External"]
        REDDIT[("Reddit API")]
        CLAUDE[("Claude API")]
        KEYRING[("OS keyring")]
    end

    subgraph ENTRY["Entry points"]
        RUN["run_weekly.py / run.ps1<br/>(weekly orchestrator)"]
        QRY["query.py<br/>(read-only lookup CLI)"]
        SETC["set_credentials.py<br/>(one-time setup)"]
    end

    subgraph PIPE["Pipeline stages (run in order)"]
        direction LR
        F["fetch<br/>collect evidence"] --> E["extract<br/>LLM: signal candidates"] --> M["match<br/>LLM: map to canonical topics"] --> R["report<br/>trend scoring"] --> MAT["materiality<br/>classify + emit"]
    end

    subgraph CORE["Shared foundation"]
        DB["db.py<br/>SQLite schema + access"]
        CFG["config.py<br/>config.yaml"]
        CRED["credentials.py"]
        ST["state.py<br/>fetch cursor JSON"]
        WK["weekutil.py<br/>ISO week"]
    end

    subgraph STORE["Data (data/, gitignored)"]
        SQL[("pi_agent.db<br/>evidence, topics, candidates")]
        EVT[("events.jsonl<br/>material signals")]
        STF[("fetch state")]
    end

    subgraph EVAL["evals/ : quality checks for the two LLM steps"]
        GOLD["golden/*.yaml<br/>extraction, matching, judge cases"]
        HAR["harness + run_*_eval<br/>grade vs golden"]
        JDG["judge.py<br/>LLM judge for summaries"]
        RUNS["runs.py -> evals/results JSON"]
        CMP["compare_runs.py<br/>deltas, flips"]
        EXP["export_judge_cases.py"]
    end

    TESTS["tests/<br/>unit per module + end_to_end"]
    DOCS["docs/<br/>vision, gap analysis, ADRs, specs, plans"]

    RUN --> PIPE
    RUN --> CFG
    QRY --> DB
    SETC --> CRED
    CRED --> KEYRING

    F --> REDDIT
    E --> CLAUDE
    M --> CLAUDE
    F & E & M & R & MAT --> DB
    F --> ST
    F & E & M --> CRED
    M & R & MAT --> WK
    MAT --> R

    DB --> SQL
    ST --> STF
    MAT --> EVT

    GOLD --> HAR
    HAR -. "imports real prompts" .-> E
    HAR -. "imports real prompts" .-> M
    HAR --> CLAUDE
    HAR --> JDG
    JDG --> CLAUDE
    HAR --> RUNS --> CMP
    RUNS --> EXP --> GOLD

    TESTS -. covers .-> PIPE
    TESTS -. covers .-> EVAL
```

## Notes

- The five pipeline stages share only SQLite. They don't call each other, except that `materiality` reuses `report`'s trend logic.
- Only `extract` and `match` call Claude (ADR 001). Trend and materiality scoring are deterministic (ADR 003).
- Evals import the production `extract` and `match` code, so they test the real prompts. `export_judge_cases` feeds saved runs back into the golden judge cases.
- The pipeline's only external output is `data/events.jsonl`, written by `materiality` (ADR 004).
