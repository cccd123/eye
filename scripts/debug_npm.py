import requests, json
from bs4 import BeautifulSoup

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Referer": "https://digitalarchive.npm.gov.tw/opendata/",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://digitalarchive.npm.gov.tw"
})

payload = {
    "RegisterType": "雕刻",
    "RegisterTypeEng": None,
    "IndexYear": None,
    "SearchContent": "",
    "WestBeginYear": 0,
    "WestEndYear": 0,
    "YearDisplay": "",
    "PageInfo": {"PageIndex": 1, "PageSize": 15, "PageCount": 0}
}

r = SESSION.post(
    "https://digitalarchive.npm.gov.tw/opendata/Pub/Search",
    json=payload, timeout=20
)

print("状态码:", r.status_code)
print("响应Content-Type:", r.headers.get("Content-Type",""))
print("响应前2000字:")
print(r.text[:2000])

# 同时试试GET方式
r2 = SESSION.get(
    "https://digitalarchive.npm.gov.tw/opendata/Pub/Detail/1828?dep=U&mode=full",
    timeout=20
)
print("\n\n=== 详情页测试（ID=1828竹根雕牧童臥牛）===")
print("状态码:", r2.status_code)
soup = BeautifulSoup(r2.text, "lxml")
print("页面标题:", soup.find("title").get_text() if soup.find("title") else "无")
print("所有段落文字:")
for p in soup.find_all("p"):
    t = p.get_text(strip=True)
    if len(t) > 10:
        print(" ", t[:100])
print("所有表格行:")
for tr in soup.select("table tr"):
    print(" ", tr.get_text(strip=True)[:80])