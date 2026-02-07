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

TARGET_KEYWORDS = [
    "software engineer",
    "full stack",
    "frontend",
    "front end",
    "web engineer",
    "product engineer"
]

EXCLUDE = [
    "senior", "staff", "principal", "lead",
    "manager", "director", "vp", "sr.",
    "intern", "internship", "embedded",
    "hardware", "verification"
]

def send_email(subject, body):
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.send_message(msg)

def valid_title(title):
    t = title.lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in TARGET_KEYWORDS)

def is_us(loc):
    l = (loc or "").lower()
    return (
        "united states" in l or
        "usa" in l or
        "us remote" in l or
        "remote - us" in l or
        "remote (us" in l
    )

def posted_recent(updated_at):
    if not updated_at:
        return False
    try:
        dt = datetime.fromisoformat(updated_at.replace("Z","+00:00"))
        return datetime.utcnow() - dt < timedelta(hours=24)
    except:
        return False

# -------- GREENHOUSE ----------
def greenhouse(board):
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    r = requests.get(url, timeout=30)
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "company": board,
            "title": j.get("title"),
            "loc": (j.get("location") or {}).get("name"),
            "url": j.get("absolute_url"),
            "updated": j.get("updated_at"),
            "desc": (j.get("content") or "")[:2000]
        })
    return jobs

# -------- LEVER ----------
def lever(handle):
    url = f"https://api.lever.co/v0/postings/{handle}?mode=json"
    r = requests.get(url, timeout=30)
    data = r.json()
    jobs = []
    for j in data:
        jobs.append({
            "company": handle,
            "title": j.get("text"),
            "loc": (j.get("categories") or {}).get("location"),
            "url": j.get("hostedUrl"),
            "updated": datetime.utcfromtimestamp(j.get("createdAt")/1000).isoformat(),
            "desc": (j.get("descriptionPlain") or "")[:2000]
        })
    return jobs

GREENHOUSE = ["stripe","notion","coinbase","figma"]
LEVER = ["pinterest","robinhood","rippling","scaleai"]

def ai_score(job):
    prompt = f"""
Candidate: 3-4 years full stack / frontend engineer US.

Score 0-100 fit.

Job:
{job['title']}
{job['desc'][:1500]}

Return ONLY number.
"""
    try:
        r = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        text = r.choices[0].message.content.strip()
        num = int(re.findall(r"\d+", text)[0])
        return num
    except:
        return 50

def run():
    all_jobs = []

    for g in GREENHOUSE:
        try:
            all_jobs += greenhouse(g)
        except:
            pass

    for l in LEVER:
        try:
            all_jobs += lever(l)
        except:
            pass

    filtered = []
    for j in all_jobs:
        if not valid_title(j["title"]): continue
        if not is_us(j["loc"]): continue
        if not posted_recent(j["updated"]): continue
        filtered.append(j)

    ranked = []
    for j in filtered[:20]:
        score = ai_score(j)
        ranked.append((score, j))
        time.sleep(0.3)

    ranked.sort(reverse=True)

    if not ranked:
        send_email(
            "⚠️ Job Radar: 0 matches last 24h",
            "No strong matches in last 24 hrs.\nMarket is slow today."
        )
        return

    lines = []
    for s, j in ranked[:10]:
        if s < 60: continue
        lines.append(
            f"{j['company'].upper()} — {j['title']}\n"
            f"Score: {s}/100\n"
            f"{j['loc']}\n"
            f"{j['url']}\n"
            f"{'-'*40}"
        )

    if not lines:
        send_email(
            "⚠️ Job Radar: nothing worth applying",
            "Jobs exist but none strong match today."
        )
        return

    send_email(
        "🚨 HIGH MATCH JOBS (apply fast)",
        "\n".join(lines)
    )

if __name__ == "__main__":
    run()
