"""Buffer GraphQL client — ONLY Buffer API logic. No X API."""
from __future__ import annotations

import time
from typing import Any

import requests

from app import config
from app.logging_setup import get_logger

log = get_logger("x-news-bot.buffer")

# ── Low-level GraphQL helper ─────────────────────────

def _gql(query: str, variables: dict | None = None) -> dict:
    if not config.BUFFER_ACCESS_TOKEN:
        raise RuntimeError("BUFFER_ACCESS_TOKEN not set")
    headers = {
        "Authorization": f"Bearer {config.BUFFER_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"query": query}
    if variables:
        body["variables"] = variables

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(config.BUFFER_API_URL, headers=headers, json=body, timeout=20)
            # Rate-limit
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "10"))
                log.warning("Buffer rate-limited, waiting %ds", wait)
                time.sleep(min(wait, 30))
                continue
            resp.raise_for_status()
            data = resp.json()
            if "errors" in data:
                # GraphQL errors — treat as permanent unless message suggests retry
                msgs = "; ".join(e.get("message", "") for e in data["errors"])
                # Retry on transient keywords
                if any(k in msgs.lower() for k in ("timeout", "temporarily", "try again")) and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"Buffer GraphQL error: {msgs}")
            return data.get("data") or {}
        except requests.RequestException as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status and 500 <= status < 600 and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    if last_exc:
        raise last_exc
    raise RuntimeError("Buffer request failed after retries")


# ── Public API ───────────────────────────────────────

def verify_channel() -> dict:
    """
    Verify credentials and resolve channel/organization.
    Returns {organization_id, channel_id, channel_name, service}.
    Uses account → organizations → channels(input:{organizationId}) path.
    The nested account.organizations.channels is FORBIDDEN on some accounts;
    the top-level channels(input:{organizationId}) works correctly.
    """
    # Step 1: discover organizations
    q_orgs = "query { account { organizations { id name } } }"
    data = _gql(q_orgs)
    orgs = (data.get("account") or {}).get("organizations") or []
    if not orgs:
        raise RuntimeError("No organizations found for this Buffer account")

    # If BUFFER_ORGANIZATION_ID is set, prefer that org
    target_org = None
    if config.BUFFER_ORGANIZATION_ID:
        for o in orgs:
            if o.get("id") == config.BUFFER_ORGANIZATION_ID:
                target_org = o
                break
        if not target_org:
            log.warning("BUFFER_ORGANIZATION_ID %s not found, using first org", config.BUFFER_ORGANIZATION_ID)

    if not target_org:
        target_org = orgs[0]

    org_id = target_org["id"]

    # Step 2: fetch channels for that org via top-level channels(input:)
    q_channels = f'query {{ channels(input: {{organizationId: "{org_id}"}}) {{ id name service type }} }}'
    data2 = _gql(q_channels)
    channels = data2.get("channels") or []

    # Find X/Twitter channel
    target_ch = None
    if config.BUFFER_CHANNEL_ID:
        for ch in channels:
            if ch.get("id") == config.BUFFER_CHANNEL_ID:
                target_ch = ch
                break
        if not target_ch:
            raise RuntimeError(f"BUFFER_CHANNEL_ID {config.BUFFER_CHANNEL_ID!r} not found in organization {org_id}")

    if not target_ch:
        for ch in channels:
            if ch.get("service", "").lower() in ("twitter", "x"):
                target_ch = ch
                break
        if not target_ch and channels:
            # Fallback: first channel
            target_ch = channels[0]
            log.warning("No X channel found; using first channel %s (%s)", target_ch.get("id"), target_ch.get("service"))

    if not target_ch:
        raise RuntimeError("No channels found in organization")

    result = {
        "organization_id": org_id,
        "channel_id": target_ch["id"],
        "channel_name": target_ch.get("name", ""),
        "service": target_ch.get("service", ""),
    }
    log.info("Buffer verified: org=%s channel=%s (%s)", org_id, result["channel_id"], result["service"])
    return result


def get_scheduled_posts(organization_id: str, channel_id: str, limit: int = 20) -> list[dict]:
    """
    Fetch scheduled posts for a channel. Returns list of {id, text, dueAt, status}.
    Uses posts query with status=[scheduled].
    """
    q = """
    query GetScheduled($orgId: String!, $channelId: String!) {
      posts(first: 20, input: {organizationId: $orgId, filter: {status: [scheduled], channelIds: [$channelId]}}) {
        edges { node { id text dueAt status channelId } }
        pageInfo { hasNextPage endCursor }
      }
    }
    """
    # Use variables if the API supports them; fallback to inline
    # Inline the IDs for simplicity (avoids variable type mismatches)
    inline_q = f"""
    query {{
      posts(first: {limit}, input: {{organizationId: "{organization_id}", filter: {{status: [scheduled], channelIds: ["{channel_id}"]}}}}) {{
        edges {{ node {{ id text dueAt status channelId }} }}
        pageInfo {{ hasNextPage endCursor }}
      }}
    }}
    """
    data = _gql(inline_q)
    posts_data = data.get("posts") or {}
    edges = posts_data.get("edges") or []
    return [e.get("node") for e in edges if e.get("node")]


def create_scheduled_post(channel_id: str, text: str, due_at_iso: str) -> dict:
    """
    Create a scheduled post via createPost(customScheduled).
    due_at_iso must be ISO 8601 UTC (e.g. 2026-03-26T10:28:47.000Z).
    Returns {id, dueAt} on success.
    """
    # Escape text for GraphQL string literal
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    mutation = f'''
    mutation {{
      createPost(input: {{
        text: "{escaped}",
        channelId: "{channel_id}",
        schedulingType: automatic,
        mode: customScheduled,
        dueAt: "{due_at_iso}"
      }}) {{
        ... on PostActionSuccess {{
          post {{ id dueAt status }}
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    '''
    data = _gql(mutation)
    result = data.get("createPost") or {}
    # PostActionSuccess returns {post: {...}}, MutationError returns {message: ...}
    if "post" in result and result["post"]:
        post = result["post"]
        log.info("Buffer post created: id=%s dueAt=%s", post.get("id"), post.get("dueAt"))
        return post
    if "message" in result:
        raise RuntimeError(f"Buffer createPost failed: {result['message']}")
    raise RuntimeError(f"Buffer createPost unexpected response: {result}")


def delete_post(post_id: str) -> bool:
    """Delete a scheduled post. Returns True on success."""
    mutation = f'''
    mutation {{
      deletePost(input: {{ id: "{post_id}" }}) {{
        ... on DeletePostSuccess {{
          __typename
        }}
        ... on MutationError {{
          message
        }}
      }}
    }}
    '''
    try:
        data = _gql(mutation)
        result = data.get("deletePost") or {}
        if result.get("post"):
            log.info("Buffer post deleted: %s", post_id)
            return True
        if "message" in result:
            log.warning("Buffer deletePost failed %s: %s", post_id, result["message"])
            return False
        return False
    except Exception as exc:
        log.warning("Buffer deletePost error %s: %s", post_id, exc)
        return False


# CLI helper: python -m app.buffer --verify
if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        info = verify_channel()
        print(info)
        try:
            posts = get_scheduled_posts(info["organization_id"], info["channel_id"])
            print(f"Scheduled posts: {len(posts)}")
            for p in posts[:5]:
                print(f"  {p.get('id')} dueAt={p.get('dueAt')} {p.get('text','')[:60]}")
        except Exception as e:
            print(f"Could not fetch scheduled posts: {e}")
    else:
        print("Usage: python -m app.buffer --verify")
