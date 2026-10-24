import urllib.request
import json
import time

companies_to_test = [
    # 18 companies
    "devangles", "sko", "quadrang", "heaptrace", "wipro", "zensar", "alifcloud", 
    "flextrade", "shyenatech", "tuvsud", "particle41", "birlasoft", "pepperadvantage", 
    "vois", "aexonic", "wolterskluwer", "supabase", "tatacommunications",
    # Larger list
    "freshworks", "zoho", "razorpay", "postman", "meesho", "phonepe", "cred", 
    "groww", "swiggy", "zerodha", "icertis", "druva", "persistentsystems", "persistent",
    "egnyte", "synechron", "bmc", "veritas", "barclays", "nutanix", "clari", 
    "chargebee", "hasura", "middleware", "zenduty", "airmeet", "whatfix", "turing", 
    "uplers", "cloudsek", "hashicorp", "automattic", "zapier", "doist", 
    "digitalocean", "elastic", "grafanalabs", "docker", "snowflake", "nice", 
    "securonix", "amdocs", "adp", "ukg", "nvidia", "quickheal", "tesla", 
    "deloitte", "allstate", "springernature", "mindcrest", "whirlpool", "workday", 
    "atlassian", "lenovo"
]

results = {}

for comp in companies_to_test:
    # 1. Ashby
    url = f"https://api.ashbyhq.com/posting-api/job-board/{comp}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                results[comp] = ("ashby", comp)
                print(f"[FOUND] {comp} -> Ashby")
                continue
    except Exception:
        pass

    # 2. Greenhouse
    url = f"https://boards-api.greenhouse.io/v1/boards/{comp}/jobs"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                results[comp] = ("greenhouse", comp)
                print(f"[FOUND] {comp} -> Greenhouse")
                continue
    except Exception:
        pass

    # 3. Lever
    url = f"https://api.lever.co/v0/postings/{comp}?mode=json"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                results[comp] = ("lever", comp)
                print(f"[FOUND] {comp} -> Lever")
                continue
    except Exception:
        pass

print("\nSUMMARY FOUND:")
for k, v in results.items():
    print(f"  {k}: {v}")
