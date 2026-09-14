# crawl_npm_v2.py
# 策略：先从列表页用Selenium或requests-html获取所有ID，
# 再直接访问详情页批量爬取
# 由于列表是JS渲染，改用requests直接探测ID范围

import requests
import json
import time
import os
from bs4 import BeautifulSoup

SAVE_DIR = r"D:\MuseumGlasses\knowledge_base\raw_text"
os.makedirs(SAVE_DIR, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://digitalarchive.npm.gov.tw/opendata/",
})

BASE = "https://digitalarchive.npm.gov.tw"

def fetch_detail(artifact_id):
    """访问详情页，提取完整文物信息"""
    url = f"{BASE}/opendata/Pub/Detail/{artifact_id}?dep=U&mode=full"
    try:
        r = SESSION.get(url, timeout=15)
        if r.status_code != 200:
            return None
        r.encoding = "utf-8"
        soup = BeautifulSoup(r.text, "lxml")

        # 检查是否是有效文物页（有标题）
        title_tag = soup.find("title")
        if not title_tag:
            return None
        page_title = title_tag.get_text(strip=True)
        # 如果标题是通用标题说明这个ID不存在
        if "Opendata開放資料" == page_title or "故宮典藏" == page_title:
            return None

        # 从表格提取结构化数据
        attrs = {}
        desc = ""
        for tr in soup.select("table tr"):
            tds = tr.find_all("td")
            if len(tds) >= 2:
                k = tds[0].get_text(strip=True)
                v = tds[1].get_text(strip=True)
                if k and v:
                    attrs[k] = v
                    if k in ["說明", "说明"]:
                        desc = v

        # 如果表格没有说明，从段落里找最长的那段
        if not desc:
            paras = [p.get_text(strip=True) for p in soup.find_all("p")
                     if len(p.get_text(strip=True)) > 40]
            # 排除版权声明类文字
            paras = [p for p in paras if "CC0" not in p and "申請" not in p
                     and "開放" not in p and "授權" not in p]
            if paras:
                desc = max(paras, key=len)

        # 从属性中提取分类，只要雕刻类
        category = attrs.get("分類", attrs.get("分类", ""))
        if category and "雕刻" not in category:
            return None  # 跳过非雕刻类

        name = attrs.get("品名", page_title).split("\n")[0].strip()
        # 品名可能含中英文，只取中文部分（第一行）

        return {
            "id": str(artifact_id),
            "name": name,
            "description": desc,
            "attributes": attrs,
            "url": url,
            "source": "国立故宫博物院（台北）Open Data CC BY 4.0"
        }

    except Exception as e:
        return None

def collect_ids_from_list_pages():
    """
    通过POST搜索接口获取列表HTML，
    用正则从JS变量或data属性中提取ID
    """
    import re
    ids = []
    seen = set()

    for page in range(1, 46):
        payload = {
            "RegisterType": "雕刻",
            "RegisterTypeEng": None,
            "IndexYear": None,
            "SearchContent": "",
            "WestBeginYear": 0,
            "WestEndYear": 0,
            "YearDisplay": "",
            "PageInfo": {"PageIndex": page, "PageSize": 15, "PageCount": 0}
        }
        try:
            SESSION.headers["Content-Type"] = "application/json"
            SESSION.headers["X-Requested-With"] = "XMLHttpRequest"
            r = SESSION.post(
                "https://digitalarchive.npm.gov.tw/opendata/Pub/Search",
                json=payload, timeout=20
            )
            html = r.text

            # 从HTML中用正则提取所有Detail/数字 格式的ID
            found = re.findall(r"/opendata/Pub/Detail/(\d+)", html)
            # 也找data-id、data-objectid等属性
            found += re.findall(r'data-id=["\'](\d+)["\']', html)
            found += re.findall(r'objectid["\s]*:["\s]*(\d+)', html, re.IGNORECASE)

            new_ids = [i for i in found if i not in seen]
            for i in new_ids:
                seen.add(i)
                ids.append(i)

            if new_ids:
                print(f"  第{page}页找到ID: {new_ids}")
            else:
                # 打印原始HTML片段帮助调试
                if page == 1:
                    print(f"  第1页未找到ID，HTML片段:")
                    # 找body部分
                    body_start = html.find("<body")
                    print(html[body_start:body_start+500] if body_start > 0 else html[:500])

        except Exception as e:
            print(f"  第{page}页失败: {e}")

        time.sleep(1)

    return ids

def main():
    print("=" * 50)
    print("台北故宫雕刻类文物爬取 v2")
    print("=" * 50)

    # 第一步：收集所有文物ID
    print("\n【第一步】从列表页收集文物ID...")
    ids = collect_ids_from_list_pages()
    print(f"\n共收集到 {len(ids)} 个ID")

    # 如果列表页收集失败，使用已知ID手动补充
    # 从debug结果知道：1828, 62307-62311是雕刻类
    # 用探测方式找出完整范围
    if len(ids) < 10:
        print("\n列表页ID提取不足，改用范围探测...")
        print("探测已知ID附近的范围...")

        # 已知的雕刻类ID：1828, 62307, 62308, 62309, 62310, 62311
        # 先探测62300-62400范围
        probe_ids = [str(i) for i in range(62300, 62400)]
        probe_ids += ["1828"]  # 加上已知的第一件
        ids = probe_ids

    # 第二步：逐个访问详情页
    print(f"\n【第二步】爬取 {len(ids)} 个详情页...")
    all_artifacts = []
    failed = []

    for i, artifact_id in enumerate(ids, 1):
        detail = fetch_detail(artifact_id)
        if detail and len(detail.get("description", "")) > 20:
            all_artifacts.append(detail)
            print(f"  [{i}/{len(ids)}] ✅ {detail['name'][:20]} | {len(detail['description'])}字")
        else:
            failed.append(artifact_id)

        time.sleep(0.5)

        # 每50件保存一次
        if i % 50 == 0:
            tmp = os.path.join(SAVE_DIR, "npm_artifacts_tmp.json")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(all_artifacts, f, ensure_ascii=False, indent=2)
            print(f"  💾 临时保存 {len(all_artifacts)} 件")

    # 最终保存
    save_path = os.path.join(SAVE_DIR, "npm_artifacts.json")
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(all_artifacts, f, ensure_ascii=False, indent=2)

    print(f"\n{'='*50}")
    print(f"✅ 完成！成功 {len(all_artifacts)} 件，失败/跳过 {len(failed)} 个ID")
    print(f"保存路径: {save_path}")

    print("\n--- 预览前3件 ---")
    for a in all_artifacts[:3]:
        print(f"名称：{a['name']}")
        print(f"时代：{a['attributes'].get('時代','')}")
        print(f"说明：{a['description'][:100]}...")
        print()

if __name__ == "__main__":
    main()