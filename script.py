import os
import random
import time
import datetime as dt
from collections import Counter, defaultdict

import pandas as pd
import praw
from tqdm import tqdm


SMOKE_TEST = os.getenv("SMOKE_TEST", "").strip().lower() in {"1", "true", "yes"}
DEFAULT_SUBREDDITS = [
    "loseit",
    "caloriecount",
    "Myfitnesspal",
    "nutrition",
    "1200isplenty",
]
SMOKE_TEST_SUBREDDITS = ["Myfitnesspal"]
SUBREDDITS_TO_SCRAPE = [
    subreddit.strip()
    for subreddit in os.getenv(
        "REDDIT_SUBREDDITS",
        ",".join(SMOKE_TEST_SUBREDDITS if SMOKE_TEST else DEFAULT_SUBREDDITS),
    ).split(",")
    if subreddit.strip()
]
YEARS_BACK = int(os.getenv("REDDIT_YEARS_BACK", "5"))
POSTS_PER_SUBREDDIT_PER_YEAR = int(
    os.getenv("REDDIT_POSTS_PER_YEAR", "1" if SMOKE_TEST else "50")
)
REQUEST_SLEEP_SECONDS = float(
    os.getenv("REDDIT_SLEEP_SECONDS", "0" if SMOKE_TEST else "2")
)
SAMPLE_RANDOM_SEED = int(os.getenv("REDDIT_SAMPLE_SEED", "42"))

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
FATIGUE_PHRASES = [
    "burnout",
    "burned out",
    "fatigue",
    "tired of logging",
    "quit tracking",
    "stopped tracking",
    "obsessed with tracking",
    "mentally draining",
    "overwhelming to track",
    "too much work to log",
]


def required_env(name):
    value = os.getenv(name)
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, and REDDIT_USER_AGENT before running."
        )
    return value


def build_reddit_client():
    return praw.Reddit(
        client_id=required_env("REDDIT_CLIENT_ID"),
        client_secret=required_env("REDDIT_CLIENT_SECRET"),
        user_agent=required_env("REDDIT_USER_AGENT"),
    )


def normalize_text(text):
    return " ".join((text or "").lower().split())


def is_tracking_related(text):
    normalized = normalize_text(text)
    if not normalized or normalized in {"[deleted]", "[removed]"}:
        return False
    phrases = TRACKING_PHRASES + FATIGUE_PHRASES
    return any(phrase in normalized for phrase in phrases)


def empty_frame(columns):
    return pd.DataFrame(columns=columns)


reddit = build_reddit_client()
now = dt.datetime.now(dt.UTC)
min_epoch = int((now - dt.timedelta(days=YEARS_BACK * 365)).timestamp())

all_posts_master = []
all_comments_master = []
sampling_summary = []
year_balance_summary = []

post_columns = [
    "submission_id",
    "subreddit",
    "title",
    "selftext",
    "author",
    "created_utc",
    "score",
    "num_comments",
    "url",
]
comment_columns = [
    "submission_id",
    "subreddit",
    "comment_id",
    "comment_parent_id",
    "body",
    "comment_author",
    "comment_created_utc",
    "comment_score",
]

for sub_name in SUBREDDITS_TO_SCRAPE:
    print(f"\nCollecting balanced data from r/{sub_name} ...")

    subreddit_posts = []
    subreddit_comments = []
    scanned_posts = 0
    candidate_counts = Counter()
    selected_counts = Counter()
    candidates_by_year = defaultdict(list)

    subreddit = reddit.subreddit(sub_name)

    for submission in tqdm(subreddit.new(limit=None), desc=f"r/{sub_name} posts", unit="post"):
        scanned_posts += 1

        if submission.created_utc < min_epoch:
            break

        submission_year = dt.datetime.fromtimestamp(submission.created_utc, dt.UTC).year
        combined_text = f"{submission.title or ''} {submission.selftext or ''}".strip()

        if not is_tracking_related(combined_text):
            continue

        post_info = {
            "submission_id": submission.id,
            "subreddit": sub_name.lower(),
            "title": submission.title,
            "selftext": submission.selftext,
            "author": str(submission.author) if submission.author else None,
            "created_utc": submission.created_utc,
            "score": submission.score,
            "num_comments": submission.num_comments,
            "url": submission.url,
        }
        candidates_by_year[submission_year].append(
            {
                "submission": submission,
                "post_info": post_info,
            }
        )
        candidate_counts[submission_year] += 1

    selected_candidates = []
    for submission_year, year_candidates in sorted(candidates_by_year.items()):
        if len(year_candidates) <= POSTS_PER_SUBREDDIT_PER_YEAR:
            chosen = year_candidates
        else:
            rng = random.Random(f"{SAMPLE_RANDOM_SEED}:{sub_name.lower()}:{submission_year}")
            chosen = rng.sample(year_candidates, POSTS_PER_SUBREDDIT_PER_YEAR)
            chosen.sort(key=lambda item: item["post_info"]["created_utc"], reverse=True)

        selected_counts[submission_year] = len(chosen)
        year_balance_summary.append(
            {
                "subreddit": sub_name.lower(),
                "year": submission_year,
                "eligible_posts_in_year": candidate_counts[submission_year],
                "selected_posts_in_year": selected_counts[submission_year],
                "post_cap_per_year": POSTS_PER_SUBREDDIT_PER_YEAR,
                "sampling_strategy": (
                    "all_eligible_posts"
                    if candidate_counts[submission_year] <= POSTS_PER_SUBREDDIT_PER_YEAR
                    else "reproducible_random_sample"
                ),
            }
        )
        selected_candidates.extend(chosen)

    selected_candidates.sort(key=lambda item: item["post_info"]["created_utc"], reverse=True)

    for candidate in selected_candidates:
        submission = candidate["submission"]
        subreddit_posts.append(candidate["post_info"])

        submission.comments.replace_more(limit=None)
        for comment in submission.comments.list():
            body = (comment.body or "").strip()
            if not body or body in {"[deleted]", "[removed]"}:
                continue

            comment_info = {
                "submission_id": submission.id,
                "subreddit": sub_name.lower(),
                "comment_id": comment.id,
                "comment_parent_id": comment.parent_id,
                "body": body,
                "comment_author": str(comment.author) if comment.author else None,
                "comment_created_utc": comment.created_utc,
                "comment_score": comment.score,
            }
            subreddit_comments.append(comment_info)

    df_sub_posts = (
        pd.DataFrame(subreddit_posts)
        if subreddit_posts
        else empty_frame(post_columns)
    )
    df_sub_comments = (
        pd.DataFrame(subreddit_comments)
        if subreddit_comments
        else empty_frame(comment_columns)
    )

    df_sub_posts.to_excel(f"reddit_posts_{sub_name}.xlsx", index=False)
    df_sub_comments.to_excel(f"reddit_comments_{sub_name}.xlsx", index=False)

    print(f"\nSaved {len(df_sub_posts)} posts to 'reddit_posts_{sub_name}.xlsx'")
    print(f"Saved {len(df_sub_comments)} comments to 'reddit_comments_{sub_name}.xlsx'")

    all_posts_master.extend(subreddit_posts)
    all_comments_master.extend(subreddit_comments)
    sampling_summary.append(
        {
            "subreddit": sub_name.lower(),
            "scanned_posts_within_window": scanned_posts,
            "posts_collected": len(df_sub_posts),
            "comments_collected": len(df_sub_comments),
            "years_captured": ", ".join(str(year) for year in sorted(selected_counts)),
            "per_year_post_cap": POSTS_PER_SUBREDDIT_PER_YEAR,
        }
    )

    time.sleep(REQUEST_SLEEP_SECONDS)

print("\nData collection from all subreddits is complete.")

df_all_posts = pd.DataFrame(all_posts_master) if all_posts_master else empty_frame(post_columns)
df_all_comments = (
    pd.DataFrame(all_comments_master)
    if all_comments_master
    else empty_frame(comment_columns)
)
df_sampling_summary = pd.DataFrame(sampling_summary)
df_year_balance_summary = pd.DataFrame(year_balance_summary)

df_all_posts.to_excel("all_reddit_posts.xlsx", index=False)
df_all_comments.to_excel("all_reddit_comments.xlsx", index=False)
df_sampling_summary.to_excel("reddit_sampling_summary.xlsx", index=False)
df_year_balance_summary.to_excel("reddit_year_balance_summary.xlsx", index=False)

print("\nFinal combined data saved.")
print(f"  Total posts across all subreddits   : {len(df_all_posts)}")
print(f"  Total comments across all subreddits: {len(df_all_comments)}")
print("  Sampling summary saved              : reddit_sampling_summary.xlsx")
print("  Year balance summary saved          : reddit_year_balance_summary.xlsx")