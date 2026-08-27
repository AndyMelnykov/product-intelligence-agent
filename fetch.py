from datetime import date, datetime, timezone

import praw

import db
from credentials import get_secret
from state import load_state, save_state

REMOVED_MARKERS = {"[deleted]", "[removed]"}


def build_reddit_client():
    return praw.Reddit(
        client_id=get_secret("reddit_client_id"),
        client_secret=get_secret("reddit_client_secret"),
        user_agent=get_secret("reddit_user_agent"),
    )


def _is_removed(submission) -> bool:
    return submission.title in REMOVED_MARKERS or submission.selftext in REMOVED_MARKERS


def _post_to_dict(submission) -> dict:
    return {
        "id": submission.id,
        "title": submission.title,
        "selftext": submission.selftext,
        "permalink": submission.permalink,
        "score": submission.score,
        "num_comments": submission.num_comments,
        "created_utc": submission.created_utc,
    }


def fetch_new_posts(reddit_client, subreddit_name, last_seen_fullname, limit):
    subreddit = reddit_client.subreddit(subreddit_name)
    posts = []
    newest_fullname = last_seen_fullname

    for submission in subreddit.new(limit=limit):
        if newest_fullname == last_seen_fullname:
            newest_fullname = submission.fullname

        if submission.fullname == last_seen_fullname:
            break

        if _is_removed(submission):
            continue

        posts.append(_post_to_dict(submission))

    return posts, newest_fullname


def run(subreddits, fetch_limit_per_subreddit, state_path="data/state.json",
        db_path="data/pi_agent.db", today=None, reddit_client=None):
    reddit_client = reddit_client or build_reddit_client()
    state = load_state(state_path)
    run_date = today or date.today()
    captured_at = datetime(run_date.year, run_date.month, run_date.day, tzinfo=timezone.utc).isoformat()

    conn = db.connect(db_path)
    db.init_db(conn)

    new_state = dict(state)
    try:
        for subreddit_name in subreddits:
            last_seen = state.get(subreddit_name)
            posts, newest = fetch_new_posts(reddit_client, subreddit_name, last_seen, fetch_limit_per_subreddit)

            for post in posts:
                db.insert_evidence(
                    conn,
                    source_type="reddit_post",
                    source_name=subreddit_name,
                    source_url=post["permalink"],
                    captured_at=captured_at,
                    published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
                    title=post["title"],
                    content=post["selftext"],
                    metadata={"reddit_post_id": post["id"], "score": post["score"], "num_comments": post["num_comments"]},
                )

            new_state[subreddit_name] = newest
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()
    save_state(state_path, new_state)


if __name__ == "__main__":
    run(subreddits=[], fetch_limit_per_subreddit=100)
