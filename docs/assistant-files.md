# Assistant Files Map

## Overview

This document lists files related to the local assistant feature in TrendRadar.

## Core Files

- `trendradar/assistant_web.py`
  - Local assistant web server.
  - Provides:
    - `GET /assistant` (chat page)
    - `POST /api/assistant/ask` (LLM chat API)

- `trendradar/assistant_router.py`
  - Rule-based intent routing logic.
  - Includes:
    - intent routing (`learning` / `investment` / `cognition`)
    - default system prompt resolution

- `trendradar/ai/client.py`
  - Unified LLM client (LiteLLM wrapper).
  - Used by assistant API to call model providers.

- `trendradar/core/loader.py`
  - Loads assistant router config from external file:
    - default: `config/assistant_router.yaml`
    - override: `ASSISTANT_ROUTER_CONFIG`

- `trendradar/__main__.py`
  - CLI entry.
  - Supports assistant web mode:
    - `--assistant-web`
    - `--assistant-web-host`
    - `--assistant-web-port`
    - `--assistant-web-no-open`
  - Normal run also attempts background assistant web startup.

- `trendradar/report/html.py`
  - Report page UI.
  - AI button opens assistant modal and loads `/assistant` in iframe.

## Config Files

- `config/assistant_router.yaml`
  - Configurable intent keywords (`route_rules`)
  - Configurable role prompts (`system_prompts`)

- `config/config.yaml`
  - Main project config (AI model/API key etc.).
  - No longer stores assistant router dictionary/prompt content directly.

## Run Commands

- Start full workflow (includes auto background assistant startup):
  - `python -m trendradar`

- Start assistant web service only:
  - `python -m trendradar --assistant-web`
