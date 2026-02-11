import os
import re
import time
import requests
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from openai import OpenAI

# ---------------- ENV ----------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
EMAIL_TO = os.getenv("EMAIL_TO")
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

client = OpenAI(api_key=OPENAI_API_KEY)
HEADERS = {"User-Agent": "Mozilla/5.0"}

# ---------------- ROLE FILTERS ----------------
TARGET_KEYWORDS = [
    "software engineer",
    "full stack",
    "full-stack",
    "frontend",
    "front end",
    "web engineer",
    "product engineer",
]

EXCLUDE = [
    "senior","sr","staff","principal","lead",
    "manager","director","vp","recruiter",
    "intern","internship","hardware","embedded",
    "firmware","verification","architect"
]

# ---------------- TIME FILTER (LAST 3 HOURS) ----------------
def parse_iso(s):
    try:
        if not s: return None
        if s.endswith("Z"): s=s[:-1]+"+00:00"
        dt=datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt=dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except:
        return None

def last_3_hours(iso):
    dt=parse_iso(iso)
    if not dt: return False
    return datetime.now(timezone.utc)-dt <= timedelta(hours=3)

# ---------------- EMAIL ----------------
def send_email(sub, body):
    msg=MIMEText(body)
    msg["Subject"]=sub
    msg["From"]=GMAIL_USER
    msg["To"]=EMAIL_TO

    with smtplib.SMTP("smtp.gmail.com",587) as s:
        s.starttls()
        s.login(GMAIL_USER,GMAIL_APP_PASSWORD)
        s.send_message(msg)

# ---------------- FILTERS ----------------
def valid(title):
    t=(title or "").lower()
    if any(x in t for x in EXCLUDE): return False
    return any(x in t for x in TARGET_KEYWORDS)

def us(loc):
    l=(loc or "").lower()
    return "us" in l or "united states" in l or "remote" in l

# ---------------- AI SCORE ----------------
def score(job):
    prompt=f"""
Candidate: 3-4 yrs full stack/frontend SWE US.
Score 0-100 fit. Return only number.

{job['title']}
{job['desc'][:800]}
"""
    try:
        r=client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0
        )
        txt=r.choices[0].message.content
        n=int(re.findall(r"\d+",txt)[0])
        return n
    except:
        return 50

# ---------------- LOAD 200+ COMPANIES ----------------
def load_companies():
    try:
        with open("greenhouse_companies.txt","r") as f:
            return [x.strip() for x in f if x.strip()]
    except:
        # fallback if file missing
        return ["stripe","coinbase","notion","figma","airbnb","databricks"]

# ---------------- FETCH GREENHOUSE ----------------
def greenhouse(board):
    url=f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
    jobs=[]
    try:
        r=requests.get(url,headers=HEADERS,timeout=20)
        data=r.json()

        for j in data.get("jobs",[]):
            jobs.append({
                "company":board,
                "title":j.get("title"),
                "loc":(j.get("location") or {}).get("name"),
                "url":j.get("absolute_url"),
                "updated":j.get("updated_at"),
                "desc":(j.get("content") or "")[:1200]
            })
    except:
        pass
    return jobs

# ---------------- RUN ----------------
def run():
    boards=load_companies()
    all_jobs=[]

    for b in boards:
        all_jobs+=greenhouse(b)

    filtered=[]
    for j in all_jobs:
        if not valid(j["title"]): continue
        if not us(j["loc"]): continue
        if not last_3_hours(j["updated"]): continue
        filtered.append(j)

    ranked=[]
    for j in filtered[:40]:
        s=score(j)
        ranked.append((s,j))
        time.sleep(0.2)

    ranked.sort(reverse=True)

    if not ranked:
        send_email("0 jobs last 3 hrs","No strong SWE jobs last 3 hrs")
        return

    lines=[]
    for s,j in ranked[:15]:
        if s<60: continue
        lines.append(f"""
{j['company'].upper()} — {j['title']}
Score: {s}/100
{j['loc']}
{j['url']}
------------------------------""")

    if not lines:
        send_email("Jobs but weak match","Nothing strong right now")
        return

    send_email("🚨 HOT JOBS LAST 3 HRS","\n".join(lines))

if __name__=="__main__":
    run()
