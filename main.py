import os
import re
import time
import requests
import smtplib
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from openai import OpenAI

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_TO = os.getenv("EMAIL_TO")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)

# ---- Target role filters (tune these later) ----
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

# Remove senior+ roles and non-SWE roles aggressively
EXCLUDE = [
    "senior", "sr.", "sr ", "staff", "principal", "lead", "manager", "director", "vp",
    "recruiter", "recruiting", "talent", "people",
    "intern", "internship", "embedded", "hardware", "verification", "firmware",
    "architect", "security engineer", "sre", "devops", "site reliability",
]

# ---- Sources ----
# Greenhouse board "slugs" (boards-api.greenhouse.io/v1/boards/<slug>/jobs)
GREENHOUSE = [
    "airbnb","stripe","coinbase","notion","figma","brex","databricks",
    "discord","robinhood","dropbox","scaleai","pinterest",
    "reddit","shopify","affirm","square","instacart","asana","twitch",
    "coursera","rippling","flexport","gusto","segment","intercom",
    "quora","doordash","lyft","uber","zillow","yelp","box","plaid",
    "cruise","snowflake","twilio","okta","benchling","hashicorp",
    "confluent","mongodb","unity","carta","loom","retool","webflow",
    "zapier","superhuman","calendly","grammarly","canva",
    "wise","klarna","checkout","chime","sofi","betterment","wealthfront",
    "nuro","anduril","palantir","verkada","samsara","gong",
    "clickup","monday","miro","linear","airtable","typeform","pitch",
    "perplexityai","characterai","runwayml","huggingface"
]

# Lever handles (api.lever.co/v0/postings/<handle>?mode=json)
LEVER = [
    "netflix","tesla","snap","bytedance","tiktok","spotify","atlassian",
    "cloudflare","nvidia","intel","amd","qualcomm","roblox",
    "unity","epicgames","riotgames","coinlist","chainalysis",
    "blockdaemon","alchemy","anchorage","fireblocks",
    "scaleai","huggingface","anthropic","perplexity",
    "benchling","ginkgo","synthesia","runway",
    "figma","loom","retool","ramp","brex","rippling",
    "flexport","convoy","deliverr","shippo",
    "ro","himshers","noom","headspace","calm",
    "duolingo","masterclass","udemy","coursera"
]

HEADERS = {"User-Agent": "job-radar-ai/1.0"}

def send_email(subject: str, body: str):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)

def valid_title(title: str) -> bool:
    t = (title or "").lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in TARGET_KEYWORDS)

def is_us(loc: str) -> bool:
    l = (loc or "").lower().strip()
    # Keep this permissive; we filter harder with keywords anyway
    return (
        "united states" in l
        or "usa" in l
        or "us remote" in l
        or "remote - us" in l
        or "remote (us" in l
        or "remote, us" in l
        or l.endswith(", us")
        or ", us-" in l
    )

def posted_recent_iso(updated_at: str, hours: int = 24) -> bool:
    if not updated_at:
        return False
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
        return datetime.utcnow().replace(tzinfo=dt.tzinfo) - dt < timedelta(hours=hours)
    except:
        return False

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
            "title": j.get("title"),
            "loc": (j.get("location") or {}).get("name"),
            "url": j.get("absolute_url"),
            "updated": j.get("updated_at"),  # ISO string
            "desc": (j.get("content") or "")[:2000]
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
            updated_iso = datetime.utcfromtimestamp(created_ms / 1000).isoformat() + "+00:00"
        jobs.append({
            "source": "Lever",
            "company": handle,
            "title": j.get("text"),
            "loc": (j.get("categories") or {}).get("location"),
            "url": j.get("hostedUrl"),
            "updated": updated_iso,
            "desc": (j.get("descriptionPlain") or "")[:2000]
        })
    return jobs

def ai_score(job) -> int:
    # Low-cost scoring: returns a number only
    prompt = f"""
Candidate: 3-4 years experience. Target roles: Full Stack / Frontend / Software Engineer (US only).
Reject if clearly senior+ or non-SWE.

Score fit 0-100 (integer only).

Title: {job['title']}
Location: {job['loc']}

Description:
{job['desc'][:1500]}
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0
        )
        text = (r.choices[0].message.content or "").strip()
        nums = re.findall(r"\d+", text)
        if not nums:
            return 0
        score = int(nums[0])
        return max(0, min(100, score))
    except:
        return 0

def run():
    all_jobs = []

    # Collect jobs
    for b in GREENHOUSE:
        try:
            all_jobs.extend(greenhouse_jobs(b))
        except Exception as e:
            print("Greenhouse error:", b, e)

    for h in LEVER:
        try:
            all_jobs.extend(lever_jobs(h))
        except Exception as e:
            print("Lever error:", h, e)

    # Filter to US + title match + posted last 24h
    filtered = []
    for j in all_jobs:
        if not j.get("title"):
            continue
        if not valid_title(j["title"]):
            continue
        if not is_us(j.get("loc", "")):
            continue
        if not posted_recent_iso(j.get("updated", ""), hours=24):
            continue
        filtered.append(j)

    # Rank with AI (cap to control cost)
    ranked = []
    for j in filtered[:30]:
        score = ai_score(j)
        ranked.append((score, j))
        time.sleep(0.25)

    ranked.sort(key=lambda x: x[0], reverse=True)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if not ranked:
        send_email(
            f"⚠️ Job Radar: 0 matches last 24h ({now})",
            "No matches in the last 24 hours based on current filters.\n\nNext: we can tune filters (YOE, keywords, remote/on-site) and add more verified Greenhouse/Lever sources."
        )
        return

    lines = []
    for s, j in ranked[:15]:
        # Only send “worth applying” jobs
        if s < 60:
            continue
        lines.append(
            f"{j['source']} | {j['company']}\n"
            f"{j['title']}\n"
            f"Score: {s}/100\n"
            f"Location: {j.get('loc','')}\n"
            f"Apply: {j.get('url','')}\n"
            f"{'-'*50}"
        )

    if not lines:
        send_email(
            f"⚠️ Job Radar: ran OK, nothing worth applying ({now})",
            "Jobs were found, but none crossed the quality threshold (60/100) in last 24h.\n\nNext: adjust threshold/keywords or expand company list with verified boards."
        )
        return

    send_email(
        f"🚨 Job Radar: HIGH MATCH JOBS ({now})",
        "\n\n".join(lines)
    )

if __name__ == "__main__":
    run()
