import os
import re
import json
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

# --- Target profile ---
TARGET = {
    "country": "United States",
    "years": "3-4",
    "roles_keywords": [
        "software engineer", "full stack", "full-stack", "frontend", "front-end",
        "web", "ui", "product engineer"
    ],
    "exclude_keywords": [
        "staff", "principal", "sr.", "senior manager", "manager", "director", "vp",
        "embedded", "verification", "hardware", "firmware", "intern", "internship"
    ],
}

# --- Sources (Greenhouse + Lever) ---
GREENHOUSE_BOARDS = [
    # add more later
    {"company": "Stripe", "board": "stripe"},
    {"company": "Notion", "board": "notion"},
    {"company": "Coinbase", "board": "coinbase"},
    {"company": "Figma", "board": "figma"},
]

LEVER_COMPANIES = [
    # add more later
    {"company": "Pinterest", "handle": "pinterest"},
    {"company": "Rivian", "handle": "rivian"},
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

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def keyword_match(title: str) -> bool:
    t = norm(title)
    if any(k in t for k in TARGET["exclude_keywords"]):
        return False
    return any(k in t for k in TARGET["roles_keywords"])

def is_us_location(location: str) -> bool:
    loc = norm(location)
    if not loc:
        return False
    # simple heuristics
    return ("united states" in loc) or ("usa" in loc) or (", us" in loc) or ("remote - us" in loc) or ("remote (us" in loc)

def fetch_greenhouse_jobs(board: str):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "source": "Greenhouse",
            "company": board,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "updated_at": j.get("updated_at", ""),
            "description": (j.get("content") or "")[:6000],
        })
    return jobs

def fetch_lever_jobs(handle: str):
    url = f"https://api.lever.co/v0/postings/{handle}?mode=json"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data:
        jobs.append({
            "source": "Lever",
            "company": handle,
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", ""),
            "url": j.get("hostedUrl", ""),
            "updated_at": j.get("createdAt", ""),
            "description": (j.get("descriptionPlain") or "")[:6000],
        })
    return jobs

def ai_rank(job):
    prompt = f"""
You are screening jobs for a candidate with {TARGET["years"]} years experience targeting Full Stack / Software Engineer / Frontend roles in the United States.

Return JSON ONLY with:
- match: integer 0-100
- reason: 1-2 short sentences
- must_have_skills: array of up to 6 keywords

Job title: {job["title"]}
Company: {job["company"]}
Location: {job["location"]}
Job description:
{job["description"][:2500]}
"""
    res = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )
    text = res.choices[0].message.content.strip()
    try:
        return json.loads(text)
    except Exception:
        # fallback if model returns non-json
        return {"match": 0, "reason": "AI output parse failed", "must_have_skills": []}

def run():
    all_jobs = []

    for x in GREENHOUSE_BOARDS:
        try:
            jobs = fetch_greenhouse_jobs(x["board"])
            for j in jobs:
                j["company"] = x["company"]
            all_jobs.extend(jobs)
        except Exception as e:
            print("Greenhouse error", x, e)

    for x in LEVER_COMPANIES:
        try:
            jobs = fetch_lever_jobs(x["handle"])
            for j in jobs:
                j["company"] = x["company"]
            all_jobs.extend(jobs)
        except Exception as e:
            print("Lever error", x, e)

    # basic filters
    filtered = []
    for j in all_jobs:
        if not keyword_match(j["title"]):
            continue
        if not is_us_location(j["location"]):
            continue
        filtered.append(j)

    # rank with AI (limit cost)
    ranked = []
    for j in filtered[:15]:
        score = ai_rank(j)
        ranked.append((score.get("match", 0), score, j))
        time.sleep(0.2)

    ranked.sort(key=lambda x: x[0], reverse=True)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if not ranked:
        subject = f"✅ Job Radar: ran OK, 0 matches ({now})"
        body = "No matching jobs found from Greenhouse/Lever sources in this run.\n\nNext: add more company boards."
        send_email(subject, body)
        return

    lines = []
    for match, score, j in ranked[:10]:
        lines.append(
            f"Match: {match}/100\n"
            f"{j['company']} — {j['title']}\n"
            f"Location: {j['location']}\n"
            f"Apply: {j['url']}\n"
            f"Reason: {score.get('reason','')}\n"
            f"Skills: {', '.join(score.get('must_have_skills', []))}\n"
            f"{'-'*40}"
        )

    subject = f"🚨 Job Radar: top {min(10,len(ranked))} matches ({now})"
    body = "\n".join(lines)
    send_email(subject, body)

if __name__ == "__main__":
    run()
