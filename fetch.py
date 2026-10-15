from datetime import date, datetime, timezone

import praw
import prawcore

import db
from credentials import get_secret
from state import load_state

REMOVED_MARKERS = {"[deleted]", "[removed]"}


class FetchError(Exception):
    pass


def _wrap_prawcore_error(subreddit_name: str, error: prawcore.exceptions.PrawcoreException) -> FetchError:
    if isinstance(error, prawcore.exceptions.RequestException):
        return FetchError(
            f"r/{subreddit_name}: Reddit API unavailable (network error): {error.original_exception}"
        )
    if isinstance(error, prawcore.exceptions.TooManyRequests):
        return FetchError(f"r/{subreddit_name}: Reddit API rate-limited: {error}")
    if isinstance(error, (prawcore.exceptions.OAuthException, prawcore.exceptions.Forbidden)):
        return FetchError(f"r/{subreddit_name}: Reddit API authentication/authorization error: {error}")
    if isinstance(error, prawcore.exceptions.ServerError):
        return FetchError(f"r/{subreddit_name}: Reddit API server error (5xx): {error}")
    return FetchError(f"r/{subreddit_name}: Reddit API error: {error}")


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

    try:
        for submission in subreddit.new(limit=limit):
            if newest_fullname == last_seen_fullname:
                newest_fullname = submission.fullname

            if submission.fullname == last_seen_fullname:
                break

            if _is_removed(submission):
                continue

            posts.append(_post_to_dict(submission))
    except prawcore.exceptions.PrawcoreException as e:
        raise _wrap_prawcore_error(subreddit_name, e) from e

    return posts, newest_fullname


SOURCE_TYPE = "reddit_post"


def source_key_for(post):
    return f"{SOURCE_TYPE}:{post['id']}"


def run(subreddits, fetch_limit_per_subreddit, state_path="data/state.json",
        db_path="data/pi_agent.db", today=None, reddit_client=None):
    """Fetch each subreddit and commit its evidence and cursor together. A FetchError for one
    subreddit doesn't stop the others; the run raises at the end naming every failure."""
    reddit_client = reddit_client or build_reddit_client()
    # The cursor used to live in state.json. Read it only as a fallback for subreddits the
    # database has no cursor for yet; it is never written again.
    legacy_state = load_state(state_path)
    run_date = today or date.today()
    captured_at = datetime(run_date.year, run_date.month, run_date.day, tzinfo=timezone.utc).isoformat()

    conn = db.connect(db_path)
    db.init_db(conn)
    failures = []
    try:
        for subreddit_name in subreddits:
            last_seen = db.get_fetch_cursor(conn, SOURCE_TYPE, subreddit_name) or legacy_state.get(subreddit_name)
            try:
                posts, newest = fetch_new_posts(reddit_client, subreddit_name, last_seen, fetch_limit_per_subreddit)
            except FetchError as e:
                failures.append(str(e))
                continue

            for post in posts:
                db.insert_evidence_if_new(
                    conn,
                    source_key=source_key_for(post),
                    source_type=SOURCE_TYPE,
                    source_name=subreddit_name,
                    source_url=post["permalink"],
                    captured_at=captured_at,
                    published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
                    title=post["title"],
                    content=post["selftext"],
                    metadata={"reddit_post_id": post["id"], "score": post["score"], "num_comments": post["num_comments"]},
                )
            if newest:
                db.set_fetch_cursor(conn, SOURCE_TYPE, subreddit_name, newest)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if failures:
        raise FetchError("; ".join(failures))


if __name__ == "__main__":
    run(subreddits=[], fetch_limit_per_subreddit=100)
