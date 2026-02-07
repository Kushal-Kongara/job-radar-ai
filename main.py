import os
import re
import time
import requests
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from openai import OpenAI

# ----------------- ENV -----------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_TO = os.getenv("EMAIL_TO")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)

HEADERS = {"User-Agent": "job-radar-ai/1.0"}

# ----------------- FILTERS -----------------
TARGET_KEYWORDS = [
    "software engineer",
    "full stack",
    "full-stack",
    "frontend",
    "front end",
    "web engineer",
    "product engineer",
    "ui engineer",
]

# Aggressive senior+ / non-role exclusions
EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead",
    "manager", "director", "vp", "head",
    "recruiter", "recruiting", "talent", "people",
    "intern", "internship",
    "embedded", "hardware", "firmware", "verification",
    "architect", "sre", "devops", "site reliability",
]

# ----------------- SOURCES -----------------
# Greenhouse board slugs (boards-api.greenhouse.io/v1/boards/<slug>/jobs)
GREENHOUSE = [
    "airbnb","stripe","coinbase","notion","figma","brex","databricks",
    "discord","robinhood","dropbox","scaleai","pinterest","reddit",
    "shopify","affirm","square","instacart","asana","twitch","coursera",
    "rippling","flexport","gusto","intercom","doordash","lyft","uber",
    "zillow","box","plaid","snowflake","twilio","okta","hashicorp",
    "mongodb","unity","carta","loom","retool","webflow","zapier",
    "calendly","grammarly","canva","chime","sofi","nuro","anduril",
    "palantir","samsara","gong","clickup","monday","miro","airtable",
    "perplexityai","runwayml","huggingface"
]

# Lever handles (api.lever.co/v0/postings/<handle>?mode=json)
LEVER = [
    "netflix","tesla","snap","spotify","cloudflare","nvidia",
    "roblox","epicgames","riotgames","scaleai","anthropic",
    "benchling","duolingo","udemy","coursera"
]

# ----------------- HELPERS -----------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_to_utc(s: str) -> datetime | None:
    """
    Returns timezone-aware UTC datetime or None.
    Handles Z / offsets / naive values.
    """
    if not s:
        return None
    try:
        s = s.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except:
        return None

def posted_in_last_24h(iso_str: str) -> bool:
    dt = parse_iso_to_utc(iso_str)
    if not dt:
        return False
    return now_utc() - dt <= timedelta(hours=24)

def valid_title(title: str) -> bool:
    t = (title or "").lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in TARGET_KEYWORDS)

def is_us_location(loc: str) -> bool:
    """
    Strict-ish US filter.
    - allows 'Remote - USA', 'US-Remote', ', US', 'United States'
    - rejects random 'us' substring matches
    """
    l = (loc or "").lower().strip()
    if not l:
        return False
    if "united states" in l or "usa" in l:
        return True
    if "us-remote" in l or "remote - us" in l or "remote (us" in l or "remote, us" in l:
        return True
    if re.search(r",\s*us\b", l):  # "Seattle, US"
        return True
    return False

# ----------------- EMAIL -----------------
def send_email(subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)

# ----------------- FETCHERS -----------------
def greenhouse_jobs(board: str):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "source": "Greenhouse",
            "company": board,
            "title": j.get("title", "") or "",
            "loc": (j.get("location") or {}).get("name", "") or "",
            "url": j.get("absolute_url", "") or "",
            "updated": j.get("updated_at", "") or "",
            "desc": (j.get("content") or "")[:1500],
        })
    return jobs

def lever_jobs(handle: str):
    url = f"https://api.lever.co/v0/postings/{handle}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()

    jobs = []
    for j in data:
        created_ms = j.get("createdAt")
        updated_iso = ""
        if created_ms:
            # timezone-aware UTC
            dt = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
            updated_iso = dt.isoformat()

        jobs.append({
            "source": "Lever",
            "company": handle,
            "title": j.get("text", "") or "",
            "loc": (j.get("categories") or {}).get("location", "") or "",
            "url": j.get("hostedUrl", "") or "",
            "updated": updated_iso,
            "desc": (j.get("descriptionPlain") or "")[:1500],
        })
    return jobs

def microsoft_jobs():
    """
    Pull newest Microsoft jobs (first page, sorted recent).
    We keep last-24h filter using fields if present; otherwise we skip date filter for MS jobs.
    """
    url = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
    params = {"l": "en_us", "pg": 1, "pgSz": 50, "o": "Recent"}
    headers = {"User-Agent": "Mozilla/5.0"}

    jobs = []
    try:
        r = requests.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        items = data.get("operationResult", {}).get("result", {}).get("jobs", []) or []
        for j in items:
            title = j.get("title", "") or ""
            loc = j.get("primaryLocation", "") or ""
            job_id = str(j.get("jobId", "") or "")
            link = f"https://jobs.careers.microsoft.com/global/en/job/{job_id}" if job_id else ""

            # Try to find a real posted/updated date field (varies)
            # If not present, leave blank (we’ll still allow it through with a relaxed rule for MS)
            posted = (
                j.get("postedDate")
                or j.get("datePosted")
                or j.get("postingDate")
                or ""
            )

            # Normalize if it’s like "/Date(1700000000000)/"
            if isinstance(posted, str) and "Date(" in posted:
                m = re.search(r"Date\((\d+)\)", posted)
                if m:
                    dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
                    posted = dt.isoformat()

            jobs.append({
                "source": "Microsoft",
                "company": "Microsoft",
                "title": title,
                "loc": loc,
                "url": link,
                "updated": posted,   # may be ""
                "desc": title,       # MS API doesn’t include full JD here
            })
    except Exception as e:
        print("Microsoft fetch error:", e)

    return jobs

# ----------------- AI SCORE -----------------
def ai_score(job) -> int:
    """
    Low-cost scoring. Returns integer 0-100.
    """
    prompt = f"""
Candidate: 3-4 years experience. Target: Full Stack / Frontend / Software Engineer roles in the US.
Reject if senior/staff/principal/lead/recruiter.

Score fit 0-100. Return ONLY a number.

Title: {job['title']}
Location: {job['loc']}
Description:
{job['desc'][:1200]}
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        txt = (r.choices[0].message.content or "").strip()
        nums = re.findall(r"\d+", txt)
        if not nums:
            return 0
        s = int(nums[0])
        return max(0, min(100, s))
    except:
        return 0

# ----------------- RUN -----------------
def run():
    all_jobs = []

    # Greenhouse
    for b in GREENHOUSE:
        try:
            all_jobs.extend(greenhouse_jobs(b))
        except Exception as e:
            print("Greenhouse error:", b, e)

    # Lever
    for h in LEVER:
        try:
            all_jobs.extend(lever_jobs(h))
        except Exception as e:
            print("Lever error:", h, e)

    # Microsoft
    all_jobs.extend(microsoft_jobs())

    # Filter (US + title + last 24h)
    filtered = []
    for j in all_jobs:
        if not j.get("title"):
            continue
        if not valid_title(j["title"]):
            continue
        if not is_us_location(j.get("loc", "")):
            continue

        # Date filtering:
        # - For Greenhouse/Lever: require last 24h
        # - For Microsoft: if date missing, allow through (because MS API doesn’t always return it in this endpoint)
        if j.get("source") != "Microsoft":
            if not posted_in_last_24h(j.get("updated", "")):
                continue
        else:
            upd = j.get("updated", "")
            if upd and not posted_in_last_24h(upd):
                continue

        filtered.append(j)

    # Rank with AI (cap to control cost)
    ranked = []
    for j in filtered[:30]:
        s = ai_score(j)
        ranked.append((s, j))
        time.sleep(0.25)

    ranked.sort(key=lambda x: x[0], reverse=True)

    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")

    if not ranked:
        send_email(
            f"⚠️ Job Radar: 0 matches last 24h ({stamp})",
            "No matching jobs found in the last 24 hours with current filters.\n\nNext: we can widen keywords slightly or lower the score threshold."
        )
        return

    # Only send “worth applying”
    lines = []
    for s, j in ranked[:15]:
        if s < 60:
            continue
        lines.append(
            f"{j['source']} | {j['company']}\n"
            f"{j['title']}\n"
            f"Score: {s}/100\n"
            f"Location: {j.get('loc','')}\n"
            f"Apply: {j.get('url','')}\n"
            f"{'-'*55}"
        )

    if not lines:
        send_email(
            f"⚠️ Job Radar: ran OK, nothing worth applying ({stamp})",
            "Jobs existed in the last 24h, but none scored >= 60.\n\nNext: lower threshold to 50 or broaden keywords."
        )
        return

    send_email(
        f"🚨 Job Radar: HIGH MATCH JOBS ({stamp})",
        "\n\n".join(lines)
    )

if __name__ == "__main__":
    run()
