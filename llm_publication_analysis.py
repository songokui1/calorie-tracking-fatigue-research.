#!/usr/bin/env python3
"""
Publication-grade Reddit analysis using the OpenAI Responses API.

This script leaves Pipeline.py untouched and adds a deeper automation path:

1. Reads Reddit post/comment exports.
2. Builds thread packets (post + sampled comments).
3. Runs a structured first-pass code with the OpenAI API.
4. Runs a second-pass audit/adjudication code for each thread.
5. Produces reproducible tables, agreement audits, evidence tables, and a
   publication-ready markdown findings report.

Usage
-----
export OPENAI_API_KEY=...
python llm_publication_analysis.py \
  --posts all_reddit_posts.xlsx \
  --comments all_reddit_comments.xlsx \
  --output-dir publication_llm_outputs

Recommended first test
----------------------
python llm_publication_analysis.py \
  --posts .smoke-test/all_reddit_posts.xlsx \
  --comments .smoke-test/all_reddit_comments.xlsx \
  --output-dir publication_llm_outputs_smoke \
  --thread-limit 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openai import OpenAI


DEFAULT_PRIMARY_MODEL = os.getenv("OPENAI_PRIMARY_MODEL", "gpt-5.4-mini")
DEFAULT_AUDIT_MODEL = os.getenv("OPENAI_AUDIT_MODEL", DEFAULT_PRIMARY_MODEL)
DEFAULT_SYNTHESIS_MODEL = os.getenv("OPENAI_SYNTHESIS_MODEL", DEFAULT_PRIMARY_MODEL)
EXCEL_ILLEGAL_CHARACTERS_RE = re.compile(r"[\x00-\x08\x0B-\x0C\x0E-\x1F]")

TRACKING_PHRASES = [
    "calorie",
    "calories",
    "calorie tracking",
    "calorie counting",
    "counting calories",
    "food log",
    "food logging",
    "logging food",
    "log food",
    "log meals",
    "meal logging",
    "track food",
    "tracking food",
    "track calories",
    "tracking calories",
    "macro tracking",
    "macros",
    "food diary",
    "barcode scanner",
    "myfitnesspal",
    "lose it",
    "cronometer",
    "fatsecret",
    "yazio",
]

THREAD_CODE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "thread_included",
        "scope_decision",
        "post_focus",
        "fatigue_present",
        "fatigue_salience",
        "fatigue_types",
        "barriers",
        "churn_signal",
        "help_seeking",
        "support_patterns",
        "support_balance",
        "actionable_advice_present",
        "harmful_or_extreme_advice_present",
        "app_mentions",
        "design_implications",
        "post_evidence",
        "comment_evidence",
        "analyst_memo",
        "confidence",
    ],
    "properties": {
        "thread_included": {"type": "boolean"},
        "scope_decision": {
            "type": "string",
            "enum": [
                "in_scope",
                "general_dieting_not_tracking",
                "exercise_only",
                "too_sparse",
                "unclear",
                "other",
            ],
        },
        "post_focus": {
            "type": "string",
            "enum": [
                "calorie_tracking",
                "macro_tracking",
                "app_specific_usability",
                "weight_loss_general",
                "nutrition_general",
                "community_support",
                "mental_health_or_obsession",
                "other",
            ],
        },
        "fatigue_present": {"type": "boolean"},
        "fatigue_salience": {"type": "integer", "minimum": 0, "maximum": 4},
        "fatigue_types": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "string",
                "enum": [
                    "cognitive_burden",
                    "data_entry_burden",
                    "emotional_distress",
                    "obsessive_or_disordered_pattern",
                    "technical_or_usability",
                    "monetization",
                    "social_pressure",
                    "lack_of_progress_or_reward",
                    "habit_disruption",
                    "none",
                ],
            },
        },
        "barriers": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "string",
                "enum": [
                    "time_burden",
                    "memory_burden",
                    "database_inaccuracy",
                    "too_many_steps",
                    "paywall_or_ads",
                    "bugs_or_crashes",
                    "confusing_feedback_or_metrics",
                    "social_or_identity_tension",
                    "mental_health_trigger",
                    "lack_of_results",
                    "other",
                ],
            },
        },
        "churn_signal": {
            "type": "string",
            "enum": [
                "none",
                "considering_break",
                "temporary_break",
                "switching_tools",
                "quit_tracking",
                "uninstalled_app",
            ],
        },
        "help_seeking": {"type": "boolean"},
        "support_patterns": {
            "type": "array",
            "maxItems": 4,
            "items": {
                "type": "string",
                "enum": [
                    "emotional_validation",
                    "practical_advice",
                    "shared_experience",
                    "normalization",
                    "encouragement_to_take_a_break",
                    "discouragement_or_judgment",
                    "extreme_or_potentially_harmful_advice",
                    "no_substantive_support",
                ],
            },
        },
        "support_balance": {
            "type": "string",
            "enum": [
                "mostly_supportive",
                "mixed",
                "mostly_neutral",
                "problematic_or_harmful",
                "no_meaningful_responses",
            ],
        },
        "actionable_advice_present": {"type": "boolean"},
        "harmful_or_extreme_advice_present": {"type": "boolean"},
        "app_mentions": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 40},
        },
        "design_implications": {
            "type": "array",
            "maxItems": 4,
            "items": {"type": "string", "maxLength": 180},
        },
        "post_evidence": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "quote"],
                "properties": {
                    "claim": {"type": "string", "maxLength": 180},
                    "quote": {"type": "string", "maxLength": 220},
                },
            },
        },
        "comment_evidence": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["claim", "quote"],
                "properties": {
                    "claim": {"type": "string", "maxLength": 180},
                    "quote": {"type": "string", "maxLength": 220},
                },
            },
        },
        "analyst_memo": {"type": "string", "maxLength": 500},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}

THREAD_CODER_PROMPT = """
You are a senior mixed-methods health researcher coding Reddit threads about
calorie tracking, food logging, dieting apps, and fatigue/disengagement.

Your job is to code conservatively and transparently.

Rules:
- Use only the supplied post and sampled comments.
- Code only what is actually supported by the text.
- Do not infer diagnosis, demographics, or outcomes that are not stated.
- Distinguish calorie-tracking fatigue from general dieting frustration.
- If the thread is out of scope, mark thread_included=false and keep labels conservative.
- Evidence quotes must be brief, verbatim spans from the supplied text.
- If comments are mixed, represent the balance rather than the loudest single reply.
- The sampled comments do not represent the entire thread perfectly; avoid overstating certainty.
- Keep the JSON concise: limit evidence to the strongest few examples, keep quotes short, and keep analyst_memo brief.
- analyst_memo should briefly explain ambiguity, nuance, or why a code was chosen.
""".strip()

THREAD_AUDITOR_PROMPT = """
You are the second coder and adjudicator for a publication-grade qualitative analysis.

Review the raw thread packet and the first-pass structured code. Correct anything that is:
- unsupported by the text,
- too aggressive,
- internally inconsistent,
- missing an important competing interpretation,
- or too shallow to capture the thread's main burden/support pattern.

Return the same schema as the first pass, but revised as needed.
Favor conservative coding over over-interpretation.
""".strip()

SYNTHESIS_PROMPT = """
You are writing a publication-grade analytic memo for a health informatics study.

Use only the supplied summary tables and evidence excerpts. Do not invent counts.
Be specific about:
- recurring fatigue mechanisms,
- differences across subreddits,
- churn/disengagement signals,
- peer-support dynamics,
- design implications,
- and validity cautions.

Write in polished Markdown with short paragraphs and flat bullets where helpful.
Sections:
1. Analytic Overview
2. Core Themes
3. Subreddit Differences
4. Support Dynamics
5. Design Implications
6. Validity and Remaining Risks
""".strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publication-grade LLM Reddit analysis.")
    parser.add_argument("--posts", default="all_reddit_posts.xlsx", help="Path to post Excel export.")
    parser.add_argument("--comments", default="all_reddit_comments.xlsx", help="Path to comment Excel export.")
    parser.add_argument("--output-dir", default="publication_llm_outputs", help="Directory for outputs.")
    parser.add_argument("--years-back", type=int, default=5, help="Rolling window for posts/comments.")
    parser.add_argument("--thread-limit", type=int, default=None, help="Optional cap for number of threads to code.")
    parser.add_argument("--max-comments-per-thread", type=int, default=12, help="Max sampled comments per thread.")
    parser.add_argument("--primary-model", default=DEFAULT_PRIMARY_MODEL, help="Primary coding model.")
    parser.add_argument("--audit-model", default=DEFAULT_AUDIT_MODEL, help="Audit/adjudication model.")
    parser.add_argument("--synthesis-model", default=DEFAULT_SYNTHESIS_MODEL, help="Report-writing model.")
    parser.add_argument(
        "--coding-reasoning-effort",
        default=os.getenv("OPENAI_CODING_REASONING_EFFORT", "low"),
        choices=["none", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument(
        "--audit-reasoning-effort",
        default=os.getenv("OPENAI_AUDIT_REASONING_EFFORT", "low"),
        choices=["none", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument(
        "--synthesis-reasoning-effort",
        default=os.getenv("OPENAI_SYNTHESIS_REASONING_EFFORT", "medium"),
        choices=["none", "low", "medium", "high", "xhigh"],
    )
    parser.add_argument("--temperature", type=float, default=0.2, help="Temperature for coding calls.")
    parser.add_argument("--sleep-between-calls", type=float, default=0.0, help="Optional delay between API calls.")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore cache and rerun all API calls.")
    parser.add_argument("--mock-llm", action="store_true", help="Run the full pipeline without API calls for testing.")
    return parser.parse_args()


def require_openai_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    return api_key


def normalize_text(text: Any) -> str:
    cleaned = EXCEL_ILLEGAL_CHARACTERS_RE.sub("", str(text or ""))
    return " ".join(cleaned.split())


def lower_text(text: Any) -> str:
    return normalize_text(text).lower()


def truncate_text(text: Any, max_chars: int) -> str:
    value = normalize_text(text)
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 3].rstrip() + "..."


def slugify(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def strip_json_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?", "", cleaned).strip()
        cleaned = re.sub(r"```$", "", cleaned).strip()
    return cleaned


def response_output_text(response: Any) -> str:
    direct = getattr(response, "output_text", None)
    if direct:
        return direct

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        for content in getattr(item, "content", []) or []:
            content_type = getattr(content, "type", None)
            if content_type == "output_text":
                parts.append(getattr(content, "text", ""))
            if content_type == "refusal":
                raise RuntimeError(f"Model refusal: {getattr(content, 'refusal', '')}")
    return "\n".join(part for part in parts if part).strip()


class RetryableStructuredOutputError(RuntimeError):
    """Raised when the model response is empty, incomplete, or not parseable JSON yet."""


def usage_to_dict(usage: Any) -> dict[str, Any]:
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return {key: make_json_safe(value) for key, value in usage.items()}
    data = {}
    for key in ["input_tokens", "output_tokens", "total_tokens", "input_tokens_details", "output_tokens_details"]:
        value = getattr(usage, key, None)
        if value is not None:
            data[key] = make_json_safe(value)
    return data


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [make_json_safe(item) for item in value]
    if hasattr(value, "model_dump"):
        return make_json_safe(value.model_dump())
    if hasattr(value, "__dict__"):
        return {
            str(key): make_json_safe(item)
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        }
    return str(value)


def parse_json_safely(text: str) -> dict[str, Any]:
    cleaned = strip_json_fences(text)
    if not cleaned:
        raise RetryableStructuredOutputError("Structured output was empty.")

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        if start >= 0:
            try:
                parsed, _ = json.JSONDecoder().raw_decode(cleaned[start:])
            except json.JSONDecodeError as nested_exc:
                preview = truncate_text(cleaned, 300)
                raise RetryableStructuredOutputError(
                    f"Structured output was not valid JSON: {nested_exc}. Preview: {preview}"
                ) from exc
        else:
            preview = truncate_text(cleaned, 300)
            raise RetryableStructuredOutputError(
                f"Structured output did not contain JSON content. Preview: {preview}"
            ) from exc

    if not isinstance(parsed, dict):
        raise RetryableStructuredOutputError(f"Structured output parsed to {type(parsed).__name__}, not an object.")
    return parsed


def call_structured_response(
    client: OpenAI,
    *,
    model: str,
    schema_name: str,
    schema: dict[str, Any],
    developer_prompt: str,
    user_prompt: str,
    reasoning_effort: str,
    temperature: float,
    metadata: dict[str, str],
    max_output_tokens: int = 3500,
    max_retries: int = 5,
) -> dict[str, Any]:
    for attempt in range(1, max_retries + 1):
        try:
            attempt_max_output_tokens = max_output_tokens + ((attempt - 1) * 1500)
            request_kwargs = {
                "model": model,
                "instructions": developer_prompt,
                "input": [{"role": "user", "content": user_prompt}],
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": attempt_max_output_tokens,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "strict": True,
                        "schema": schema,
                    }
                },
                "metadata": metadata,
            }
            if not (model.startswith("gpt-5") and reasoning_effort != "none"):
                request_kwargs["temperature"] = temperature

            response = client.responses.create(
                **request_kwargs,
            )
            if getattr(response, "status", None) == "incomplete":
                details = make_json_safe(getattr(response, "incomplete_details", None))
                raise RetryableStructuredOutputError(f"Structured response was incomplete: {details}")
            parsed = parse_json_safely(response_output_text(response))
            return {
                "parsed": parsed,
                "response_id": getattr(response, "id", None),
                "usage": usage_to_dict(getattr(response, "usage", None)),
                "model": model,
                "max_output_tokens": attempt_max_output_tokens,
            }
        except Exception as exc:  # noqa: BLE001
            if attempt == max_retries:
                raise
            sleep_seconds = min(20, 2 ** attempt)
            print(f"Retrying {schema_name} after error: {exc}. Sleeping {sleep_seconds}s.", file=sys.stderr)
            time.sleep(sleep_seconds)
    raise RuntimeError("Unreachable retry block.")


def call_text_response(
    client: OpenAI,
    *,
    model: str,
    developer_prompt: str,
    user_prompt: str,
    reasoning_effort: str,
    temperature: float,
    metadata: dict[str, str],
    max_output_tokens: int = 4500,
    max_retries: int = 5,
) -> dict[str, Any]:
    for attempt in range(1, max_retries + 1):
        try:
            attempt_max_output_tokens = max_output_tokens + ((attempt - 1) * 1500)
            request_kwargs = {
                "model": model,
                "instructions": developer_prompt,
                "input": [{"role": "user", "content": user_prompt}],
                "reasoning": {"effort": reasoning_effort},
                "max_output_tokens": attempt_max_output_tokens,
                "metadata": metadata,
            }
            if not (model.startswith("gpt-5") and reasoning_effort != "none"):
                request_kwargs["temperature"] = temperature

            response = client.responses.create(**request_kwargs)
            if getattr(response, "status", None) == "incomplete":
                details = make_json_safe(getattr(response, "incomplete_details", None))
                raise RuntimeError(f"Text response was incomplete: {details}")
            text = response_output_text(response)
            if not text.strip():
                raise RuntimeError("Text response was empty.")
            return {
                "text": text,
                "response_id": getattr(response, "id", None),
                "usage": usage_to_dict(getattr(response, "usage", None)),
                "model": model,
                "max_output_tokens": attempt_max_output_tokens,
            }
        except Exception as exc:  # noqa: BLE001
            if attempt == max_retries:
                raise
            sleep_seconds = min(20, 2 ** attempt)
            print(f"Retrying text synthesis after error: {exc}. Sleeping {sleep_seconds}s.", file=sys.stderr)
            time.sleep(sleep_seconds)
    raise RuntimeError("Unreachable retry block.")


def load_posts(path: Path, years_back: int) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Posts file not found: {path}")

    posts = pd.read_excel(path)
    required = {"submission_id", "subreddit", "title", "selftext", "created_utc"}
    missing = required - set(posts.columns)
    if missing:
        raise ValueError(f"Posts file is missing required columns: {sorted(missing)}")

    posts = posts.copy()
    posts["subreddit"] = posts["subreddit"].astype(str).str.lower()
    posts["title"] = posts["title"].fillna("").astype(str)
    posts["selftext"] = posts["selftext"].fillna("").astype(str)
    posts["thread_text"] = (posts["title"] + "\n\n" + posts["selftext"]).str.strip()
    posts["created_dt"] = pd.to_datetime(posts["created_utc"], unit="s", errors="coerce")

    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    start = now - pd.DateOffset(years=years_back)

    posts = posts.dropna(subset=["created_dt"])
    posts = posts[(posts["created_dt"] >= start) & (posts["created_dt"] <= now)]
    posts = posts.drop_duplicates(subset=["submission_id"]).reset_index(drop=True)
    posts["thread_year"] = posts["created_dt"].dt.year
    posts["thread_month"] = posts["created_dt"].dt.to_period("M").astype(str)
    posts["has_tracking_terms"] = posts["thread_text"].apply(
        lambda text: any(term in lower_text(text) for term in TRACKING_PHRASES)
    )
    return posts


def load_comments(path: Path, posts: pd.DataFrame, years_back: int) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(
            columns=[
                "submission_id",
                "subreddit",
                "comment_id",
                "body",
                "comment_created_utc",
                "comment_score",
            ]
        )

    comments = pd.read_excel(path)
    required = {"submission_id", "comment_id", "body", "comment_created_utc"}
    missing = required - set(comments.columns)
    if missing:
        raise ValueError(f"Comments file is missing required columns: {sorted(missing)}")

    comments = comments.copy()
    if "subreddit" not in comments.columns:
        comments = comments.merge(posts[["submission_id", "subreddit"]], on="submission_id", how="left")
    comments["subreddit"] = comments["subreddit"].astype(str).str.lower()
    comments["body"] = comments["body"].fillna("").astype(str).str.strip()
    comments["comment_score"] = pd.to_numeric(comments.get("comment_score", 0), errors="coerce").fillna(0)
    comments["comment_created_dt"] = pd.to_datetime(comments["comment_created_utc"], unit="s", errors="coerce")

    now = pd.Timestamp.now(tz="UTC").tz_localize(None)
    start = now - pd.DateOffset(years=years_back)

    comments = comments.dropna(subset=["comment_created_dt"])
    comments = comments[(comments["comment_created_dt"] >= start) & (comments["comment_created_dt"] <= now)]
    comments = comments[comments["submission_id"].isin(posts["submission_id"])]
    comments = comments[comments["body"].ne("")]
    comments = comments[~comments["body"].isin(["[deleted]", "[removed]"])]
    comments = comments.drop_duplicates(subset=["comment_id"]).reset_index(drop=True)
    return comments


def sample_comments_for_submission(group: pd.DataFrame, cap: int) -> pd.DataFrame:
    if cap <= 0 or len(group) <= cap:
        return group.sort_values(["comment_created_dt", "comment_score"], ascending=[True, False])

    score_cap = max(1, cap // 2)
    top = group.sort_values(["comment_score", "comment_created_dt"], ascending=[False, True]).head(score_cap)
    remaining = group[~group["comment_id"].isin(top["comment_id"])]
    early = remaining.sort_values(["comment_created_dt", "comment_score"], ascending=[True, False]).head(cap - len(top))
    sampled = (
        pd.concat([top, early], ignore_index=True)
        .drop_duplicates(subset=["comment_id"])
        .sort_values(["comment_created_dt", "comment_score"], ascending=[True, False])
    )
    return sampled


def build_thread_packets(
    posts: pd.DataFrame,
    comments: pd.DataFrame,
    max_comments_per_thread: int,
    thread_limit: int | None,
) -> list[dict[str, Any]]:
    comments_by_submission = {
        submission_id: group.copy()
        for submission_id, group in comments.groupby("submission_id", sort=False)
    }

    packets: list[dict[str, Any]] = []
    iter_posts = posts.head(thread_limit) if thread_limit is not None else posts

    for _, post in iter_posts.iterrows():
        raw_comments = comments_by_submission.get(post["submission_id"])
        if raw_comments is None or raw_comments.empty:
            sampled_comments = raw_comments if raw_comments is not None else pd.DataFrame()
        else:
            sampled_comments = sample_comments_for_submission(raw_comments, max_comments_per_thread)

        comment_records = []
        if sampled_comments is not None and not sampled_comments.empty:
            for _, comment in sampled_comments.iterrows():
                comment_records.append(
                    {
                        "comment_id": str(comment["comment_id"]),
                        "score": int(comment.get("comment_score", 0) or 0),
                        "created_at": str(comment["comment_created_dt"]),
                        "text": truncate_text(comment["body"], 800),
                    }
                )

        packets.append(
            {
                "submission_id": str(post["submission_id"]),
                "subreddit": str(post["subreddit"]),
                "created_at": str(post["created_dt"]),
                "thread_year": int(post["thread_year"]),
                "thread_month": str(post["thread_month"]),
                "title": truncate_text(post["title"], 250),
                "post_text": truncate_text(post["selftext"], 3500),
                "sampled_comment_count": len(comment_records),
                "available_comment_count": int(len(raw_comments)) if raw_comments is not None else 0,
                "has_tracking_terms": bool(post["has_tracking_terms"]),
                "comment_sample": comment_records,
            }
        )

    return packets


def cache_path_for(cache_dir: Path, stage: str, record_id: str, payload: dict[str, Any]) -> Path:
    digest = hash_text(json.dumps(payload, sort_keys=True, ensure_ascii=True))
    return cache_dir / f"{stage}_{slugify(record_id)}_{digest[:16]}.json"


def load_cached(cache_path: Path) -> dict[str, Any] | None:
    if not cache_path.exists():
        return None
    try:
        with cache_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


def save_cached(cache_path: Path, payload: dict[str, Any]) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as handle:
        json.dump(make_json_safe(payload), handle, indent=2, ensure_ascii=True)


def build_thread_user_prompt(packet: dict[str, Any]) -> str:
    body = {
        "submission_id": packet["submission_id"],
        "subreddit": packet["subreddit"],
        "created_at": packet["created_at"],
        "thread_year": packet["thread_year"],
        "thread_month": packet["thread_month"],
        "has_tracking_terms": packet["has_tracking_terms"],
        "title": packet["title"],
        "post_text": packet["post_text"],
        "available_comment_count": packet["available_comment_count"],
        "sampled_comment_count": packet["sampled_comment_count"],
        "comment_sample": packet["comment_sample"],
    }
    return (
        "Code this Reddit thread for a study of calorie-tracking fatigue and peer support.\n\n"
        "Thread packet JSON:\n"
        f"{json.dumps(body, indent=2, ensure_ascii=True)}"
    )


def build_audit_user_prompt(packet: dict[str, Any], first_pass: dict[str, Any]) -> str:
    return (
        "Review this Reddit thread packet and the first-pass code. Return a corrected final code.\n\n"
        "Thread packet JSON:\n"
        f"{json.dumps(packet, indent=2, ensure_ascii=True)}\n\n"
        "First-pass code JSON:\n"
        f"{json.dumps(first_pass, indent=2, ensure_ascii=True)}"
    )


def find_snippet(text: str, keywords: list[str], fallback: str) -> str:
    lowered = lower_text(text)
    for keyword in keywords:
        index = lowered.find(keyword)
        if index >= 0:
            return truncate_text(text[max(0, index - 40): index + len(keyword) + 80], 140)
    return truncate_text(text, 140) if text else fallback


def mock_thread_code(packet: dict[str, Any], *, audit: bool) -> dict[str, Any]:
    post_text = normalize_text(packet.get("title", "") + " " + packet.get("post_text", ""))
    comment_text = " ".join(comment.get("text", "") for comment in packet.get("comment_sample", []))
    combined = lower_text(post_text + " " + comment_text)

    fatigue_types = []
    if any(term in combined for term in ["overwhel", "mental", "exhaust", "drain"]):
        fatigue_types.append("cognitive_burden")
    if any(term in combined for term in ["tedious", "log everything", "enter everything", "tracking everything", "too much work"]):
        fatigue_types.append("data_entry_burden")
    if any(term in combined for term in ["anxious", "guilty", "ashamed", "burned out", "burnt out", "stress"]):
        fatigue_types.append("emotional_distress")
    if any(term in combined for term in ["obsess", "disorder", "orthorex", "triggering"]):
        fatigue_types.append("obsessive_or_disordered_pattern")
    if any(term in combined for term in ["bug", "crash", "glitch", "scanner", "database wrong", "wrong calories"]):
        fatigue_types.append("technical_or_usability")
    if any(term in combined for term in ["premium", "subscription", "paywall", "ads"]):
        fatigue_types.append("monetization")
    if any(term in combined for term in ["friends", "family", "social", "judge"]):
        fatigue_types.append("social_pressure")
    if any(term in combined for term in ["not working", "plateau", "no progress", "no results"]):
        fatigue_types.append("lack_of_progress_or_reward")
    if any(term in combined for term in ["forget", "inconsistent", "keep up", "routine"]):
        fatigue_types.append("habit_disruption")
    if not fatigue_types:
        fatigue_types = ["none"]

    barriers = []
    if any(term in combined for term in ["time", "takes forever", "too much work", "tedious"]):
        barriers.append("time_burden")
    if any(term in combined for term in ["forget", "remember", "memory"]):
        barriers.append("memory_burden")
    if any(term in combined for term in ["wrong calories", "database", "entry incorrect"]):
        barriers.append("database_inaccuracy")
    if any(term in combined for term in ["too many steps", "too much work", "tedious"]):
        barriers.append("too_many_steps")
    if any(term in combined for term in ["paywall", "subscription", "premium", "ads"]):
        barriers.append("paywall_or_ads")
    if any(term in combined for term in ["bug", "crash", "glitch"]):
        barriers.append("bugs_or_crashes")
    if any(term in combined for term in ["chart", "stats", "numbers", "confusing"]):
        barriers.append("confusing_feedback_or_metrics")
    if any(term in combined for term in ["judge", "social", "friends", "family"]):
        barriers.append("social_or_identity_tension")
    if any(term in combined for term in ["anxious", "guilty", "ashamed", "trigger"]):
        barriers.append("mental_health_trigger")
    if any(term in combined for term in ["plateau", "not working", "no results"]):
        barriers.append("lack_of_results")
    if not barriers:
        barriers = ["other"]

    fatigue_present = fatigue_types != ["none"] or any(
        term in combined for term in ["tired", "fatigue", "quit", "stop tracking", "uninstall", "burned out"]
    )
    fatigue_salience = min(
        4,
        max(
            1 if fatigue_present else 0,
            sum(term in combined for term in ["tired", "fatigue", "quit", "burned out", "overwhelmed"]),
        ),
    )

    if any(term in combined for term in ["uninstall", "deleted the app"]):
        churn_signal = "uninstalled_app"
    elif any(term in combined for term in ["quit tracking", "stopped tracking", "gave up tracking"]):
        churn_signal = "quit_tracking"
    elif any(term in combined for term in ["switched", "moving to", "another app"]):
        churn_signal = "switching_tools"
    elif any(term in combined for term in ["take a break", "break from tracking"]):
        churn_signal = "temporary_break"
    elif any(term in combined for term in ["thinking about stopping", "consider stopping", "want to stop"]):
        churn_signal = "considering_break"
    else:
        churn_signal = "none"

    help_seeking = any(term in combined for term in ["help", "advice", "tips", "how do i", "anyone else", "?"])

    support_patterns = []
    if any(term in combined for term in ["same here", "me too", "you're not alone", "i get it"]):
        support_patterns.append("emotional_validation")
        support_patterns.append("shared_experience")
    if any(term in combined for term in ["try", "you can", "recommend", "use", "set reminders"]):
        support_patterns.append("practical_advice")
    if any(term in combined for term in ["normal", "common", "everyone"]):
        support_patterns.append("normalization")
    if any(term in combined for term in ["take a break", "step back"]):
        support_patterns.append("encouragement_to_take_a_break")
    if any(term in combined for term in ["lazy", "just be disciplined", "no excuses"]):
        support_patterns.append("discouragement_or_judgment")
    if any(term in combined for term in ["starve", "eat nothing", "extreme deficit"]):
        support_patterns.append("extreme_or_potentially_harmful_advice")
    if not support_patterns:
        support_patterns = ["no_substantive_support"]

    if "extreme_or_potentially_harmful_advice" in support_patterns:
        support_balance = "problematic_or_harmful"
    elif "practical_advice" in support_patterns or "emotional_validation" in support_patterns:
        support_balance = "mostly_supportive"
    elif support_patterns == ["no_substantive_support"]:
        support_balance = "no_meaningful_responses"
    else:
        support_balance = "mixed"

    actionable_advice_present = "practical_advice" in support_patterns
    harmful_advice_present = "extreme_or_potentially_harmful_advice" in support_patterns

    app_mentions = []
    for app in ["myfitnesspal", "lose it", "cronometer", "fatsecret", "yazio"]:
        if app in combined:
            app_mentions.append(app)

    design_implications = []
    if "data_entry_burden" in fatigue_types:
        design_implications.append("Reduce repetitive logging steps and support faster meal entry.")
    if "cognitive_burden" in fatigue_types:
        design_implications.append("Lower mental load with reminders, defaults, and simpler daily workflows.")
    if "technical_or_usability" in fatigue_types:
        design_implications.append("Improve food database quality and reduce friction around search/scanning.")
    if "monetization" in fatigue_types:
        design_implications.append("Avoid paywalling core tracking workflows that users treat as essential.")
    if not design_implications:
        design_implications.append("Preserve low-friction tracking and offer flexible engagement modes.")

    scope_decision = "in_scope" if packet.get("has_tracking_terms") else "unclear"
    if any(term in combined for term in ["running", "gym", "lifting"]) and not packet.get("has_tracking_terms"):
        scope_decision = "exercise_only"

    if any(term in combined for term in ["app", "bug", "scanner", "database"]):
        post_focus = "app_specific_usability"
    elif any(term in combined for term in ["macro", "protein", "carb"]) and "calorie" not in combined:
        post_focus = "macro_tracking"
    elif any(term in combined for term in ["obsess", "trigger", "anxious", "guilty"]):
        post_focus = "mental_health_or_obsession"
    elif any(term in combined for term in ["community", "anyone else", "support"]):
        post_focus = "community_support"
    else:
        post_focus = "calorie_tracking"

    post_evidence = [
        {
            "claim": "fatigue_or_burden_signal",
            "quote": find_snippet(post_text, ["tired", "overwhel", "quit", "burn", "log"], "No clear fatigue quote found."),
        }
    ]
    comment_evidence = [
        {
            "claim": "peer_response_pattern",
            "quote": find_snippet(comment_text, ["try", "same", "not alone", "take a break"], "No sampled comment evidence found."),
        }
    ]

    memo = "Deterministic mock code for end-to-end plumbing validation."
    if audit:
        memo = "Mock audit pass completed; labels kept conservative for test validation."

    return {
        "thread_included": scope_decision == "in_scope",
        "scope_decision": scope_decision,
        "post_focus": post_focus,
        "fatigue_present": fatigue_present,
        "fatigue_salience": fatigue_salience,
        "fatigue_types": fatigue_types,
        "barriers": barriers,
        "churn_signal": churn_signal,
        "help_seeking": help_seeking,
        "support_patterns": sorted(set(support_patterns)),
        "support_balance": support_balance,
        "actionable_advice_present": actionable_advice_present,
        "harmful_or_extreme_advice_present": harmful_advice_present,
        "app_mentions": sorted(set(app_mentions)),
        "design_implications": sorted(set(design_implications)),
        "post_evidence": post_evidence,
        "comment_evidence": comment_evidence,
        "analyst_memo": memo,
        "confidence": 0.58 if audit else 0.52,
    }


def mock_synthesis(findings_payload: dict[str, Any]) -> str:
    sample = findings_payload["sample"]
    top_fatigue = findings_payload["fatigue_frequency"][:5]
    top_barriers = findings_payload["barrier_frequency"][:5]
    top_support = findings_payload["support_frequency"][:5]

    def render_items(items: list[dict[str, Any]], value_key: str = "thread_count") -> str:
        if not items:
            return "- No items available."
        return "\n".join(f"- {item['label']}: {item[value_key]}" for item in items)

    subreddits = findings_payload["subreddit_summary"]
    subreddit_lines = []
    for row in subreddits:
        subreddit_lines.append(
            f"- {row['subreddit']}: {row['threads']} threads, fatigue rate {row['fatigue_rate']:.2f}, "
            f"help-seeking rate {row['help_seeking_rate']:.2f}, harmful-advice rate {row['harmful_advice_rate']:.2f}"
        )

    return f"""# Analytic Overview

The mock end-to-end run coded {sample['threads_coded']} Reddit threads across {len(sample['subreddits'])} subreddit(s). In this test set, {sample['fatigue_threads']} thread(s) were coded as containing calorie-tracking fatigue and {sample['help_seeking_threads']} thread(s) contained help-seeking behavior.

## Core Themes

Most common fatigue mechanisms:
{render_items(top_fatigue)}

Most common barriers:
{render_items(top_barriers)}

## Subreddit Differences

{chr(10).join(subreddit_lines) if subreddit_lines else '- No subreddit comparisons available.'}

## Support Dynamics

Most common support patterns:
{render_items(top_support)}

## Design Implications

- Reduce repeated manual entry burden.
- Lower cognitive overhead during routine tracking.
- Preserve low-friction support features around breaks, inconsistency, and burnout.

## Validity and Remaining Risks

- This report was generated in mock mode to validate pipeline plumbing, not to establish final study findings.
- Final publication use should rely on live OpenAI coding with a valid API key and a larger balanced dataset.
"""


def normalize_list_values(values: Iterable[Any]) -> list[str]:
    cleaned = []
    for value in values or []:
        item = normalize_text(value)
        if item:
            cleaned.append(item)
    return cleaned


def normalize_evidence(values: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    evidence = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        claim = normalize_text(item.get("claim"))
        quote = normalize_text(item.get("quote"))
        if claim or quote:
            evidence.append({"claim": claim, "quote": quote})
    return evidence


def normalize_thread_code(code: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(code)
    normalized["fatigue_types"] = normalize_list_values(code.get("fatigue_types", []))
    normalized["barriers"] = normalize_list_values(code.get("barriers", []))
    normalized["support_patterns"] = normalize_list_values(code.get("support_patterns", []))
    normalized["app_mentions"] = normalize_list_values(code.get("app_mentions", []))
    normalized["design_implications"] = normalize_list_values(code.get("design_implications", []))
    normalized["post_evidence"] = normalize_evidence(code.get("post_evidence", []))
    normalized["comment_evidence"] = normalize_evidence(code.get("comment_evidence", []))
    normalized["analyst_memo"] = normalize_text(code.get("analyst_memo", ""))
    normalized["confidence"] = float(code.get("confidence", 0) or 0)
    normalized["fatigue_salience"] = int(code.get("fatigue_salience", 0) or 0)
    return normalized


def jaccard_similarity(left: list[str], right: list[str]) -> float:
    left_set = set(left or [])
    right_set = set(right or [])
    if not left_set and not right_set:
        return 1.0
    union = left_set | right_set
    if not union:
        return 1.0
    return len(left_set & right_set) / len(union)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    phat = successes / total
    denom = 1 + z**2 / total
    center = (phat + z**2 / (2 * total)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * total)) / total) / denom
    return center - margin, center + margin


def list_counter(frame: pd.DataFrame, field: str) -> Counter:
    counter: Counter = Counter()
    for values in frame[field]:
        for value in values or []:
            counter[value] += 1
    return counter


def counter_to_frame(counter: Counter, column_name: str) -> pd.DataFrame:
    rows = [{"label": label, column_name: count} for label, count in counter.most_common()]
    return pd.DataFrame(rows)


def agreement_rate(frame: pd.DataFrame, coder_col: str, audit_col: str) -> float:
    if frame.empty:
        return 0.0
    return float((frame[coder_col] == frame[audit_col]).mean())


def mean_jaccard(frame: pd.DataFrame, coder_col: str, audit_col: str) -> float:
    if frame.empty:
        return 0.0
    scores = [
        jaccard_similarity(coder_values, audit_values)
        for coder_values, audit_values in zip(frame[coder_col], frame[audit_col])
    ]
    return float(sum(scores) / len(scores))


def usage_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    for row in rows:
        usage = row.get("usage") or {}
        input_tokens += int(usage.get("input_tokens", 0) or 0)
        output_tokens += int(usage.get("output_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
    return {
        "requests": len(rows),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def serialize_for_excel(frame: pd.DataFrame) -> pd.DataFrame:
    copy = frame.copy()
    for column in copy.columns:
        copy[column] = copy[column].apply(
            lambda value: EXCEL_ILLEGAL_CHARACTERS_RE.sub("", json.dumps(value, ensure_ascii=True))
            if isinstance(value, (list, dict))
            else EXCEL_ILLEGAL_CHARACTERS_RE.sub("", value)
            if isinstance(value, str)
            else value
        )
    return copy


def build_findings_payload(
    coded_threads: pd.DataFrame,
    subreddit_summary: pd.DataFrame,
    yearly_summary: pd.DataFrame,
    fatigue_freq: pd.DataFrame,
    barrier_freq: pd.DataFrame,
    support_freq: pd.DataFrame,
) -> dict[str, Any]:
    fatigue_examples = []
    for _, row in coded_threads.head(12).iterrows():
        fatigue_examples.append(
            {
                "submission_id": row["submission_id"],
                "subreddit": row["subreddit"],
                "fatigue_present": bool(row["final_fatigue_present"]),
                "fatigue_types": row["final_fatigue_types"],
                "support_balance": row["final_support_balance"],
                "post_evidence": row["final_post_evidence"][:2],
                "comment_evidence": row["final_comment_evidence"][:2],
            }
        )

    payload = {
        "sample": {
            "threads_coded": int(len(coded_threads)),
            "subreddits": sorted(coded_threads["subreddit"].dropna().unique().tolist()),
            "fatigue_threads": int(coded_threads["final_fatigue_present"].sum()),
            "help_seeking_threads": int(coded_threads["final_help_seeking"].sum()),
        },
        "subreddit_summary": subreddit_summary.to_dict(orient="records"),
        "yearly_summary": yearly_summary.to_dict(orient="records"),
        "fatigue_frequency": fatigue_freq.to_dict(orient="records"),
        "barrier_frequency": barrier_freq.to_dict(orient="records"),
        "support_frequency": support_freq.to_dict(orient="records"),
        "evidence_examples": fatigue_examples,
    }
    return payload


def write_methods_appendix(
    output_path: Path,
    *,
    args: argparse.Namespace,
    posts_count: int,
    comments_count: int,
    threads_count: int,
    failed_threads_count: int,
    usage: dict[str, Any],
) -> None:
    methods = f"""# Automated LLM Methods Appendix

## Data Inputs

- Posts file: `{args.posts}`
- Comments file: `{args.comments}`
- Posts loaded within rolling {args.years_back}-year window: `{posts_count}`
- Comments loaded within rolling {args.years_back}-year window: `{comments_count}`
- Threads coded: `{threads_count}`
- Threads skipped after repeated API/parsing failure: `{failed_threads_count}`

## Analytic Unit

The primary analytic unit was a Reddit thread consisting of one submission plus a sampled set of comments. Comment sampling used a mixed strategy per thread:

- highest-scoring comments, to capture the most visible community responses
- earliest comments, to capture the immediate response pattern

The maximum sampled comments per thread was `{args.max_comments_per_thread}`.

## LLM Coding Design

- First-pass coding model: `{args.primary_model}`
- Audit/adjudication model: `{args.audit_model}`
- Synthesis model: `{args.synthesis_model}`
- Coding reasoning effort: `{args.coding_reasoning_effort}`
- Audit reasoning effort: `{args.audit_reasoning_effort}`
- Synthesis reasoning effort: `{args.synthesis_reasoning_effort}`
- Temperature: `{args.temperature}`

Each thread was coded twice:

1. A structured first-pass code
2. A second-pass audit/adjudication using the raw thread packet plus the first-pass code

This design was used to reduce over-interpretation, improve internal consistency, and make the automated coding more defensible than a single-pass surface labeling workflow.

## Output Structure

The structured code captured:

- in-scope decision
- post focus
- fatigue presence and salience
- fatigue mechanisms
- barriers
- churn/disengagement signal
- help-seeking behavior
- peer-support patterns
- support balance
- actionable versus potentially harmful advice
- app mentions
- design implications
- evidence excerpts from both the post and comments
- coder confidence

## Reproducibility

- API outputs were cached locally to support resumption and auditability
- Structured outputs were requested using a strict JSON schema
- Token usage summary:
  - requests: `{usage.get("requests", 0)}`
  - input tokens: `{usage.get("input_tokens", 0)}`
  - output tokens: `{usage.get("output_tokens", 0)}`
  - total tokens: `{usage.get("total_tokens", 0)}`
"""
    output_path.write_text(methods, encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.mock_llm:
        require_openai_key()

    output_dir = Path(args.output_dir)
    cache_dir = output_dir / ".cache"
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    posts = load_posts(Path(args.posts), args.years_back)
    comments = load_comments(Path(args.comments), posts, args.years_back)
    packets = build_thread_packets(posts, comments, args.max_comments_per_thread, args.thread_limit)

    if not packets:
        raise RuntimeError("No thread packets were built from the supplied inputs.")

    client = OpenAI() if not args.mock_llm else None

    coding_rows: list[dict[str, Any]] = []
    failed_rows: list[dict[str, Any]] = []
    usage_rows: list[dict[str, Any]] = []

    for index, packet in enumerate(packets, start=1):
        record_id = packet["submission_id"]
        print(f"[{index}/{len(packets)}] Coding thread {record_id} ({packet['subreddit']})")
        current_stage = "first_pass"
        try:
            first_payload = {
                "model": args.primary_model,
                "schema": THREAD_CODE_SCHEMA,
                "developer_prompt": THREAD_CODER_PROMPT,
                "user_prompt": build_thread_user_prompt(packet),
                "reasoning_effort": args.coding_reasoning_effort,
                "temperature": args.temperature,
            }
            first_cache = cache_path_for(cache_dir, "thread_first_pass", record_id, first_payload)
            first_result = None if args.force_refresh else load_cached(first_cache)
            if first_result is None:
                if args.mock_llm:
                    first_result = {
                        "parsed": mock_thread_code(packet, audit=False),
                        "response_id": "mock_first_pass",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        "model": "mock-llm",
                    }
                else:
                    first_result = call_structured_response(
                        client,
                        model=args.primary_model,
                        schema_name="thread_code",
                        schema=THREAD_CODE_SCHEMA,
                        developer_prompt=THREAD_CODER_PROMPT,
                        user_prompt=first_payload["user_prompt"],
                        reasoning_effort=args.coding_reasoning_effort,
                        temperature=args.temperature,
                        metadata={"stage": "first_pass", "submission_id": record_id},
                    )
                save_cached(first_cache, first_result)
                if args.sleep_between_calls:
                    time.sleep(args.sleep_between_calls)

            first_code = normalize_thread_code(first_result["parsed"])

            current_stage = "audit_pass"
            audit_payload = {
                "model": args.audit_model,
                "schema": THREAD_CODE_SCHEMA,
                "developer_prompt": THREAD_AUDITOR_PROMPT,
                "user_prompt": build_audit_user_prompt(packet, first_code),
                "reasoning_effort": args.audit_reasoning_effort,
                "temperature": args.temperature,
            }
            audit_cache = cache_path_for(cache_dir, "thread_audit_pass", record_id, audit_payload)
            audit_result = None if args.force_refresh else load_cached(audit_cache)
            if audit_result is None:
                if args.mock_llm:
                    audit_result = {
                        "parsed": mock_thread_code(packet, audit=True),
                        "response_id": "mock_audit_pass",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        "model": "mock-llm",
                    }
                else:
                    audit_result = call_structured_response(
                        client,
                        model=args.audit_model,
                        schema_name="thread_code_audit",
                        schema=THREAD_CODE_SCHEMA,
                        developer_prompt=THREAD_AUDITOR_PROMPT,
                        user_prompt=audit_payload["user_prompt"],
                        reasoning_effort=args.audit_reasoning_effort,
                        temperature=args.temperature,
                        metadata={"stage": "audit_pass", "submission_id": record_id},
                    )
                save_cached(audit_cache, audit_result)
                if args.sleep_between_calls:
                    time.sleep(args.sleep_between_calls)

            final_code = normalize_thread_code(audit_result["parsed"])
        except Exception as exc:  # noqa: BLE001
            failed_rows.append(
                {
                    "submission_id": record_id,
                    "subreddit": packet["subreddit"],
                    "stage": current_stage,
                    "error": str(exc),
                    "title": packet["title"],
                    "sampled_comment_count": packet["sampled_comment_count"],
                    "available_comment_count": packet["available_comment_count"],
                }
            )
            print(
                f"Skipping thread {record_id} ({packet['subreddit']}) after {current_stage} failure: {exc}",
                file=sys.stderr,
            )
            continue

        usage_rows.extend(
            [
                {
                    "stage": "first_pass",
                    "submission_id": record_id,
                    "usage": first_result.get("usage", {}),
                    "model": first_result.get("model"),
                    "response_id": first_result.get("response_id"),
                },
                {
                    "stage": "audit_pass",
                    "submission_id": record_id,
                    "usage": audit_result.get("usage", {}),
                    "model": audit_result.get("model"),
                    "response_id": audit_result.get("response_id"),
                },
            ]
        )

        coding_rows.append(
            {
                "submission_id": record_id,
                "subreddit": packet["subreddit"],
                "created_at": packet["created_at"],
                "thread_year": packet["thread_year"],
                "thread_month": packet["thread_month"],
                "sampled_comment_count": packet["sampled_comment_count"],
                "available_comment_count": packet["available_comment_count"],
                "has_tracking_terms": packet["has_tracking_terms"],
                "title": packet["title"],
                "post_text": packet["post_text"],
                "comment_sample": packet["comment_sample"],
                "coder_json": first_code,
                "audit_json": final_code,
                "coder_thread_included": first_code["thread_included"],
                "audit_thread_included": final_code["thread_included"],
                "coder_scope_decision": first_code["scope_decision"],
                "audit_scope_decision": final_code["scope_decision"],
                "coder_post_focus": first_code["post_focus"],
                "audit_post_focus": final_code["post_focus"],
                "coder_fatigue_present": first_code["fatigue_present"],
                "audit_fatigue_present": final_code["fatigue_present"],
                "coder_churn_signal": first_code["churn_signal"],
                "audit_churn_signal": final_code["churn_signal"],
                "coder_support_balance": first_code["support_balance"],
                "audit_support_balance": final_code["support_balance"],
                "coder_fatigue_types": first_code["fatigue_types"],
                "audit_fatigue_types": final_code["fatigue_types"],
                "coder_support_patterns": first_code["support_patterns"],
                "audit_support_patterns": final_code["support_patterns"],
                "coder_confidence": first_code["confidence"],
                "audit_confidence": final_code["confidence"],
                "final_thread_included": final_code["thread_included"],
                "final_scope_decision": final_code["scope_decision"],
                "final_post_focus": final_code["post_focus"],
                "final_fatigue_present": final_code["fatigue_present"],
                "final_fatigue_salience": final_code["fatigue_salience"],
                "final_fatigue_types": final_code["fatigue_types"],
                "final_barriers": final_code["barriers"],
                "final_churn_signal": final_code["churn_signal"],
                "final_help_seeking": final_code["help_seeking"],
                "final_support_patterns": final_code["support_patterns"],
                "final_support_balance": final_code["support_balance"],
                "final_actionable_advice_present": final_code["actionable_advice_present"],
                "final_harmful_or_extreme_advice_present": final_code["harmful_or_extreme_advice_present"],
                "final_app_mentions": final_code["app_mentions"],
                "final_design_implications": final_code["design_implications"],
                "final_post_evidence": final_code["post_evidence"],
                "final_comment_evidence": final_code["comment_evidence"],
                "final_analyst_memo": final_code["analyst_memo"],
                "final_confidence": final_code["confidence"],
            }
        )

    if not coding_rows:
        failed_threads = pd.DataFrame(failed_rows)
        serialize_for_excel(failed_threads).to_excel(output_dir / "LLM_Publication_FailedThreads.xlsx", index=False)
        raise RuntimeError(
            "No threads were successfully coded. See LLM_Publication_FailedThreads.xlsx for the failure log."
        )

    coded_threads = pd.DataFrame(coding_rows)
    included_threads = coded_threads[coded_threads["final_thread_included"]].reset_index(drop=True)
    if included_threads.empty:
        raise RuntimeError("All threads were marked out of scope by the final coding pass.")

    fatigue_counter = list_counter(included_threads, "final_fatigue_types")
    barrier_counter = list_counter(included_threads, "final_barriers")
    support_counter = list_counter(included_threads, "final_support_patterns")

    fatigue_frequency = counter_to_frame(fatigue_counter, "thread_count")
    barrier_frequency = counter_to_frame(barrier_counter, "thread_count")
    support_frequency = counter_to_frame(support_counter, "thread_count")

    subreddit_summary = included_threads.groupby("subreddit").agg(
        threads=("submission_id", "count"),
        fatigue_threads=("final_fatigue_present", "sum"),
        mean_fatigue_salience=("final_fatigue_salience", "mean"),
        help_seeking_threads=("final_help_seeking", "sum"),
        actionable_advice_threads=("final_actionable_advice_present", "sum"),
        harmful_advice_threads=("final_harmful_or_extreme_advice_present", "sum"),
        mean_confidence=("final_confidence", "mean"),
    ).reset_index()

    subreddit_summary["fatigue_rate"] = subreddit_summary["fatigue_threads"] / subreddit_summary["threads"]
    subreddit_summary["help_seeking_rate"] = subreddit_summary["help_seeking_threads"] / subreddit_summary["threads"]
    subreddit_summary["actionable_advice_rate"] = (
        subreddit_summary["actionable_advice_threads"] / subreddit_summary["threads"]
    )
    subreddit_summary["harmful_advice_rate"] = subreddit_summary["harmful_advice_threads"] / subreddit_summary["threads"]
    ci_bounds = subreddit_summary.apply(
        lambda row: wilson_interval(int(row["fatigue_threads"]), int(row["threads"])),
        axis=1,
        result_type="expand",
    )
    subreddit_summary["fatigue_rate_ci_low"] = ci_bounds[0]
    subreddit_summary["fatigue_rate_ci_high"] = ci_bounds[1]

    yearly_summary = included_threads.groupby("thread_year").agg(
        threads=("submission_id", "count"),
        fatigue_threads=("final_fatigue_present", "sum"),
        help_seeking_threads=("final_help_seeking", "sum"),
        mean_fatigue_salience=("final_fatigue_salience", "mean"),
        harmful_advice_threads=("final_harmful_or_extreme_advice_present", "sum"),
    ).reset_index()
    yearly_summary["fatigue_rate"] = yearly_summary["fatigue_threads"] / yearly_summary["threads"]
    yearly_summary["help_seeking_rate"] = yearly_summary["help_seeking_threads"] / yearly_summary["threads"]
    yearly_summary["harmful_advice_rate"] = yearly_summary["harmful_advice_threads"] / yearly_summary["threads"]

    agreement_audit = {
        "thread_count_total": int(len(coded_threads)),
        "thread_count_included": int(len(included_threads)),
        "thread_included_exact_agreement": agreement_rate(coded_threads, "coder_thread_included", "audit_thread_included"),
        "scope_decision_exact_agreement": agreement_rate(coded_threads, "coder_scope_decision", "audit_scope_decision"),
        "post_focus_exact_agreement": agreement_rate(coded_threads, "coder_post_focus", "audit_post_focus"),
        "fatigue_present_exact_agreement": agreement_rate(coded_threads, "coder_fatigue_present", "audit_fatigue_present"),
        "churn_signal_exact_agreement": agreement_rate(coded_threads, "coder_churn_signal", "audit_churn_signal"),
        "support_balance_exact_agreement": agreement_rate(coded_threads, "coder_support_balance", "audit_support_balance"),
        "fatigue_types_mean_jaccard": mean_jaccard(coded_threads, "coder_fatigue_types", "audit_fatigue_types"),
        "support_patterns_mean_jaccard": mean_jaccard(coded_threads, "coder_support_patterns", "audit_support_patterns"),
        "coder_mean_confidence": float(coded_threads["coder_confidence"].mean()),
        "audit_mean_confidence": float(coded_threads["audit_confidence"].mean()),
    }

    findings_payload = build_findings_payload(
        included_threads,
        subreddit_summary,
        yearly_summary,
        fatigue_frequency,
        barrier_frequency,
        support_frequency,
    )

    synthesis_cache_payload = {
        "model": args.synthesis_model,
        "developer_prompt": SYNTHESIS_PROMPT,
        "payload": findings_payload,
        "reasoning_effort": args.synthesis_reasoning_effort,
        "temperature": args.temperature,
    }
    synthesis_cache = cache_path_for(cache_dir, "synthesis_report", "all_threads", synthesis_cache_payload)
    synthesis_result = None if args.force_refresh else load_cached(synthesis_cache)
    if synthesis_result is None:
        if args.mock_llm:
            synthesis_result = {
                "text": mock_synthesis(findings_payload),
                "response_id": "mock_synthesis",
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                "model": "mock-llm",
            }
        else:
            synthesis_result = call_text_response(
                client,
                model=args.synthesis_model,
                developer_prompt=SYNTHESIS_PROMPT,
                user_prompt=json.dumps(findings_payload, indent=2, ensure_ascii=True),
                reasoning_effort=args.synthesis_reasoning_effort,
                temperature=args.temperature,
                metadata={"stage": "synthesis_report", "submission_id": "all_threads"},
            )
        save_cached(synthesis_cache, synthesis_result)

    usage_rows.append(
        {
            "stage": "synthesis_report",
            "submission_id": "all_threads",
            "usage": synthesis_result.get("usage", {}),
            "model": synthesis_result.get("model"),
            "response_id": synthesis_result.get("response_id"),
        }
    )
    usage = usage_summary(usage_rows)

    evidence_rows = []
    for _, row in included_threads.iterrows():
        for item in row["final_post_evidence"]:
            evidence_rows.append(
                {
                    "submission_id": row["submission_id"],
                    "subreddit": row["subreddit"],
                    "evidence_source": "post",
                    "claim": item.get("claim", ""),
                    "quote": item.get("quote", ""),
                }
            )
        for item in row["final_comment_evidence"]:
            evidence_rows.append(
                {
                    "submission_id": row["submission_id"],
                    "subreddit": row["subreddit"],
                    "evidence_source": "comment",
                    "claim": item.get("claim", ""),
                    "quote": item.get("quote", ""),
                }
            )
    evidence_table = pd.DataFrame(evidence_rows)
    failed_threads = pd.DataFrame(failed_rows)

    serialize_for_excel(coded_threads).to_excel(output_dir / "LLM_Publication_ThreadCodes.xlsx", index=False)
    serialize_for_excel(included_threads).to_excel(output_dir / "LLM_Publication_IncludedThreads.xlsx", index=False)
    serialize_for_excel(evidence_table).to_excel(output_dir / "LLM_Publication_EvidenceTable.xlsx", index=False)
    serialize_for_excel(failed_threads).to_excel(output_dir / "LLM_Publication_FailedThreads.xlsx", index=False)
    subreddit_summary.to_excel(output_dir / "LLM_Publication_SubredditSummary.xlsx", index=False)
    yearly_summary.to_excel(output_dir / "LLM_Publication_YearlySummary.xlsx", index=False)
    fatigue_frequency.to_excel(output_dir / "LLM_Publication_FatigueFrequency.xlsx", index=False)
    barrier_frequency.to_excel(output_dir / "LLM_Publication_BarrierFrequency.xlsx", index=False)
    support_frequency.to_excel(output_dir / "LLM_Publication_SupportFrequency.xlsx", index=False)
    serialize_for_excel(pd.DataFrame(usage_rows)).to_excel(output_dir / "LLM_Publication_UsageLog.xlsx", index=False)

    with (output_dir / "LLM_Publication_QualityAudit.json").open("w", encoding="utf-8") as handle:
        json.dump(agreement_audit, handle, indent=2, ensure_ascii=True)

    with (output_dir / "LLM_Publication_FindingsPayload.json").open("w", encoding="utf-8") as handle:
        json.dump(findings_payload, handle, indent=2, ensure_ascii=True)

    with (output_dir / "LLM_Publication_UsageSummary.json").open("w", encoding="utf-8") as handle:
        json.dump(usage, handle, indent=2, ensure_ascii=True)

    findings_report = synthesis_result["text"].strip() + "\n"
    (output_dir / "LLM_Publication_Report.md").write_text(findings_report, encoding="utf-8")

    write_methods_appendix(
        output_dir / "LLM_Publication_MethodsAppendix.md",
        args=args,
        posts_count=len(posts),
        comments_count=len(comments),
        threads_count=len(coded_threads),
        failed_threads_count=len(failed_threads),
        usage=usage,
    )

    print("Publication-grade LLM analysis complete.")
    print(f"Outputs written to: {output_dir}")
    print(f"Threads coded: {len(coded_threads)}")
    print(f"Included threads: {len(included_threads)}")
    print(f"Skipped threads: {len(failed_threads)}")


if __name__ == "__main__":
    main()