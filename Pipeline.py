import os

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer, util
from textblob import TextBlob


YEARS_BACK = int(os.getenv("REDDIT_YEARS_BACK", "5"))
MODEL_NAME = os.getenv("SENTENCE_MODEL_NAME", "all-MiniLM-L6-v2")
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


def normalize_text(text):
    return " ".join((text or "").lower().split())


def is_tracking_related(text):
    normalized = normalize_text(text)
    if not normalized or normalized in {"[deleted]", "[removed]"}:
        return False
    phrases = TRACKING_PHRASES + FATIGUE_PHRASES
    return any(phrase in normalized for phrase in phrases)


def to_utc_naive_timestamp():
    return pd.Timestamp.now(tz="UTC").tz_localize(None)


def scale_cosine(scores):
    return (scores + 1.0) / 2.0


def cosine_scores(embeddings, prototype_vector):
    return util.cos_sim(embeddings, prototype_vector).cpu().numpy().ravel()


def breakdown_stage(collection_raw, reflection_raw, thresh=0.35, margin=0.03):
    if collection_raw < thresh and reflection_raw < thresh:
        return "none"
    if collection_raw > reflection_raw + margin:
        return "collection"
    if reflection_raw > collection_raw + margin:
        return "reflection"
    return "mixed"


def support_type(info_raw, emo_raw, thresh=0.35, margin=0.03):
    if info_raw < thresh and emo_raw < thresh:
        return "none"
    if info_raw > emo_raw + margin:
        return "informational"
    if emo_raw > info_raw + margin:
        return "emotional"
    return "mixed"


def tag_fatigue(text):
    normalized = normalize_text(text)
    tags = []
    if any(k in normalized for k in ["bug", "crash", "lag", "slow", "glitch"]):
        tags.append("technical_fatigue")
    if any(k in normalized for k in ["anxious", "guilty", "ashamed", "burned out", "overwhelmed"]):
        tags.append("emotional_fatigue")
    if any(k in normalized for k in ["friends", "community", "support group"]):
        tags.append("social_fatigue")
    if any(k in normalized for k in ["paywall", "subscription", "premium", "ads", "upgrade"]):
        tags.append("monetization_fatigue")
    if any(k in normalized for k in ["mentally draining", "cognitive load", "too much to think"]):
        tags.append("cognitive_fatigue")
    return ",".join(tags) if tags else "none"


def prepare_posts(posts_df):
    posts_df = posts_df.copy()
    posts_df["subreddit"] = posts_df["subreddit"].astype(str).str.lower()
    posts_df["text"] = (
        posts_df["title"].fillna("") + " " + posts_df["selftext"].fillna("")
    ).str.strip()
    posts_df["created_dt"] = pd.to_datetime(posts_df["created_utc"], unit="s", errors="coerce")

    now = to_utc_naive_timestamp()
    start = now - pd.DateOffset(years=YEARS_BACK)

    posts_df = posts_df.dropna(subset=["created_dt"])
    posts_df = posts_df[(posts_df["created_dt"] >= start) & (posts_df["created_dt"] <= now)]
    posts_df = posts_df[posts_df["text"].apply(is_tracking_related)]
    posts_df = posts_df.drop_duplicates(subset=["submission_id"])

    return posts_df, start, now


def prepare_comments(comments_df, posts_df, start, now):
    comments_df = comments_df.copy()

    if "subreddit" not in comments_df.columns:
        comments_df = comments_df.merge(
            posts_df[["submission_id", "subreddit"]],
            on="submission_id",
            how="left",
        )

    comments_df["subreddit"] = comments_df["subreddit"].astype(str).str.lower()
    comments_df["body"] = comments_df["body"].fillna("").str.strip()
    comments_df["comment_created_dt"] = pd.to_datetime(
        comments_df["comment_created_utc"], unit="s", errors="coerce"
    )

    comments_df = comments_df.dropna(subset=["comment_created_dt"])
    comments_df = comments_df[
        (comments_df["comment_created_dt"] >= start) & (comments_df["comment_created_dt"] <= now)
    ]
    comments_df = comments_df[comments_df["submission_id"].isin(posts_df["submission_id"])]
    comments_df = comments_df[comments_df["body"].ne("")]
    comments_df = comments_df[~comments_df["body"].isin(["[deleted]", "[removed]"])]
    comments_df = comments_df.drop_duplicates(subset=["comment_id"])

    return comments_df


def average_subreddit_means(df, group_cols, metric_map):
    by_subreddit = df.groupby(group_cols + ["subreddit"]).agg(**metric_map).reset_index()
    overall = by_subreddit.groupby(group_cols).agg(
        **{column: (column, "mean") for column in metric_map}
    ).reset_index()
    counts = by_subreddit.groupby(group_cols)["subreddit"].nunique().reset_index(name="subreddits_included")
    return overall.merge(counts, on=group_cols, how="left")


posts_df = pd.read_excel("all_reddit_posts.xlsx")
comments_df = pd.read_excel("all_reddit_comments.xlsx")

posts_df, start, now = prepare_posts(posts_df)

if posts_df.empty:
    raise ValueError("No Reddit posts remained after the 5-year and relevance filters.")

comments_df = prepare_comments(comments_df, posts_df, start, now)

model = SentenceTransformer(MODEL_NAME)

post_embeddings = model.encode(
    posts_df["text"].tolist(),
    convert_to_tensor=True,
    show_progress_bar=True,
)

cognitive_load = [
    "too much to think about",
    "mentally exhausting",
    "overwhelming to keep up",
    "hard to remember everything I eat",
    "constantly thinking about food logging",
]
data_entry = [
    "logging every bite is tedious",
    "manual entry is tedious",
    "takes forever to add food",
    "too many steps to log a meal",
    "searching the database is frustrating",
]
monetization = [
    "tired of paywalls",
    "sick of ads",
    "exhausted by upsells",
    "features locked behind subscription",
    "annoyed by constant upgrade prompts",
]
collection_phrases = [
    "logging is too much work",
    "tired of entering everything",
    "forget to log",
    "hard to keep up with tracking",
    "data entry is annoying",
    "too many steps to log food",
]
reflection_phrases = [
    "hard to understand the charts",
    "confusing insights",
    "don't know what the numbers mean",
    "overwhelmed by all the stats",
    "not sure how to use the data",
    "graphs are hard to interpret",
]
info_phrases = [
    "here's what worked for me",
    "try turning off this feature",
    "use barcode scanning instead",
    "set reminders to reduce forgetting",
    "here is a workaround",
    "you can simplify logging by",
]
emo_phrases = [
    "I feel the same way",
    "you're not alone",
    "this is exhausting",
    "I get your burnout",
    "it's okay to take a break",
    "I understand how draining this is",
]

fatigue_vec = model.encode(cognitive_load + data_entry + monetization, convert_to_tensor=True).mean(dim=0)
collection_vec = model.encode(collection_phrases, convert_to_tensor=True).mean(dim=0)
reflection_vec = model.encode(reflection_phrases, convert_to_tensor=True).mean(dim=0)
info_vec = model.encode(info_phrases, convert_to_tensor=True).mean(dim=0)
emo_vec = model.encode(emo_phrases, convert_to_tensor=True).mean(dim=0)

posts_df["fatigue_index_raw"] = cosine_scores(post_embeddings, fatigue_vec)
posts_df["fatigue_index"] = scale_cosine(posts_df["fatigue_index_raw"])
posts_df["collection_score_raw"] = cosine_scores(post_embeddings, collection_vec)
posts_df["collection_score"] = scale_cosine(posts_df["collection_score_raw"])
posts_df["reflection_score_raw"] = cosine_scores(post_embeddings, reflection_vec)
posts_df["reflection_score"] = scale_cosine(posts_df["reflection_score_raw"])
posts_df["breakdown_stage"] = [
    breakdown_stage(c, r)
    for c, r in zip(posts_df["collection_score_raw"], posts_df["reflection_score_raw"])
]
posts_df["fatigue_taxonomy"] = posts_df["text"].apply(tag_fatigue)
posts_df["sentiment"] = posts_df["text"].apply(lambda text: TextBlob(text).sentiment.polarity)
posts_df["month"] = posts_df["created_dt"].dt.to_period("M").astype(str)
posts_df["month_num"] = posts_df["created_dt"].dt.month
posts_df["cohort"] = np.where(posts_df["month_num"].isin([1, 2]), "new_year", "other")

if comments_df.empty:
    comments_support_summary = pd.DataFrame(
        columns=[
            "submission_id",
            "comment_count",
            "mean_info_score",
            "mean_emo_score",
            "supportive_comment_rate",
            "informational_comment_rate",
            "emotional_comment_rate",
            "mixed_support_rate",
        ]
    )
else:
    comment_embeddings = model.encode(
        comments_df["body"].tolist(),
        convert_to_tensor=True,
        show_progress_bar=True,
    )
    comments_df["info_score_raw"] = cosine_scores(comment_embeddings, info_vec)
    comments_df["info_score"] = scale_cosine(comments_df["info_score_raw"])
    comments_df["emo_score_raw"] = cosine_scores(comment_embeddings, emo_vec)
    comments_df["emo_score"] = scale_cosine(comments_df["emo_score_raw"])
    comments_df["support_type"] = [
        support_type(i, e)
        for i, e in zip(comments_df["info_score_raw"], comments_df["emo_score_raw"])
    ]

    comments_support_summary = comments_df.groupby("submission_id").agg(
        comment_count=("comment_id", "nunique"),
        mean_info_score=("info_score", "mean"),
        mean_emo_score=("emo_score", "mean"),
        supportive_comment_rate=("support_type", lambda s: (s != "none").mean()),
        informational_comment_rate=("support_type", lambda s: (s == "informational").mean()),
        emotional_comment_rate=("support_type", lambda s: (s == "emotional").mean()),
        mixed_support_rate=("support_type", lambda s: (s == "mixed").mean()),
    ).reset_index()

posts_df = posts_df.merge(comments_support_summary, on="submission_id", how="left")
posts_df["comment_count"] = posts_df["comment_count"].fillna(0).astype(int)

subreddit_summary = posts_df.groupby("subreddit").agg(
    post_count=("submission_id", "nunique"),
    mean_sentiment=("sentiment", "mean"),
    mean_fatigue=("fatigue_index", "mean"),
    mean_collection=("collection_score", "mean"),
    mean_reflection=("reflection_score", "mean"),
    mean_comment_count=("comment_count", "mean"),
    mean_supportive_comment_rate=("supportive_comment_rate", "mean"),
).reset_index()

balance_audit = posts_df.groupby("subreddit").agg(
    post_count=("submission_id", "nunique"),
    unique_authors=("author", pd.Series.nunique),
).reset_index()

if not comments_df.empty:
    comment_counts = comments_df.groupby("subreddit").agg(
        comment_count=("comment_id", "nunique"),
        unique_comment_authors=("comment_author", pd.Series.nunique),
    ).reset_index()
    balance_audit = balance_audit.merge(comment_counts, on="subreddit", how="left")
else:
    balance_audit["comment_count"] = 0
    balance_audit["unique_comment_authors"] = 0

reddit_monthly = average_subreddit_means(
    posts_df,
    ["month"],
    {
        "mean_sentiment": ("sentiment", "mean"),
        "mean_fatigue": ("fatigue_index", "mean"),
        "mean_collection": ("collection_score", "mean"),
        "mean_reflection": ("reflection_score", "mean"),
        "mean_supportive_comment_rate": ("supportive_comment_rate", "mean"),
    },
)

reddit_cohort_stats = average_subreddit_means(
    posts_df,
    ["cohort"],
    {
        "mean_sentiment": ("sentiment", "mean"),
        "mean_fatigue": ("fatigue_index", "mean"),
        "mean_collection": ("collection_score", "mean"),
        "mean_reflection": ("reflection_score", "mean"),
        "mean_supportive_comment_rate": ("supportive_comment_rate", "mean"),
    },
)

posts_df.to_excel("Phase2_Reddit_FullAnalysis.xlsx", index=False)
comments_df.to_excel("Phase2_Reddit_CommentAnalysis.xlsx", index=False)
reddit_monthly.to_excel("Phase2_Reddit_Temporal.xlsx", index=False)
reddit_cohort_stats.to_excel("Phase2_Reddit_Cohorts.xlsx", index=False)
subreddit_summary.to_excel("Phase2_Reddit_SubredditSummary.xlsx", index=False)
balance_audit.to_excel("Phase2_Reddit_BalanceAudit.xlsx", index=False)

print("PHASE 2 COMPLETE.")
print(f"Post rows analyzed     : {len(posts_df)}")
print(f"Comment rows analyzed  : {len(comments_df)}")
print("Balanced outputs saved : Phase2_Reddit_Temporal.xlsx, Phase2_Reddit_Cohorts.xlsx")