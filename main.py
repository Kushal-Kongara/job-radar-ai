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

EXCLUDE = [
    "senior", "sr", "staff", "principal", "lead",
    "manager", "director", "vp", "head",
    "recruiter", "recruiting", "talent", "people",
    "intern", "internship",
    "embedded", "hardware", "firmware", "verification",
    "architect", "sre", "devops", "site reliability",
]

# ----------------- TIME HELPERS -----------------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def parse_iso_to_utc(s: str) -> datetime | None:
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

def posted_in_last_hours(iso_str: str, hours: int = 3) -> bool:
    dt = parse_iso_to_utc(iso_str)
    if not dt:
        return False
    return now_utc() - dt <= timedelta(hours=hours)

# ----------------- COMPANY LIST LOADER -----------------
# Put companies in companies.txt like:
# greenhouse:stripe
# lever:netflix
# workday:adobe
def load_companies_file(path="companies.txt"):
    gh, lev, wd = [], [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("greenhouse:"):
                    gh.append(line.split(":", 1)[1].strip())
                elif line.startswith("lever:"):
                    lev.append(line.split(":", 1)[1].strip())
                elif line.startswith("workday:"):
                    wd.append(line.split(":", 1)[1].strip())
    except FileNotFoundError:
        pass
    return gh, lev, wd

# ----------------- FILTER HELPERS -----------------
def valid_title(title: str) -> bool:
    t = (title or "").lower()
    if any(x in t for x in EXCLUDE):
        return False
    return any(x in t for x in TARGET_KEYWORDS)

def is_us_location(loc: str) -> bool:
    l = (loc or "").lower().strip()
    if not l:
        return False
    if "united states" in l or "usa" in l:
        return True
    if "us-remote" in l or "remote - us" in l or "remote (us" in l or "remote, us" in l:
        return True
    if re.search(r",\s*us\b", l):
        return True
    # Some ATS put just "Remote" (assume US remote is ok)
    if l == "remote":
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
    url = "https://gcsservices.careers.microsoft.com/search/api/v1/search"
    params = {"l": "en_us", "pg": 1, "pgSz": 50, "o": "Recent"}
    headers = {"User-Agent": "Mozilla/5.0"}

    jobs = []
    r = requests.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()

    items = data.get("operationResult", {}).get("result", {}).get("jobs", []) or []
    for j in items:
        title = j.get("title", "") or ""
        loc = j.get("primaryLocation", "") or ""
        job_id = str(j.get("jobId", "") or "")
        link = f"https://jobs.careers.microsoft.com/global/en/job/{job_id}" if job_id else ""

        posted = j.get("postedDate") or j.get("datePosted") or j.get("postingDate") or ""

        # Handle "/Date(1700000000000)/" style
        if isinstance(posted, str) and "Date(" in posted:
            m = re.search(r"Date\((\d+)\)", posted)
            if m:
                dt = datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc)
                posted = dt.isoformat()

        # Some responses have no posted date; with strict last-3-hours, we drop those.
        jobs.append({
            "source": "Microsoft",
            "company": "Microsoft",
            "title": title,
            "loc": loc,
            "url": link,
            "updated": posted,   # may be ""
            "desc": title,
        })
    return jobs

# Workday is NOT universal; tenants differ.
# This generic attempt will work for some, fail for others (and will be skipped).
def workday_jobs(tenant: str):
    urls = [
        f"https://{tenant}.wd1.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs",
        f"https://{tenant}.wd5.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs",
        f"https://{tenant}.wd3.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs",
        f"https://{tenant}.wd10.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs",
        f"https://{tenant}.wd108.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs",
    ]

    jobs = []
    last_err = None
    for url in urls:
        try:
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            r.raise_for_status()
            data = r.json()

            for j in data.get("jobPostings", []) or []:
                jobs.append({
                    "source": "Workday",
                    "company": tenant,
                    "title": j.get("title", "") or "",
                    "loc": j.get("locationsText", "") or "",
                    "url": (f"https://{tenant}.myworkdayjobs.com" + (j.get("externalPath") or "")) if j.get("externalPath") else "",
                    "updated": "",  # Workday API often doesn't give a clean ISO posted date here
                    "desc": (j.get("title", "") or "")[:400],
                })
            # if we got data, stop trying other urls
            if jobs:
                break
        except Exception as e:
            last_err = e
            continue

    if not jobs and last_err:
        print("Workday fetch failed for", tenant, ":", last_err)
    return jobs

# ----------------- AI SCORE -----------------
def ai_score(job) -> int:
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

    # Load company lists from companies.txt (so you can paste 250+ without changing code)
    gh_list, lever_list, workday_list = load_companies_file()

    # If companies.txt is missing/empty, fallback to a small default set
    if not gh_list and not lever_list and not workday_list:
        gh_list = ["stripe", "coinbase", "shopify", "airbnb", "dropbox"]
        lever_list = ["netflix", "spotify"]
        workday_list = []

    # Greenhouse
    for b in gh_list:
        try:
            all_jobs.extend(greenhouse_jobs(b))
        except Exception as e:
            print("Greenhouse error:", b, e)

    # Lever
    for h in lever_list:
        try:
            all_jobs.extend(lever_jobs(h))
        except Exception as e:
            print("Lever error:", h, e)

    # Microsoft (strict last 3 hours requires posted date)
    try:
        all_jobs.extend(microsoft_jobs())
    except Exception as e:
        print("Microsoft error:", e)

    # Workday (NOTE: strict last-3-hours won't work reliably without posted date)
    for w in workday_list:
        try:
            all_jobs.extend(workday_jobs(w))
        except Exception as e:
            print("Workday error:", w, e)

    # Filter: US + title + last 3 hours (strict)
    filtered = []
    for j in all_jobs:
        if not j.get("title"):
            continue
        if not valid_title(j["title"]):
            continue
        if not is_us_location(j.get("loc", "")):
            continue

        upd = j.get("updated", "")
        # STRICT: must have a usable timestamp
        if not posted_in_last_hours(upd, hours=3):
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
            f"⚠️ Job Radar: 0 matches last 3h ({stamp})",
            "No matching jobs found in the last 3 hours with current filters.\n\nIf you want more results, widen the time window to 12-24h OR allow sources that don't provide posted time."
        )
        return

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
            "Jobs existed in the last 3 hours, but none scored >= 60.\n\nLower threshold to 50 if you want more."
        )
        return

    send_email(
        f"🚨 Job Radar: HIGH MATCH JOBS (last 3h) ({stamp})",
        "\n\n".join(lines)
    )

if __name__ == "__main__":
    run()
