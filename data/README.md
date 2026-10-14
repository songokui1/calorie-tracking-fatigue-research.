# Dataset availability

Raw Reddit posts and comments are intentionally excluded from this public repository.

The collection script creates Excel exports containing post text, comment text, usernames, thread IDs, and timestamps. These may include identifiable health-related narratives.

To reproduce the data collection, configure Reddit API credentials and run `script.py` or `run_full_study.sh` in your own environment, subject to platform terms and institutional research requirements. Avoid redistributing raw exports without appropriate review.

Expected exports include `all_reddit_posts.xlsx`, `all_reddit_comments.xlsx`, `reddit_sampling_summary.xlsx`, and `reddit_year_balance_summary.xlsx`. The baseline pipeline reads the first two files from its working directory.
