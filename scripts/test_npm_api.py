import requests
import json

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://digitalarchive.npm.gov.tw/'
}

# 测试1：尝试器物类列表API（陶瓷器在器物大类下）
# 台北故宫API通常是这种格式
test_urls = [
    'https://digitalarchive.npm.gov.tw/api/Artifacts/List?categoryId=6&page=1&pageSize=10',
    'https://digitalarchive.npm.gov.tw/api/artifacts?type=ceramics&page=1',
    'https://digitalarchive.npm.gov.tw/Artifacts/List?categoryId=6&pageNo=1&pageSize=10',
    'https://digitalarchive.npm.gov.tw/opendata/api/list?category=6&page=1',
]

for url in test_urls:
    try:
        r = requests.get(url, headers=headers, timeout=10)
        print(f'URL: {url}')
        print(f'状态码: {r.status_code} | 内容前200字: {r.text[:200]}')
        print('---')
    except Exception as e:
        print(f'URL: {url} | 错误: {e}')
        print('---')