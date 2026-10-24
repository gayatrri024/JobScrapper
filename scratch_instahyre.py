import urllib.request, re

ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36'
headers = {'User-Agent': ua, 'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'}
url = 'https://www.instahyre.com/job-362586-devops-engineer-at-razorpay-bangalore/'
req = urllib.request.Request(url, headers=headers)
with urllib.request.urlopen(req, timeout=10) as resp:
    html = resp.read().decode('utf-8', errors='ignore')

idx = html.find('posted-wrapper')
if idx != -1:
    print(html[idx-100:idx+300])
else:
    print("Not found")
